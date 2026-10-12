"""Offline tests: no API key, provider requests or company artwork modified."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

SPEC = importlib.util.spec_from_file_location("wordmarks", Path(__file__).resolve().parents[1] / "scripts/fetch_company_wordmarks.py")
wordmarks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wordmarks)


def png(transparent=True):
    image = Image.new("RGBA", (100, 30), (0, 0, 0, 0) if transparent else (255, 255, 255, 255))
    for x in range(10, 90):
        for y in range(10, 20):
            image.putpixel((x, y), (10, 30, 50, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class WordmarkTests(unittest.TestCase):
    def test_transparency_is_checked_on_actual_pixels(self):
        self.assertGreater(wordmarks.inspect_png(png())["transparentPercent"], 50)
        with self.assertRaises(ValueError):
            wordmarks.inspect_png(png(False))
        with self.assertRaises(Exception):
            wordmarks.inspect_png(b"invalid")

    def test_download_url_preserves_signature_without_secret(self):
        value = wordmarks.download_url("https://img.logo.dev/assets/signed-token?existing=1")
        self.assertIn("format=png", value)
        self.assertIn("theme=dark", value)
        self.assertIn("existing=1", value)
        with self.assertRaises(ValueError):
            wordmarks.download_url("https://example.com/assets/token")

    def test_full_wordmark_is_preferred_and_stored_atomically(self):
        payload = {"data": {"status": "ready", "brandmark": {"url": "https://img.logo.dev/assets/example", "etag": "test", "source": {"has_transparency": True}}}}
        calls = []
        def fetch(url, key=None):
            calls.append((url, key))
            return json.dumps(payload).encode() if key else png()
        with tempfile.TemporaryDirectory() as directory, patch.object(wordmarks, "ROOT", Path(directory)):
            result = wordmarks.fetch_artwork({"slug": "test", "domain": "test.com"}, {}, "sk_fixture", fetch)
            self.assertEqual(result["kind"], "brandmark")
            self.assertTrue((Path(directory) / result["path"]).exists())
            self.assertNotIn("signed-token", json.dumps(result))
            self.assertNotIn("sk_fixture", json.dumps(result))
            self.assertIsNone(calls[1][1], "Secret key passed to download host")
            prior = result.copy()
            second = wordmarks.fetch_artwork({"slug": "test", "domain": "test.com"}, prior, "sk_fixture", fetch)
            self.assertEqual(second, prior)
            self.assertEqual(len(calls), 3, "Unchanged image downloaded again")

    def test_opaque_artwork_does_not_overwrite_good_data(self):
        payload = {"data": {"status": "ready", "brandmark": {"url": "https://img.logo.dev/assets/opaque", "source": {"has_transparency": True}}}}
        def fetch(url, key=None):
            return json.dumps(payload).encode() if key else png(False)
        with tempfile.TemporaryDirectory() as directory, patch.object(wordmarks, "ROOT", Path(directory)):
            old = Path(directory) / "images/company-logos/test-hero.png"
            old.parent.mkdir(parents=True)
            old.write_bytes(png())
            self.assertIsNone(wordmarks.fetch_artwork({"slug": "test", "domain": "test.com"}, {}, "sk_fixture", fetch))
            self.assertEqual(old.read_bytes(), png())


if __name__ == "__main__":
    unittest.main()
