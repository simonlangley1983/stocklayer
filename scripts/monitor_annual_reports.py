#!/usr/bin/env python3
"""Monitor FTSE 100 annual report pages and maintain a report index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPANIES_FILE = ROOT / "uk-companies.json"
LOCAL_COMPANIES_FALLBACK = ROOT / "ftse100.json"
DEFAULT_SOURCES_FILE = ROOT / "annual-reports" / "sources.json"
DEFAULT_INDEX_FILE = ROOT / "annual-reports" / "reports-index.json"
USER_AGENT = "StockLayer annual report monitor (+https://stocklayer.co.uk)"
REPORT_KEYWORDS = ("annual report", "annual-report", "annualreview", "annual review", "integrated report")
EXCLUDED_KEYWORDS = ("half year", "half-year", "interim", "quarter", "q1", "q2", "q3", "q4")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._active_href = href
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._active_href:
            return
        self.links.append({
            "href": self._active_href,
            "text": normalise_space(" ".join(self._active_text)),
        })
        self._active_href = None
        self._active_text = []


def normalise_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def slugify(value: str) -> str:
    slug = value.lower()
    slug = slug.replace("&", " and ")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_companies(path: Path) -> tuple[list[dict[str, Any]], Path]:
    source = path
    if not source.exists() and path == DEFAULT_COMPANIES_FILE and LOCAL_COMPANIES_FALLBACK.exists():
        source = LOCAL_COMPANIES_FALLBACK
    companies = read_json(source, [])
    if not isinstance(companies, list) or not companies:
        raise SystemExit(f"No companies found in {source}")
    return companies, source


def clean_domain(value: str | None) -> str:
    domain = str(value or "").strip().lower()
    domain = domain.replace("https://", "").replace("http://", "").strip("/")
    return domain


def candidate_source_urls(company: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for field in ("annualReportsUrl", "annualReportUrl", "reportsUrl", "investorRelationsUrl"):
        value = str(company.get(field) or "").strip()
        if value and value.startswith(("http://", "https://")):
            urls.append(value)

    domain = clean_domain(company.get("domain"))
    if domain:
        urls.extend([
            f"https://www.{domain}/investors/results-reports-and-presentations",
            f"https://www.{domain}/investors/annual-reports",
            f"https://www.{domain}/investors/reports-results",
            f"https://www.{domain}/investors",
            f"https://www.{domain}/investor-relations/annual-reports",
            f"https://www.{domain}/investor-relations",
        ])

    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def build_sources(companies: list[dict[str, Any]], existing: dict[str, Any]) -> dict[str, Any]:
    companies_by_slug = existing.setdefault("companies", {})
    for company in companies:
        slug = company.get("slug") or slugify(company.get("companyName") or company.get("ticker") or "")
        if not slug:
            continue
        record = companies_by_slug.setdefault(slug, {})
        record["companyName"] = company.get("companyName") or company.get("name")
        record["ticker"] = company.get("ticker")
        record["domain"] = clean_domain(company.get("domain"))
        record.setdefault("sourceUrls", candidate_source_urls(company))
        record.setdefault("notes", "")
    existing["generatedAt"] = datetime.now(timezone.utc).isoformat()
    existing["sourceCount"] = len(companies_by_slug)
    return existing


def fetch_url(url: str, timeout: int) -> tuple[int | None, str, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body, content_type
    except urllib.error.HTTPError as error:
        return error.code, "", error.reason
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return None, "", str(error)


def extract_year(value: str, current_year: int) -> int | None:
    years = [int(match) for match in re.findall(r"\b(20[1-3][0-9])\b", value)]
    valid = [year for year in years if current_year - 7 <= year <= current_year + 1]
    return max(valid) if valid else None


def is_report_link(text: str, url: str, current_year: int) -> bool:
    haystack = f"{text} {url}".lower()
    if any(keyword in haystack for keyword in EXCLUDED_KEYWORDS):
        return False
    has_report_keyword = any(keyword in haystack for keyword in REPORT_KEYWORDS)
    has_pdf = ".pdf" in urllib.parse.urlparse(url).path.lower()
    return bool(has_report_keyword and (has_pdf or extract_year(haystack, current_year)))


def link_id(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}|{title}".encode("utf-8")).hexdigest()[:16]


def parse_report_links(source_url: str, html: str, current_year: int) -> list[dict[str, Any]]:
    parser = LinkParser()
    parser.feed(html)
    reports: list[dict[str, Any]] = []
    for link in parser.links:
        absolute_url = urllib.parse.urljoin(source_url, link["href"])
        title = link["text"] or urllib.parse.unquote(Path(urllib.parse.urlparse(absolute_url).path).name)
        if not is_report_link(title, absolute_url, current_year):
            continue
        year = extract_year(f"{title} {absolute_url}", current_year)
        reports.append({
            "id": link_id(absolute_url, title),
            "year": year,
            "title": title,
            "url": absolute_url,
            "sourceUrl": source_url,
            "detectedAt": datetime.now(timezone.utc).isoformat(),
            "status": "detected",
        })
    return reports


def merge_reports(existing: list[dict[str, Any]], discovered: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_id = {report.get("id"): report for report in existing if report.get("id")}
    new_count = 0
    for report in discovered:
        report_id = report["id"]
        if report_id in by_id:
            preserved = by_id[report_id]
            preserved.update({key: value for key, value in report.items() if key not in {"detectedAt"}})
        else:
            by_id[report_id] = report
            new_count += 1

    return sorted(
        by_id.values(),
        key=lambda item: (item.get("year") or 0, item.get("title") or ""),
        reverse=True,
    ), new_count


def monitor_company(company: dict[str, Any], source_record: dict[str, Any], existing_company_index: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    current_year = datetime.now(timezone.utc).year
    source_urls = source_record.get("sourceUrls") or candidate_source_urls(company)
    existing_reports = existing_company_index.get("reports") or []
    discovered_reports: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    for url in source_urls[: args.max_sources_per_company]:
        if args.init_only:
            checks.append({"url": url, "status": "not_checked"})
            continue
        status_code, body, error = fetch_url(url, args.timeout)
        check = {
            "url": url,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "statusCode": status_code,
        }
        if error:
            check["error"] = error
        if body:
            reports = parse_report_links(url, body, current_year)
            check["reportLinksFound"] = len(reports)
            discovered_reports.extend(reports)
        checks.append(check)

    merged_reports, new_count = merge_reports(existing_reports, discovered_reports)
    latest_years = [report.get("year") for report in merged_reports if isinstance(report.get("year"), int)]
    status = "monitoring"
    if args.init_only:
        status = "source_indexed"
    elif not merged_reports:
        status = "needs_source_review"

    record = {
        "slug": company.get("slug"),
        "companyName": company.get("companyName") or company.get("name"),
        "ticker": company.get("ticker"),
        "domain": clean_domain(company.get("domain")),
        "status": status,
        "latestReportYear": max(latest_years) if latest_years else None,
        "sources": checks,
        "reports": merged_reports,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    return record, new_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", type=Path, default=DEFAULT_COMPANIES_FILE)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_FILE)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_FILE)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--max-sources-per-company", type=int, default=3)
    parser.add_argument("--max-companies", type=int, default=0)
    parser.add_argument("--init-only", action="store_true", help="Create source/index files without fetching pages.")
    args = parser.parse_args()

    companies, companies_source = load_companies(args.companies)
    if args.max_companies:
        companies = companies[: args.max_companies]

    sources = build_sources(companies, read_json(args.sources, {"companies": {}}))
    existing_index = read_json(args.index, {"companies": {}})
    index_companies = existing_index.setdefault("companies", {})
    total_new_reports = 0

    for company in companies:
        slug = company.get("slug") or slugify(company.get("companyName") or company.get("ticker") or "")
        if not slug:
            continue
        record, new_count = monitor_company(company, sources["companies"].get(slug, {}), index_companies.get(slug, {}), args)
        index_companies[slug] = record
        total_new_reports += new_count

    now = datetime.now(timezone.utc).isoformat()
    index = {
        "generatedAt": now,
        "source": companies_source.name,
        "companyCount": len(index_companies),
        "newReportsDetected": total_new_reports,
        "note": "Annual-report monitor records detected report links and source-page health. Keyword extraction can consume this index once report text extraction is enabled.",
        "companies": dict(sorted(index_companies.items())),
    }

    write_json(args.sources, sources)
    write_json(args.index, index)
    print(f"Updated {args.sources.relative_to(ROOT)} for {len(sources['companies'])} companies")
    print(f"Updated {args.index.relative_to(ROOT)}; new reports detected: {total_new_reports}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
