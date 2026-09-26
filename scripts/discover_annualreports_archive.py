"""Discover missing historical annual-report PDFs from AnnualReports.com.

This is a discovery source only: every candidate is subsequently validated by
the PDF extractor. Search results are matched to the issuer name before a
company page is read; selecting the first result is unsafe for short names.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = "https://www.annualreports.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# AnnualReports uses former legal names for a small number of listed issuers.
SEARCH_ALIASES = {
    "barratt-redrow": "Barratt Developments",
    "standard-life": "Phoenix Group",
    "j-sainsbury": "J Sainsbury",
    "international-consolidated-airlines": "International Consolidated Airlines",
}


def get(url: str, timeout: int, retries: int) -> str:
    """Fetch a page with bounded retries for transient archive limits."""
    error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(3 * 1024 * 1024).decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as caught:
            error = caught
            if attempt == retries:
                break
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(error) if error else "archive request failed")


def normalise(value: str) -> str:
    return re.sub(r"\b(plc|p\.?l\.?c\.?|group|holdings|limited|ltd|public|company)\b|[^a-z0-9]", "", value.lower())


def company_pages(html: str) -> list[tuple[str, str]]:
    """Return (href, display name) pairs from archive search results."""
    return list(dict.fromkeys(re.findall(
        r'<span class="companyName"><a href="(/Company/[^"#?]+)">\s*([^<]+?)\s*</a>',
        html, flags=re.I,
    )))


def choose_page(name: str, html: str) -> str | None:
    wanted = normalise(name)
    options = company_pages(html)
    exact = [href for href, label in options if normalise(label) == wanted]
    if exact:
        return exact[0]
    # Do not accept a partial match for short issuers: BP must not become Aker
    # BP, and IMI must not become a company merely containing those letters.
    tokens = set(re.findall(r"[a-z0-9]+", name.lower()))
    scored: list[tuple[float, str]] = []
    for href, label in options:
        label_tokens = set(re.findall(r"[a-z0-9]+", label.lower()))
        overlap = len(tokens & label_tokens) / max(1, len(tokens))
        if overlap == 1 and len(label_tokens - tokens) <= 1:
            scored.append((overlap, href))
    return max(scored, default=(0, ""))[1] or None


def pdf_links(html: str) -> list[str]:
    return list(dict.fromkeys(re.findall(
        r'href=["\']([^"\']*HostedData/AnnualReportArchive/[^"\']+\.pdf)["\']',
        html, flags=re.I,
    )))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--max-failures", type=int, default=8,
                        help="Stop after this many failed companies; prevents a blocked archive consuming a run")
    parser.add_argument("--delay", type=float, default=0.4, help="Seconds between archive requests")
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
    added = checked = matched = failures = 0
    for company in companies:
        search_name = SEARCH_ALIASES.get(company["slug"], company["companyName"])
        try:
            search = get(ARCHIVE + "/Companies?search=" + urllib.parse.quote(search_name), args.timeout, args.retries)
            time.sleep(args.delay)
            href = choose_page(search_name, search)
            if not href:
                print(f"{company['slug']}: no exact archive company match")
                continue
            matched += 1
            page = ARCHIVE + href
            html = get(page, args.timeout, args.retries)
            time.sleep(args.delay)
            checked += 1
        except Exception as error:
            failures += 1
            print(f"{company['slug']}: archive lookup failed ({type(error).__name__}: {error})")
            if failures >= args.max_failures:
                print(f"Stopping archive discovery after {failures} failures; retry in a future manual run")
                break
            continue
        reports = index.setdefault("companies", {}).setdefault(company["slug"], {"reports": []}).setdefault("reports", [])
        known = {report.get("url") for report in reports}
        for href in pdf_links(html):
            match = re.search(r"(?<!\d)(20\d{2})(?!\d)", href)
            if not match:
                continue
            year = int(match.group(1))
            url = urllib.parse.urljoin(ARCHIVE, href.replace("&amp;", "&"))
            if not args.start_year <= year <= args.end_year or url in known:
                continue
            reports.append({
                "year": year,
                "title": "AnnualReports.com archive PDF",
                "url": url,
                "sourceUrl": page,
                "discoveryStatus": "annualreports_archive_candidate",
            })
            known.add(url)
            added += 1
    if not args.dry_run:
        index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Matched {matched} archive companies; checked {checked} pages; added {added} candidates; failures {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
