"""Split numbered DOCX articles and narrate them with an ElevenLabs voice.

Python 3.10+, standard library only. Run --dry-run before generating audio.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
DEFAULT_DOCX = Path(__file__).resolve().parent / "reference" / "New_Oriental_50.docx"
if not DEFAULT_DOCX.exists():
    DEFAULT_DOCX = Path(__file__).resolve().with_name("New_Oriental_50.docx")
DEFAULT_TITLE_PATTERN = r"^\s*(\d{1,3})[.、．:：\s]+(.+?)\s*$"
VOICE_PARAMETERS = {
    "ELEVENLABS_SPEED": ("speed", 1.0, 0.25, 4.0),
    "ELEVENLABS_STABILITY": ("stability", 0.5, 0.0, 1.0),
    "ELEVENLABS_SIMILARITY_BOOST": ("similarity_boost", 0.75, 0.0, 1.0),
    "ELEVENLABS_STYLE": ("style", 0.0, 0.0, 1.0),
}
ENV_NAMES = {"ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL_ID",
             "ELEVENLABS_USE_SPEAKER_BOOST", "ELEVENLABS_TEXT_NORMALIZATION",
             "ELEVENLABS_SEED", "ELEVENLABS_OUTPUT_FORMAT"} | VOICE_PARAMETERS.keys()
MP3_FORMATS = {"mp3_22050_32", "mp3_44100_32", "mp3_44100_64", "mp3_44100_96",
               "mp3_44100_128", "mp3_44100_192"}


def read_env_file(path: Path) -> dict[str, str]:
    """Read simple UTF-8 KEY=value settings without modifying the environment."""
    if not path.exists():
        return {}
    values = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if name not in ENV_NAMES:
            continue
        if not separator:
            raise ValueError(f"Invalid .env setting at line {line_number}.")
        value = value.strip()
        if value.startswith(("'", '"')):
            match = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
            if not match:
                raise ValueError(f"Invalid .env quoted value at line {line_number}.")
            value = match.group(2)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[name] = value
    return values


def generation_settings(settings: dict[str, str], model: str) -> tuple[dict, str]:
    def value(name: str, default: str = "") -> str:
        return os.environ.get(name, settings.get(name, default)).strip()

    voice = {}
    for name, (field, default, low, high) in VOICE_PARAMETERS.items():
        raw = value(name, str(default) if model == "eleven_multilingual_v2" else "")
        if not raw:
            continue
        try:
            number = float(raw)
        except ValueError:
            raise ValueError(f"{name} must be a number between {low} and {high}.") from None
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f"{name} must be between {low} and {high}.")
        voice[field] = number
    boost = value("ELEVENLABS_USE_SPEAKER_BOOST", "true" if model == "eleven_multilingual_v2" else "")
    if boost:
        if boost.lower() not in {"true", "false"}:
            raise ValueError("ELEVENLABS_USE_SPEAKER_BOOST must be true or false.")
        voice["use_speaker_boost"] = boost.lower() == "true"
    if model.startswith("eleven_v4") and any(k in voice for k in ("speed", "style", "use_speaker_boost")):
        raise ValueError("Eleven v4 does not support these voice settings; remove SPEED, STYLE and USE_SPEAKER_BOOST from its configuration.")
    normalization = value("ELEVENLABS_TEXT_NORMALIZATION", "auto")
    if normalization not in {"auto", "on", "off"}:
        raise ValueError("ELEVENLABS_TEXT_NORMALIZATION must be auto, on or off.")
    payload = {"apply_text_normalization": normalization}
    if voice:
        payload["voice_settings"] = voice
    seed = value("ELEVENLABS_SEED")
    if seed:
        try:
            seed_number = int(seed)
        except ValueError:
            raise ValueError("ELEVENLABS_SEED must be an integer between 0 and 4294967295.") from None
        if not 0 <= seed_number <= 4294967295:
            raise ValueError("ELEVENLABS_SEED must be between 0 and 4294967295.")
        payload["seed"] = seed_number
    output_format = value("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
    if output_format not in MP3_FORMATS:
        raise ValueError("ELEVENLABS_OUTPUT_FORMAT must be a supported MP3 format.")
    return payload, output_format


def read_articles(path: Path, pattern: str) -> list[dict]:
    title_re = re.compile(pattern)
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    articles = []
    current = None
    for paragraph in root.findall(".//w:body//w:p", NS):
        # Retain explicit line breaks and tabs, including paragraphs in tables.
        text = "".join(
            (node.text or "") if node.tag == f"{{{NS['w']}}}t" else
            "\n" if node.tag in {f"{{{NS['w']}}}br", f"{{{NS['w']}}}cr"} else
            "\t" if node.tag == f"{{{NS['w']}}}tab" else ""
            for node in paragraph.iter()
        ).strip()
        if not text:
            continue
        match = title_re.fullmatch(text)
        if match:
            current = {"number": int(match.group(1)), "title": match.group(2).strip(), "paragraphs": []}
            articles.append(current)
        elif current is not None:
            current["paragraphs"].append(text)
        else:
            raise ValueError("Found text before the first numbered title; adjust --title-pattern.")
    if not articles:
        raise ValueError("No article titles found; adjust --title-pattern.")
    numbers = [article["number"] for article in articles]
    if numbers != list(range(1, len(articles) + 1)):
        raise ValueError("Article numbers must be unique and consecutive starting at 1.")
    for article in articles:
        if not article["paragraphs"]:
            raise ValueError(f"Article {article['number']} has no body text.")
        article["body"] = "\n\n".join(article.pop("paragraphs"))
    return articles


def split_text(text: str, limit: int) -> list[str]:
    chunks = []
    while len(text) > limit:
        window = text[:limit]
        boundaries = list(re.finditer(r"\n\n|(?<=[.!?。！？])\s+", window))
        cut = boundaries[-1].end() if boundaries else window.rfind(" ") + 1
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


def read_articles_json(path: Path) -> list[dict]:
    articles = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(articles, list) or not articles:
        raise ValueError("Articles JSON must be a nonempty list.")
    for number, article in enumerate(articles, 1):
        if not isinstance(article, dict) or article.get("number") != number:
            raise ValueError("JSON article numbers must be consecutive starting at 1.")
        if any(not isinstance(article.get(key), str) or not article[key].strip() for key in ("title", "body")):
            raise ValueError(f"JSON article {number} must have a nonempty title and body.")
        if re.search(r"[\u3400-\u9fff\uf900-\ufaff]", article["title"] + article["body"]):
            raise ValueError(f"JSON article {number} contains Chinese; English-only narration required.")
    return articles


def safe_name(title: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)[:100].rstrip(" .") or "article"


def write_json(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def resolve_output(name: str) -> Path:
    """Treat --output as one folder name beneath the project's output directory."""
    if not name or name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((" ", ".")):
        raise ValueError("--output must be a folder name, such as New_Oriental_30; paths are not accepted.")
    return OUTPUT_ROOT / name


