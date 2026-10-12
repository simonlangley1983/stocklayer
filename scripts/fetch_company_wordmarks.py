"""Retrieve verified transparent hero logos using the server-side Logo.dev API.

Never store keys or temporary signed download URLs in the public manifest.
Existing good files survive missing artwork, API errors and partial refreshes.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image

API = "https://api.logo.dev/v2/brands"
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "logos/hero-manifest.json"


class ProviderError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Provider HTTP {status}")


def request(url, key=None):
    headers = {"User-Agent": "StockLayer company logo refresh"}
    if key:
        if urllib.parse.urlparse(url).hostname != "api.logo.dev":
            raise ValueError("Refusing to send API credentials to another host")
        headers["Authorization"] = f"Bearer {key}"
    class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, response_headers, new_url):
            parsed = urllib.parse.urlparse(new_url)
            allowed_host = "api.logo.dev" if key else "img.logo.dev"
            if parsed.scheme != "https" or parsed.hostname != allowed_host:
                raise ProviderError("unexpected-redirect")
            return super().redirect_request(req, fp, code, msg, response_headers, new_url)
    opener = urllib.request.build_opener(RestrictedRedirect())
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=30) as response:
                if response.status == 202:
                    if attempt == 3:
                        raise ProviderError(202)
                    delay = response.headers.get("Retry-After", "5")
                    time.sleep(min(60, max(1, float(delay) if delay.isdigit() else 5)))
                    continue
                content = response.read(10_000_001)
                if len(content) > 10_000_000:
                    raise ValueError("Image or response exceeds size limit")
                return content
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise ProviderError(error.code) from None
            delay = error.headers.get("Retry-After", "")
            time.sleep(min(60, max(1, float(delay) if delay.isdigit() else 2 ** (attempt + 1))))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise ProviderError("network") from None
            time.sleep(2 ** (attempt + 1))
    raise ProviderError("retry-limit")


def inspect_png(content):
    """Inspect, never remove backgrounds or alter the company's artwork."""
    with Image.open(io.BytesIO(content)) as image:
        if image.format != "PNG" or image.width > 4096 or image.height > 4096:
            raise ValueError("Invalid PNG dimensions or format")
        alpha = image.convert("RGBA").getchannel("A")
        histogram = alpha.histogram()
        percent = sum(histogram[:250]) / (image.width * image.height) * 100
        corners = [alpha.getpixel(point) for point in [(0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1)]]
        # Reject opaque images and tiny rounded-corner backgrounds, not just JPGs.
        if percent < 5 or any(value > 8 for value in corners):
            raise ValueError("Artwork does not have a clear transparent background")
        if alpha.getextrema()[1] < 250:
            raise ValueError("Blank or insufficiently opaque artwork")
        return {"width": image.width, "height": image.height, "transparentPercent": round(percent, 2)}


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def download_url(value):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "img.logo.dev" or not parsed.path.startswith("/assets/"):
        raise ValueError("Unexpected asset download host or path")
    query = dict(urllib.parse.parse_qsl(parsed.query))
    query.update({"format": "png", "theme": "dark", "size": "800"})
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def fetch_artwork(company, previous, key, fetch=request):
    slug, domain = company["slug"], company["domain"]
    response = json.loads(fetch(f"{API}?{urllib.parse.urlencode({'domain': domain, 'nosubscribe': 'true'})}", key))
    brand = response.get("data")
    if not isinstance(brand, dict) or brand.get("status") not in (None, "ready"):
        raise ValueError("Invalid brand response")
    for kind in ("brandmark", "logo"):
        asset = brand.get(kind)
        if not isinstance(asset, dict) or not asset.get("url"):
            continue
        if asset.get("source", {}).get("has_transparency") is False:
            continue
        etag = asset.get("etag")
        if previous.get("etag") == etag and previous.get("kind") == kind and previous.get("domain") == domain and previous.get("sha256"):
            saved = (ROOT / previous.get("path", "")).resolve()
            if saved.is_relative_to(ROOT / "images/company-logos") and saved.is_file():
                content = saved.read_bytes()
                if hashlib.sha256(content).hexdigest() == previous["sha256"]:
                    inspect_png(content)
                    return previous
        content = fetch(download_url(asset["url"]))
        try:
            metadata = inspect_png(content)
        except ValueError:
            continue
        asset_path = f"images/company-logos/{slug}-hero.png"
        atomic_write(ROOT / asset_path, content)
        return {"path": asset_path, "domain": domain, "kind": kind, "etag": etag, "sha256": hashlib.sha256(content).hexdigest(), "fetchedAt": datetime.now(timezone.utc).isoformat(), **metadata}
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slugs", default="", help="Comma-separated slugs; blank means the full company universe")
    parser.add_argument("--companies", default="ftse100.json")
    args = parser.parse_args()
    key = os.environ.get("LOGO_DEV_SECRET_KEY", "").strip()
    if not key.startswith("sk_"):
        print("LOGO_DEV_SECRET_KEY is missing or is not a secret API key. Existing artwork is unchanged.")
        return 1
    companies = json.loads((ROOT / args.companies).read_text(encoding="utf-8-sig"))
    selected = {slug.strip() for slug in args.slugs.split(",") if slug.strip()}
    if selected - {company.get("slug") for company in companies}:
        print("Unknown company slug supplied. No artwork changed.")
        return 1
    companies = [company for company in companies if not selected or company.get("slug") in selected]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"version": 1, "provider": "logo.dev", "logos": {}, "issues": {}}
    ready = missing = errors = retained = attempted = 0
    for company in companies:
        slug = company.get("slug", "")
        if not re.fullmatch(r"[a-z0-9-]+", slug) or not company.get("domain"):
            errors += 1
            continue
        attempted += 1
        previous = manifest["logos"].get(slug, {})
        try:
            artwork = fetch_artwork(company, previous, key)
            if artwork:
                manifest["logos"][slug] = artwork
                manifest["issues"].pop(slug, None)
                ready += 1
                print(f"{slug}: transparent {artwork['kind']} ready")
            else:
                missing += 1
                retained += bool(previous)
                manifest["issues"][slug] = {"status": "no-transparent-artwork"}
                print(f"{slug}: no suitable transparent artwork; existing logo preserved")
        except ProviderError as error:
            errors += 1
            retained += bool(previous)
            manifest["issues"][slug] = {"status": "provider-error", "httpStatus": error.status}
            print(f"{slug}: provider request failed (HTTP {error.status}); existing logo preserved")
            if error.status in (401, 402, 403):
                break
        except (ValueError, OSError, KeyError, Image.DecompressionBombError):
            errors += 1
            retained += bool(previous)
            manifest["issues"][slug] = {"status": "invalid-response"}
            print(f"{slug}: invalid response; existing logo preserved")
        time.sleep(1.2)
    manifest["lastRun"] = {"at": datetime.now(timezone.utc).isoformat(), "requested": len(companies), "attempted": attempted, "ready": ready, "noTransparentArtwork": missing, "errors": errors, "retained": retained}
    atomic_write(MANIFEST, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    print(f"Result: {ready} transparent, {missing} without suitable artwork, {errors} errors, {retained} previous logos retained")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
