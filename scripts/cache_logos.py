#!/usr/bin/env python3
"""Cache company logos referenced by companies.json into the repository."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError


DEFAULT_SIZE = 256
USER_AGENT = "StockLayer logo cache (+https://stocklayer.co.uk)"


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "company"


def company_slug(company: dict) -> str:
    key = (
        company.get("slug")
        or company.get("ticker")
        or company.get("companyName")
        or company.get("name")
    )
    return slugify(str(key))


def is_stale(path: Path, max_age_days: int) -> bool:
    if not path.exists() or max_age_days <= 0:
        return True
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds > max_age_days * 24 * 60 * 60


def fetch_image(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("content-type", "")
        data = response.read()

    if not data:
        raise ValueError("empty response")
    if "image" not in content_type.lower():
        raise ValueError(f"unexpected content type: {content_type or 'unknown'}")
    try:
        with Image.open(io.BytesIO(data)) as image:
            png = io.BytesIO()
            image.convert("RGBA").save(png, format="PNG")
            return png.getvalue()
    except UnidentifiedImageError as error:
        raise ValueError("response is not a supported image") from error


def provider_urls(domain: str, token: str, size: int) -> list[tuple[str, str]]:
    providers: list[tuple[str, str]] = []
    if token:
        query = urllib.parse.urlencode(
            {"token": token, "size": size, "format": "png"}
        )
        providers.append(("logo.dev", f"https://img.logo.dev/{domain}?{query}"))
    google_query = urllib.parse.urlencode(
        {"domain_url": f"https://{domain}", "sz": size}
    )
    providers.append(
        ("google-favicon", f"https://www.google.com/s2/favicons?{google_query}")
    )
    return providers


def load_companies(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    if not all(isinstance(company, dict) for company in data):
        raise ValueError(f"{path} must contain only JSON objects")
    return data


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cache company logos into the repository."
    )
    parser.add_argument(
        "--companies", default="companies.json", help="Path to the company JSON file."
    )
    parser.add_argument(
        "--out-dir", default="logos", help="Directory to write cached logo PNGs."
    )
    parser.add_argument(
        "--manifest",
        default="logos/manifest.json",
        help="Path to write the logo manifest.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=30,
        help="Refresh logos older than this many days. Use 0 to force.",
    )
    parser.add_argument(
        "--size", type=int, default=DEFAULT_SIZE, help="Requested logo size in pixels."
    )
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Delay between API calls.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched without writing files.",
    )
    args = parser.parse_args()

    token = os.environ.get("LOGO_DEV_TOKEN", "").strip()
    companies_path = Path(args.companies)
    out_dir = Path(args.out_dir)
    manifest_path = Path(args.manifest)
    companies = load_companies(companies_path)

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    fetched = skipped = failed = 0

    for company in companies:
        slug = company_slug(company)
        relative_logo_path = (out_dir / f"{slug}.png").as_posix()
        company["logo"] = relative_logo_path
        logo_path = Path(relative_logo_path)
        domain = company.get("domain")
        entry = {
            "companyName": company.get("companyName") or company.get("name"),
            "ticker": company.get("ticker"),
            "domain": domain,
            "path": relative_logo_path,
        }

        if not domain:
            entry["status"] = "missing-domain"
            entries.append(entry)
            skipped += 1
            continue

        if not is_stale(logo_path, args.max_age_days):
            entry["status"] = "cached"
            entries.append(entry)
            skipped += 1
            continue

        if args.dry_run:
            entry["status"] = "would-fetch"
            entries.append(entry)
            fetched += 1
            continue

        errors = []
        for provider, url in provider_urls(str(domain), token, args.size):
            try:
                logo_path.write_bytes(fetch_image(url, args.timeout))
                entry.update(
                    status="fetched",
                    provider=provider,
                    fetchedAt=datetime.now(timezone.utc).isoformat(),
                )
                fetched += 1
                time.sleep(args.sleep)
                break
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
                errors.append(f"{provider}: {error}")
        else:
            entry["status"] = "failed"
            entry["error"] = " | ".join(errors)
            failed += 1

        entries.append(entry)

    if not args.dry_run:
        write_json(companies_path, companies)
        write_json(
            manifest_path,
            {
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "logos": entries,
            },
        )

    print(f"Logo cache complete: {fetched} fetched, {skipped} skipped, {failed} failed.")
    return 1 if failed and fetched == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