def prepare_output(root: Path, articles: list[dict]) -> dict[str, Path]:
    folders = {kind: root / kind for kind in ("text", "json", "mp3")}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    # Relocate files from earlier script versions so completed audio can resume.
    moves = [(root / "articles.json", folders["json"] / "articles.json")]
    for article in articles:
        stem = f"{article['number']:02d}_{safe_name(article['title'])}"
        for suffix, kind in ((".txt", "text"), (".mp3", "mp3"), (".mp3.json", "json")):
            moves.append((root / (stem + suffix), folders[kind] / (stem + suffix)))
    for source, target in moves:
        if source.exists() and target.exists():
            raise ValueError(f"Both legacy and new output files exist: {source} and {target}. Resolve the duplicate before running again.")
    for source, target in moves:
        if source.exists():
            source.rename(target)
    return folders


def synthesize(key: str, voice_id: str, payload: dict, target: Path, retries: int,
               output_format: str = "mp3_44100_128") -> None:
    url = "https://api.elevenlabs.io/v1/text-to-speech/" + urllib.parse.quote(voice_id, safe="")
    url += "?" + urllib.parse.urlencode({"output_format": output_format})
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={
        "xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg",
    }, method="POST")
    temp = target.with_suffix(".mp3.tmp")
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=240) as response, temp.open("wb") as output:
                if "audio/" not in response.headers.get("Content-Type", ""):
                    raise RuntimeError("API returned a non-audio response.")
                shutil.copyfileobj(response, output)
            if not temp.stat().st_size:
                raise RuntimeError("API returned empty audio.")
            temp.replace(target)
            return
        except urllib.error.HTTPError as exc:
            temp.unlink(missing_ok=True)
            detail = exc.read().decode("utf-8", errors="replace")[:800].replace(key, "[REDACTED]")
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise RuntimeError(f"ElevenLabs HTTP {exc.code}: {detail}") from None
            if attempt == retries:
                raise RuntimeError(f"ElevenLabs HTTP {exc.code}: {detail}") from None
            retry_after = exc.headers.get("Retry-After", "")
            delay = min(60, float(retry_after)) if retry_after.isdigit() else min(60, 2 ** (attempt + 1))
            print(f"  HTTP {exc.code}; retry in {delay}s", flush=True)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            temp.unlink(missing_ok=True)
            # A lost response may already have consumed credits; do not resubmit automatically.
            raise RuntimeError("Network interrupted. Run again to resume; this request may have consumed credits.") from None


