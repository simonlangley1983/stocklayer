"""Offline tests: no API key, provider requests or company artwork modified."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
import urllib.error
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
    def test_partial_failure_preserves_manifest_and_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / 'logos/hero-manifest.json'
            manifest_path.parent.mkdir()
            old = {"path": "images/company-logos/second-hero.png", "sha256": "old"}
            manifest_path.write_text(json.dumps({"logos": {"second": old}, "issues": {}}))
            (root / 'ftse100.json').write_text(json.dumps([
                {"slug": "first", "domain": "first.test"}, {"slug": "second", "domain": "second.test"}
            ]))
            with patch.object(wordmarks, 'ROOT', root), patch.object(wordmarks, 'MANIFEST', manifest_path), \
                 patch.dict(wordmarks.os.environ, {"LOGO_DEV_SECRET_KEY": "sk_fixture"}), \
                 patch.object(sys, 'argv', ['wordmarks']), patch.object(wordmarks.time, 'sleep'), \
                 patch.object(wordmarks, 'fetch_artwork', side_effect=[{"kind": "brandmark", "path": "first.png"}, wordmarks.ProviderError(500)]):
                self.assertEqual(wordmarks.main(), 1)
            saved = json.loads(manifest_path.read_text())
            self.assertEqual(saved['logos']['second'], old)
            self.assertIn('first', saved['logos'])
            self.assertEqual(saved['lastRun']['retained'], 1)
            self.assertNotIn('sk_fixture', manifest_path.read_text())

    def test_rate_limit_retry_does_not_repeat_forever(self):
        error = urllib.error.HTTPError('https://api.logo.dev/v2/brands', 429, 'Rate limited', {'Retry-After': '2'}, None)
        with patch.object(wordmarks.urllib.request, 'build_opener') as build, patch.object(wordmarks.time, 'sleep') as sleep:
            build.return_value.open.side_effect = error
            with self.assertRaises(wordmarks.ProviderError) as raised:
                wordmarks.request('https://api.logo.dev/v2/brands', 'sk_fixture')
            self.assertEqual(raised.exception.status, 429)
            self.assertEqual(build.return_value.open.call_count, 4)
            self.assertEqual(sleep.call_count, 3)
            sleep.assert_called_with(2)

    def test_credentials_are_never_sent_to_another_host(self):
        with self.assertRaises(ValueError), patch.object(wordmarks.urllib.request, 'build_opener') as build:
            wordmarks.request('https://example.com/image.png', 'sk_fixture')
        build.assert_not_called()

    def test_rate_limit_reset_header_is_respected(self):
        error = urllib.error.HTTPError('https://api.logo.dev/v2/brands', 429, 'Rate limited', {'RateLimit-Remaining': '0', 'RateLimit-Reset': '45'}, None)
        with patch.object(wordmarks.urllib.request, 'build_opener') as build, patch.object(wordmarks.time, 'sleep') as sleep:
            build.return_value.open.side_effect = error
            with self.assertRaises(wordmarks.ProviderError):
                wordmarks.request('https://api.logo.dev/v2/brands', 'sk_fixture')
            sleep.assert_called_with(45)

    def test_null_metadata_is_inspected_not_assumed_transparent(self):
        payload = {"data": {"brandmark": {"url": "https://img.logo.dev/assets/test", "source": None}}}
        def fetch(url, key=None):
            return json.dumps(payload).encode() if key else png()
        with tempfile.TemporaryDirectory() as directory, patch.object(wordmarks, "ROOT", Path(directory)):
            result = wordmarks.fetch_artwork({"slug": "test", "domain": "test.com"}, {}, "sk_fixture", fetch)
            self.assertEqual(result["kind"], "brandmark")

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
