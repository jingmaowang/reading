"""Extract the English articles from the bilingual New Oriental 30-article PDF.

Requires Poppler's pdftotext on PATH. This command never calls a speech API.
"""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

from docx_to_audio import prepare_output, resolve_output, safe_name, write_json


HEADING = re.compile(r"^[·•]?\s*第([一二三四五六七八九十]+)篇[：:]\s*(.+)$")
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def chinese_number(text):
    digits = dict(zip("一二三四五六七八九", range(1, 10)))
    if "十" in text:
        tens, ones = text.split("十")
        return (digits[tens] if tens else 1) * 10 + (digits[ones] if ones else 0)
    return digits[text]


def title_key(text):
    return re.sub(r"[^a-z]", "", re.sub(r"\(?excerpts\)?", "", text.lower()))


def extract_articles(raw_text):
    pages = raw_text.split("\f")
    articles = []
    current = None
    paragraph = []
    paragraphs = []
    reading = False
    need_title = False

    def flush_paragraph():
        if paragraph:
            paragraphs.append(" ".join(paragraph))
            paragraph.clear()

    def finish():
        if current is not None:
            flush_paragraph()
            current["body"] = "\n\n".join(paragraphs)
            if not current["body"] or CJK.search(current["title"] + current["body"]):
                raise ValueError(f"Article {current['number']} is empty or contains Chinese in narration text.")
            articles.append(current)
            paragraphs.clear()

    # The first two pages are the table of contents, not the articles.
    for page_number, page in enumerate(pages[2:], 3):
        lines = page.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and lines[-1].strip() == str(page_number):
            lines.pop()  # Printed footer page number.
        while lines and not lines[-1].strip():
            lines.pop()
        for line in lines:
            line = line.strip()
            heading = HEADING.fullmatch(line)
            if heading:
                finish()
                english_title = re.split(r"[\u3400-\u9fff]", heading.group(2), maxsplit=1)[0].strip()
                current = {"number": chinese_number(heading.group(1)), "title": english_title,
                           "source_pages": []}
                reading = True
                need_title = True
                continue
            if not reading:
                continue
            if re.match(r"^译文\s*[：:]", line):
                flush_paragraph()
                reading = False
                continue
            if not line:
                flush_paragraph()
                continue
            if need_title:
                if title_key(line) != title_key(current["title"]):
                    raise ValueError(f"Unexpected English title on page {page_number}; inspect extraction.")
                need_title = False
                continue
            if CJK.search(line):
                raise ValueError(f"Unexpected Chinese before translation on page {page_number}.")
            if page_number not in current["source_pages"]:
                current["source_pages"].append(page_number)
            paragraph.append(line)
    finish()
    if [a["number"] for a in articles] != list(range(1, 31)):
        raise ValueError("Expected exactly 30 consecutive articles after the table of contents.")
    return articles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", default="New_Oriental_30", help="Folder name under the project's output/ directory")
    args = parser.parse_args()
    args.output = resolve_output(args.output)
    executable = shutil.which("pdftotext")
    if not executable:
        raise ValueError("Install Poppler and add pdftotext to PATH first.")
    result = subprocess.run([executable, "-layout", "-enc", "UTF-8", str(args.pdf), "-"],
                            capture_output=True)
    if result.returncode:
        raise ValueError("PDF extraction failed: " + result.stderr.decode("utf-8", errors="replace")[-1000:])
    articles = extract_articles(result.stdout.decode("utf-8"))
    folders = prepare_output(args.output, articles)
    for article in articles:
        stem = f"{article['number']:02d}_{safe_name(article['title'])}"
        (folders["text"] / (stem + ".txt")).write_text(article["title"] + ".\n\n" + article["body"], encoding="utf-8")
        print(f"{article['number']:02d} {article['title']} ({len(article['body'])} body characters; pages {article['source_pages']})")
    write_json(folders["json"] / "extracted_articles.json", articles)
    print(f"Extracted 30 English-only articles to {args.output.resolve()}. No API requests sent.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