def merge_audio(parts: list[Path], destination: Path, ffmpeg: str | None) -> None:
    temp = destination.with_suffix(".tmp.mp3")
    if len(parts) == 1:
        shutil.copyfile(parts[0], temp)
    else:
        listing = parts[0].parent / "concat.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                                 "-safe", "0", "-i", str(listing), "-c", "copy", str(temp)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            temp.unlink(missing_ok=True)
            raise RuntimeError("FFmpeg merge failed: " + result.stderr[-1200:])
    temp.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", nargs="?", type=Path, default=DEFAULT_DOCX)
    parser.add_argument("--articles-json", type=Path, help="Read extracted English articles from JSON instead of DOCX")
    parser.add_argument("--output", default="audio", help="Folder name under the project's output/ directory")
    parser.add_argument("--dry-run", action="store_true", help="Export text and manifest without calling the API")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().with_name(".env"),
                        help="Credentials file (default: .env next to this script; environment variables take priority)")
    parser.add_argument("--expected-count", type=int, default=50)
    parser.add_argument("--title-pattern", default=DEFAULT_TITLE_PATTERN, help="Regex with number and title capture groups")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    parser.add_argument("--model", help="Model ID; overrides ELEVENLABS_MODEL_ID (default: eleven_multilingual_v2)")
    parser.add_argument("--max-chars", type=int, default=4500)
    parser.add_argument("--skip-title", action="store_true")
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()
    args.output = resolve_output(args.output)
    settings = read_env_file(args.env_file)
    args.model = (args.model if args.model is not None else os.environ.get(
        "ELEVENLABS_MODEL_ID", settings.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
    )).strip()
    if not args.model:
        parser.error("Model ID must not be empty")
    generation, output_format = generation_settings(settings, args.model)
    if not 100 <= args.max_chars <= 10000 or args.retries < 0:
        parser.error("--max-chars must be 100..10000; --retries must be nonnegative")
    articles = read_articles_json(args.articles_json) if args.articles_json else read_articles(args.docx, args.title_pattern)
    if len(articles) != args.expected_count:
        raise ValueError(f"Expected {args.expected_count} articles, found {len(articles)}. No API requests sent.")
    end = args.end if args.end is not None else len(articles)
    if not 1 <= args.start <= end <= len(articles):
        parser.error("Invalid --start / --end range")
    selected = []
    folders = prepare_output(args.output, articles)
    for article in articles:
        stem = f"{article['number']:02d}_{safe_name(article['title'])}"
        text = article["body"] if args.skip_title else article["title"] + ".\n\n" + article["body"]
        (folders["text"] / (stem + ".txt")).write_text(text, encoding="utf-8")
        article.update(filename=stem + ".mp3", characters=len(text), chunks=len(split_text(text, args.max_chars)))
        if args.start <= article["number"] <= end:
            selected.append((article, text))
    write_json(folders["json"] / "articles.json", articles)
    print(f"Model: {args.model}")
    print(f"Audio settings: {json.dumps(generation, ensure_ascii=False)}; output: {output_format}")
    print(f"Found {len(articles)} articles; selected {len(selected)}; {sum(len(t) for _, t in selected)} characters.")
    for article, _ in selected:
        print(f"{article['number']:02d}  {article['title']}  ({article['characters']} chars, {article['chunks']} chunks)")
    if args.dry_run:
        print(f"Preview exported to {args.output.resolve()}. No API requests sent.")
        return 0
    key = os.environ.get("ELEVENLABS_API_KEY", settings.get("ELEVENLABS_API_KEY", "")).strip()
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", settings.get("ELEVENLABS_VOICE_ID", "")).strip()
    if not key or not voice_id:
        raise ValueError("Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in .env or environment variables first.")
    ffmpeg = shutil.which("ffmpeg")
    if any(a["chunks"] > 1 for a, _ in selected) and not ffmpeg:
        raise ValueError("Long articles need FFmpeg on PATH to merge chunks. No API requests sent.")
    for article, text in selected:
        destination = folders["mp3"] / article["filename"]
        config = {"text": text, "voice_id": voice_id, "model_id": args.model,
                  "max_chars": args.max_chars, "output_format": output_format, **generation}
        signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()
        cache = args.output / ".cache" / signature
        cache.mkdir(parents=True, exist_ok=True)
        stamp = folders["json"] / (article["filename"] + ".json")
        if destination.exists() and destination.stat().st_size and stamp.exists():
            if json.loads(stamp.read_text(encoding="utf-8")).get("signature") == signature:
                print(f"Skip completed: {destination.name}", flush=True)
                continue
        if destination.exists():
            raise ValueError(f"Existing audio has different or missing settings: {destination}. Use a different --output directory.")
        chunks = split_text(text, args.max_chars)
        parts = []
        for index, chunk in enumerate(chunks):
            part = cache / f"part_{index + 1:04d}.mp3"
            if not part.exists() or not part.stat().st_size:
                print(f"Generate {article['number']:02d}: chunk {index + 1}/{len(chunks)}", flush=True)
                payload = {"text": chunk, "model_id": args.model, **generation}
                if args.model != "eleven_v3":
                    if index:
                        payload["previous_text"] = chunks[index - 1][-1000:]
                    if index + 1 < len(chunks):
                        payload["next_text"] = chunks[index + 1][:1000]
                synthesize(key, voice_id, payload, part, args.retries, output_format)
            parts.append(part)
        merge_audio(parts, destination, ffmpeg)
        write_json(stamp, {"signature": signature, "model_id": args.model, "voice_id": voice_id,
                          "output_format": output_format, **generation})
        print(f"Saved: {destination}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile, ET.ParseError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Stopped. Run the same command to resume.", file=sys.stderr)
        raise SystemExit(130)
