"""Offline checks for bilingual PDF extraction and English narration input."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import docx_to_audio as narration
from extract_pdf_articles import extract_articles
from test_docx_to_audio import AudioResponse


def sample_pdf_text():
    numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
                "二十一", "二十二", "二十三", "二十四", "二十五", "二十六", "二十七", "二十八", "二十九", "三十"]
    pages = ["目录 第一页\n1", "目录 第二页\n2"]
    for index, numeral in enumerate(numerals):
        pages.append(f"·第{numeral}篇：Article {index + 1} 中文标题\nArticle {index + 1}\n\n"
                     f"English text line one\ncontinues here.\n\nAnother paragraph.\n译文：\n中文翻译 English words excluded.\n\n{index + 3}")
    return "\f".join(pages)


class PDFArticleTests(unittest.TestCase):
    def test_translation_and_footers_removed(self):
        articles = extract_articles(sample_pdf_text())
        self.assertEqual(len(articles), 30)
        self.assertEqual(articles[0]["body"], "English text line one continues here.\n\nAnother paragraph.")
        self.assertEqual(articles[-1]["title"], "Article 30")
        self.assertEqual(articles[-1]["source_pages"], [32])
        self.assertTrue(all("excluded" not in article["body"] for article in articles))

    def test_cross_page_sentence_preserved(self):
        text = sample_pdf_text().replace("English text line one\ncontinues here.", "English text line one\n\n3\fcontinues here.", 1)
        self.assertEqual(extract_articles(text)[0]["body"], "English text line one continues here.\n\nAnother paragraph.")

    def test_missing_article_rejected(self):
        with self.assertRaisesRegex(ValueError, "30 consecutive"):
            extract_articles(sample_pdf_text().split("·第三十篇")[0])

    def test_chinese_in_english_body_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unexpected Chinese"):
            extract_articles(sample_pdf_text().replace("continues here.", "continues 中文 here.", 1))

    def test_json_narration_only_sends_english(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(narration, "OUTPUT_ROOT", Path(directory)):
            root = Path(directory)
            articles = extract_articles(sample_pdf_text())
            source = root / "source.json"
            source.write_text(json.dumps(articles, ensure_ascii=False), encoding="utf-8")
            with patch("sys.argv", ["docx_to_audio.py", "--articles-json", str(source),
                "--expected-count", "30", "--end", "1", "--output", "out",
                "--env-file", str(root / "missing.env")]), patch.dict("os.environ", {
                    "ELEVENLABS_API_KEY": "test-key", "ELEVENLABS_VOICE_ID": "test-voice",
                }, clear=True), patch("urllib.request.urlopen", return_value=AudioResponse(b"fake-audio")) as http, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(narration.main(), 0)
                text = json.loads(http.call_args.args[0].data)["text"]
                self.assertEqual(text, "Article 1.\n\nEnglish text line one continues here.\n\nAnother paragraph.")
                self.assertTrue((root / "out" / "mp3" / "01_Article 1.mp3").exists())


if __name__ == "__main__":
    unittest.main()
