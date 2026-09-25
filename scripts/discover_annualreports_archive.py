"""Discover historical annual-report PDFs from the AnnualReports.com archive.

This is a discovery source only.  The extraction pipeline still validates the
company and financial year from the document before accepting it.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = "https://www.annualreports.com"
HEADERS = {"User-Agent": "StockLayer report research (+https://stocklayer.uk/)"}


def get(url: str) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")


def links(html: str, pattern: str) -> list[str]:
    return list(dict.fromkeys(re.findall(pattern, html, flags=re.I)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    companies_path = ROOT / "uk-companies.json"
    if not companies_path.exists():
        companies_path = ROOT / "ftse100.json"
    companies = json.loads(companies_path.read_text(encoding="utf-8"))
    if args.limit:
        companies = companies[:args.limit]
    index_path = ROOT / "annual-reports" / "reports-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    added = 0
    checked = 0
    for company in companies:
        name = company["companyName"]
        try:
            search = get(ARCHIVE + "/Companies?search=" + urllib.parse.quote(name))
            pages = links(search, r'href=["\'](/Company/[^"\'#?]+)["\']')
            if not pages:
                continue
            page = ARCHIVE + pages[0]
            html = get(page)
            checked += 1
        except Exception as error:
            print(f"{company['slug']}: search failed ({type(error).__name__})")
            continue
        reports = index.setdefault("companies", {}).setdefault(company["slug"], {"reports": []}).setdefault("reports", [])
        known = {report.get("url") for report in reports}
        for href in links(html, r'href=["\']([^"\']*HostedData/AnnualReportArchive/[^"\']+\.pdf)["\']'):
            match = re.search(r"(?<!\d)(20\d{2})(?!\d)", href)
            if not match:
                continue
            year = int(match.group(1))
            url = urllib.parse.urljoin(ARCHIVE, href.replace("&amp;", "&"))
            if not args.start_year <= year <= args.end_year or url in known:
                continue
            reports.append({
                "year": year,
                "title": "Annual report archive PDF",
                "url": url,
                "sourceUrl": page,
                "discoveryStatus": "annualreports_archive_candidate",
            })
            known.add(url)
            added += 1
    if not args.dry_run:
        index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Checked {checked} archive pages; added {added} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
