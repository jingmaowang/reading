"""Offline checks: python -m unittest -v test_docx_to_audio."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import docx_to_audio as app


class AudioResponse(io.BytesIO):
    headers = {"Content-Type": "audio/mpeg"}


class NarrationTests(unittest.TestCase):
    def test_voice_settings_reach_api_and_change_cache_signature(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "OUTPUT_ROOT", Path(directory)):
            env_file = Path(directory) / ".env"
            text = ("ELEVENLABS_API_KEY=test-key\nELEVENLABS_VOICE_ID=test-voice\n"
                    "ELEVENLABS_MODEL_ID=eleven_multilingual_v2\nELEVENLABS_SPEED=0.9\n"
                    "ELEVENLABS_STABILITY=0.7\nELEVENLABS_SIMILARITY_BOOST=0.8\n"
                    "ELEVENLABS_STYLE=0.2\nELEVENLABS_USE_SPEAKER_BOOST=false\n"
                    "ELEVENLABS_TEXT_NORMALIZATION=on\nELEVENLABS_SEED=42\n"
                    "ELEVENLABS_OUTPUT_FORMAT=mp3_44100_192\n")
            env_file.write_text(text, encoding="utf-8")
            argv = ["docx_to_audio.py", str(Path(__file__).with_name("New_Oriental_50.docx")),
                    "--output", "audio", "--end", "1", "--env-file", str(env_file)]
            with patch("sys.argv", argv), patch.dict("os.environ", {}, clear=True), patch(
                "urllib.request.urlopen", side_effect=lambda *a, **kw: AudioResponse(b"fake-audio")
            ) as http, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
                request = http.call_args.args[0]
                payload = json.loads(request.data)
                self.assertEqual(payload["voice_settings"], {"speed": 0.9, "stability": 0.7,
                    "similarity_boost": 0.8, "style": 0.2, "use_speaker_boost": False})
                self.assertEqual(payload["apply_text_normalization"], "on")
                self.assertEqual(payload["seed"], 42)
                self.assertIn("output_format=mp3_44100_192", request.full_url)
                self.assertEqual(app.main(), 0)
                self.assertEqual(http.call_count, 1)
                with patch.dict("os.environ", {"ELEVENLABS_SPEED": "1.1"}):
                    with self.assertRaisesRegex(ValueError, "Existing audio"):
                        app.main()
                self.assertEqual(http.call_count, 1)

    def test_invalid_voice_settings_are_rejected(self):
        cases = {"ELEVENLABS_SPEED": ["0", "5", "nan", "inf", "fast"],
                 "ELEVENLABS_STABILITY": ["-0.1", "1.1"],
                 "ELEVENLABS_SIMILARITY_BOOST": ["2"], "ELEVENLABS_STYLE": ["2"],
                 "ELEVENLABS_USE_SPEAKER_BOOST": ["maybe"],
                 "ELEVENLABS_TEXT_NORMALIZATION": ["invalid"],
                 "ELEVENLABS_SEED": ["-1", "4294967296", "1.2"],
                 "ELEVENLABS_OUTPUT_FORMAT": ["wav_44100"]}
        with patch.dict("os.environ", {}, clear=True):
            for name, values in cases.items():
                for value in values:
                    with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                        app.generation_settings({name: value}, "eleven_multilingual_v2")
            with self.assertRaisesRegex(ValueError, "v4"):
                app.generation_settings({"ELEVENLABS_SPEED": "0.9"}, "eleven_v4")

    def test_env_file_format_and_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text('# 注释\nexport ELEVENLABS_API_KEY="file-key" # 注释\n'
                                "ELEVENLABS_VOICE_ID='file-voice'\nELEVENLABS_MODEL_ID=eleven_v3\nIGNORED=value\n", encoding="utf-8-sig")
            self.assertEqual(app.read_env_file(env_file), {
                "ELEVENLABS_API_KEY": "file-key", "ELEVENLABS_VOICE_ID": "file-voice",
                "ELEVENLABS_MODEL_ID": "eleven_v3",
            })
            self.assertEqual(app.read_env_file(Path(directory) / "missing.env"), {})

    def test_credentials_from_env_file_and_environment_priority(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "OUTPUT_ROOT", Path(directory)):
            env_file = Path(directory) / ".env"
            env_file.write_text("ELEVENLABS_API_KEY=file-key\nELEVENLABS_VOICE_ID=file-voice\n", encoding="utf-8")
            argv = ["docx_to_audio.py", str(Path(__file__).with_name("New_Oriental_50.docx")),
                    "--output", "audio", "--end", "1", "--env-file", str(env_file)]
            with patch("sys.argv", argv), patch.dict("os.environ", {}, clear=True), patch(
                "urllib.request.urlopen", side_effect=lambda *a, **kw: AudioResponse(b"fake-audio")
            ) as http, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
                request = http.call_args.args[0]
                self.assertEqual(request.get_header("Xi-api-key"), "file-key")
                self.assertIn("/file-voice?", request.full_url)
                with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "environment-key"}):
                    # Cached audio skips the request; remove final output to verify the key again.
                    for file in (Path(directory) / "audio").rglob("*.mp3"):
                        file.unlink()
                    self.assertEqual(app.main(), 0)
                    self.assertEqual(http.call_args.args[0].get_header("Xi-api-key"), "environment-key")

    def test_real_document_boundaries(self):
        articles = app.read_articles(Path(__file__).with_name("New_Oriental_50.docx"), app.DEFAULT_TITLE_PATTERN)
        self.assertEqual(len(articles), 50)
        self.assertEqual(articles[0]["title"], "The Language of Music")
        self.assertEqual(articles[-1]["title"], "Cells and Temperature")
        self.assertTrue(all(a["body"] for a in articles))
        self.assertTrue(articles[0]["body"].startswith("A painter"))
        self.assertNotIn("Schooling and Education", articles[0]["body"])

    def test_model_configuration_priority(self):
        cases = [
            (None, {}, [], "eleven_multilingual_v2"),
            ("eleven_v3", {}, [], "eleven_v3"),
            ("eleven_v3", {"ELEVENLABS_MODEL_ID": "eleven_flash_v2_5"}, [], "eleven_flash_v2_5"),
            ("eleven_v3", {"ELEVENLABS_MODEL_ID": "eleven_flash_v2_5"}, ["--model", "eleven_multilingual_v2"], "eleven_multilingual_v2"),
        ]
        for file_model, environment, flags, expected in cases:
            with self.subTest(expected=expected, flags=flags), tempfile.TemporaryDirectory() as directory, patch.object(app, "OUTPUT_ROOT", Path(directory)):
                env_file = Path(directory) / ".env"
                content = "ELEVENLABS_API_KEY=test-key\nELEVENLABS_VOICE_ID=test-voice\n"
                if file_model:
                    content += f"ELEVENLABS_MODEL_ID={file_model}\n"
                env_file.write_text(content, encoding="utf-8")
                argv = ["docx_to_audio.py", str(Path(__file__).with_name("New_Oriental_50.docx")),
                        "--output", "audio", "--end", "1", "--env-file", str(env_file)] + flags
                with patch("sys.argv", argv), patch.dict("os.environ", environment, clear=True), patch(
                    "urllib.request.urlopen", side_effect=lambda *a, **kw: AudioResponse(b"fake-audio")
                ) as http, contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(app.main(), 0)
                    self.assertEqual(json.loads(http.call_args.args[0].data)["model_id"], expected)
                    self.assertIn(f"Model: {expected}", output.getvalue())

    def test_split_preserves_all_characters(self):
        for text in ["Sentence one. Sentence two!\n\n" * 300, "汉字" * 900, "x" * 900]:
            chunks = app.split_text(text, 100)
            self.assertEqual("".join(chunks), text)
            self.assertTrue(all(0 < len(c) <= 100 for c in chunks))

    def test_request_and_resume(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "OUTPUT_ROOT", Path(directory)):
            argv = ["docx_to_audio.py", str(Path(__file__).with_name("New_Oriental_50.docx")),
                    "--output", "result", "--end", "1", "--env-file", str(Path(directory) / "missing.env")]
            with patch("sys.argv", argv), patch.dict("os.environ", {
                "ELEVENLABS_API_KEY": "test-secret", "ELEVENLABS_VOICE_ID": "my-voice",
            }), patch("urllib.request.urlopen", side_effect=lambda *a, **kw: AudioResponse(b"fake-audio")) as http, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
                request = http.call_args.args[0]
                self.assertIn("/my-voice?output_format=mp3_44100_128", request.full_url)
                payload = json.loads(request.data)
                self.assertEqual(payload["model_id"], "eleven_multilingual_v2")
                self.assertTrue(payload["text"].startswith("The Language of Music."))
                self.assertEqual(app.main(), 0)
                self.assertEqual(http.call_count, 1)
                output = Path(directory) / "result" / "mp3" / "01_The Language of Music.mp3"
                self.assertEqual(output.read_bytes(), b"fake-audio")
                self.assertNotIn("test-secret", (Path(directory) / "result" / "json" / (output.name + ".json")).read_text(encoding="utf-8"))
                with patch("sys.argv", argv + ["--skip-title"]):
                    with self.assertRaisesRegex(ValueError, "Existing audio"):
                        app.main()
                self.assertEqual(http.call_count, 1)

    def test_dry_run_does_not_call_api(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "OUTPUT_ROOT", Path(directory)), patch("sys.argv", [
            "docx_to_audio.py", str(Path(__file__).with_name("New_Oriental_50.docx")),
            "--output", "result", "--dry-run",
        ]), patch("urllib.request.urlopen") as http, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(), 0)
            http.assert_not_called()
            self.assertEqual(len(list((Path(directory) / "result" / "text").glob("*.txt"))), 50)
            self.assertTrue((Path(directory) / "result" / "json" / "articles.json").exists())
            self.assertTrue((Path(directory) / "result" / "mp3").is_dir())
            self.assertFalse(list(Path(directory).glob("*.txt")))
            self.assertFalse(list(Path(directory).glob("*.json")))

    def test_legacy_output_migrates_and_resumes_without_api(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "OUTPUT_ROOT", Path(directory)):
            root = Path(directory) / "result"
            argv = ["docx_to_audio.py", str(Path(__file__).with_name("New_Oriental_50.docx")),
                    "--output", "result", "--end", "1", "--env-file", str(root / "missing.env")]
            with patch("sys.argv", argv), patch.dict("os.environ", {
                "ELEVENLABS_API_KEY": "test-key", "ELEVENLABS_VOICE_ID": "test-voice",
            }, clear=True), patch("urllib.request.urlopen", side_effect=lambda *a, **kw: AudioResponse(b"fake-audio")) as http, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)
                # Recreate the old layout from a completed run.
                for folder in ("text", "json", "mp3"):
                    for file in (root / folder).iterdir():
                        file.rename(root / file.name)
                self.assertEqual(app.main(), 0)
                self.assertEqual(http.call_count, 1)
                self.assertEqual((root / "mp3" / "01_The Language of Music.mp3").read_bytes(), b"fake-audio")
                self.assertFalse(any(root.glob("*.mp3")))
                self.assertFalse(any(root.glob("*.txt")))
                self.assertFalse(any(root.glob("*.json")))

    def test_output_is_always_under_project_output(self):
        self.assertEqual(app.resolve_output("New_Oriental_30"), app.OUTPUT_ROOT / "New_Oriental_30")
        for invalid in ("", ".", "..", "../outside", "folder/subfolder", r"folder\subfolder", r"D:\outside", "/outside"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "folder name"):
                app.resolve_output(invalid)

    def test_migration_refuses_to_overwrite_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "articles.json").write_text("old", encoding="utf-8")
            (root / "json").mkdir()
            (root / "json" / "articles.json").write_text("new", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                app.prepare_output(root, [])
            self.assertEqual((root / "articles.json").read_text(encoding="utf-8"), "old")
            self.assertEqual((root / "json" / "articles.json").read_text(encoding="utf-8"), "new")

    def test_authentication_error_not_retried_and_secret_redacted(self):
        error = urllib.error.HTTPError("https://example.invalid", 401, "Unauthorized", {}, io.BytesIO(b"secret-key"))
        with tempfile.TemporaryDirectory() as directory, patch("urllib.request.urlopen", side_effect=error) as http:
            with self.assertRaisesRegex(RuntimeError, r"HTTP 401: \[REDACTED\]"):
                app.synthesize("secret-key", "voice", {"text": "hello"}, Path(directory) / "test.mp3", 3)
            self.assertEqual(http.call_count, 1)

    def test_rate_limit_retried(self):
        error = urllib.error.HTTPError("https://example.invalid", 429, "Rate limit", {"Retry-After": "1"}, io.BytesIO(b"rate limit"))
        with tempfile.TemporaryDirectory() as directory, patch("urllib.request.urlopen", side_effect=[error, AudioResponse(b"audio")]) as http, patch("time.sleep") as sleep, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "test.mp3"
            app.synthesize("secret", "voice", {"text": "hello"}, output, 1)
            self.assertEqual(http.call_count, 2)
            sleep.assert_called_once_with(1.0)
            self.assertEqual(output.read_bytes(), b"audio")


if __name__ == "__main__":
    unittest.main()
