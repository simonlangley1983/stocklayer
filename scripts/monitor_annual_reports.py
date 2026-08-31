#!/usr/bin/env python3
"""Monitor FTSE 100 annual report pages and maintain a report index."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPANIES_FILE = ROOT / "uk-companies.json"
LOCAL_COMPANIES_FALLBACK = ROOT / "ftse100.json"
DEFAULT_SOURCES_FILE = ROOT / "annual-reports" / "sources.json"
DEFAULT_INDEX_FILE = ROOT / "annual-reports" / "reports-index.json"
DEFAULT_SEED_INDEX_URL = (
    "https://raw.githubusercontent.com/simonlangley1983/stocklayer/"
    "main/annual-reports/reports-index.json"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36 "
    "StockLayer-Annual-Report-Research/1.0"
)
REPORT_KEYWORDS = ("annual report", "annual-report", "annualreview", "annual review", "integrated report")
EXCLUDED_KEYWORDS = ("half year", "half-year", "interim", "quarter", "q1", "q2", "q3", "q4")
REPORT_HUB_KEYWORDS = (
    "annual report",
    "annual-report",
    "annual reports",
    "annual-reports",
    "reports and presentations",
    "reports-and-presentations",
    "results reports",
    "results-reports",
    "reporting centre",
    "reporting-center",
)
MAX_SOURCE_PAGE_BYTES = 6 * 1024 * 1024

# A small curated layer keeps the monitor useful when an issuer's corporate
# domain redirects to a product site, or when its report centre is not linked
# from the usual investor-relations paths.  These are all first-party pages.
OFFICIAL_SOURCE_OVERRIDES: dict[str, list[str]] = {
    "III.L": ["https://www.3i.com/investor-relations/reports/2026/"],
    "ADM.L": ["https://www.admiralgroup.co.uk/investor-relations/results-reports-and-presentations"],
    "ALW.L": ["https://www.alliancewitan.com/documents"],
    "ABF.L": ["https://www.abf.co.uk/investors/results-reports-presentations/annual-reports"],
    "AUTO.L": ["https://plc.autotrader.co.uk/investors/2025-annual-report/"],
    "BA.L": ["https://annualreport.baesystems.com/2025"],
    "BARC.L": ["https://home.barclays/investor-relations/reports-and-events/annual-reports/"],
    "BEZ.L": ["https://www.beazley.com/en/investor-relations/results-reports-and-presentations/"],
    "BT-A.L": ["https://www.bt.com/about/investors/financial-reporting-and-news/annual-reports"],
    "CCH.L": ["https://www.coca-colahellenic.com/en/investor-relations/2025-integrated-annual-report"],
    "CPG.L": ["https://www.compass-group.com/en/investors/annual-reports.html"],
    "CTEC.L": ["https://www.convatecgroup.com/investors/reports-results-and-presentations/"],
    "EZJ.L": ["https://corporate.easyjet.com/investors/reports-and-presentations/2024/default.aspx"],
    "EXPN.L": ["https://www.experianplc.com/investors/results-reports-presentations/results-presentations"],
    "FCIT.L": ["https://www.fandc.com/investors/financial-reports/"],
    "GAW.L": ["https://investor.games-workshop.com/annual-reports-and-half-year-results"],
    "GSK.L": ["https://www.gsk.com/en-gb/investors/financial-reports/corporate-reports-archive/"],
    "HLN.L": ["https://www.haleon.com/investors/annual-report-2025"],
    "HL.L": ["https://www.hl.co.uk/about-us/investor-relations/results-reports-and-presentations"],
    "HSX.L": ["https://www.hiscoxgroup.com/investors/results-and-presentations"],
    "HWDN.L": ["https://www.howdenjoinerygroupplc.com/investors/results-reports-and-presentations"],
    "IMI.L": ["https://www.imiplc.com/investors/results-reports-and-presentations/"],
    "IMB.L": ["https://www.imperialbrandsplc.com/investors/annual-report.html"],
    "IAG.L": ["https://www.iairgroup.com/investors-and-shareholders/financial-reporting/annual-reports"],
    "ITRK.L": ["https://www.intertek.com/investors/results/"],
    "LGEN.L": ["https://group.legalandgeneral.com/en/investors/results-reports-and-presentations"],
    "LLOY.L": ["https://www.lloydsbankinggroup.com/investors/financial-downloads.html"],
    "MKS.L": ["https://corporate.marksandspencer.com/investors/reports-results-and-presentations"],
    "MRO.L": ["https://www.melroseplc.net/investors/results-reports-and-presentations/"],
    "MNDI.L": ["https://www.mondigroup.com/investors/results-reports-and-presentations/"],
    "PSON.L": ["https://plc.pearson.com/en-GB/investors/results-reports-presentations"],
    "PSH.L": ["https://pershingsquareholdings.com/company-reports/financial-statements/"],
    "PSN.L": ["https://www.persimmonhomes.com/corporate/investors/results-reports-and-presentations"],
    "PRU.L": ["https://www.prudentialplc.com/en/investors/results-reports-and-events/annual-reports"],
    "RMV.L": ["https://plc.rightmove.co.uk/investors/results-reports-and-presentations/"],
    "SGE.L": ["https://www.sage.com/en-gb/company/investors/results-reports-and-presentations/"],
    "SMT.L": ["https://www.scottishmortgage.com/en/uk/individual-investors/literature"],
    "SVT.L": ["https://www.severntrent.com/investors/results-reports-and-presentations/"],
    "SHEL.L": ["https://www.shell.com/investors/results-and-reporting/annual-publications.html"],
    "SN.L": ["https://www.smith-nephew.com/en/investors/results-reports-and-presentations"],
    "STJ.L": ["https://www.sjp.co.uk/about-us/investor-relations/results-reports-and-presentations"],
    "ULVR.L": ["https://www.unilever.com/investors/annual-report-and-accounts/"],
}

# Direct first-party documents are deliberately limited to reports whose URLs
# have been independently verified. They make blocked or JavaScript-only report
# centres deterministic without treating third-party mirrors as source data.
OFFICIAL_REPORT_SEEDS: dict[str, list[dict[str, Any]]] = {
    "III.L": [{"year": 2026, "title": "3i Group Annual Report and accounts FY2026", "url": "https://www.3i.com/investor-relations/annual-report-2026/downloads/3i-Group-Annual-Report-and-accounts-FY2026.pdf"}],
    "ALW.L": [{"year": 2025, "title": "Alliance Witan Annual Report 2025", "url": "https://media.umbraco.io/alliance-trust/zhcddtp5/4041-alliance-witan-annual-report_interactive.pdf"}],
    "ABF.L": [{"year": 2025, "title": "Associated British Foods Annual Report 2025", "url": "https://www.abf.co.uk/content/dam/abf/corporate/Documents/investors/annual-and-interim-reports/2025/abf-annual-report-2025.pdf.downloadasset.pdf"}],
    "BA.L": [{"year": 2025, "title": "BAE Systems Annual Report 2025", "url": "https://annualreport.baesystems.com/dam/jcr%3A105fe9f2-cff7-4960-9d99-956aba996540/BAE-Systems-Annual-Report-2025.2026-03-24-10-33-48.pdf"}],
    "BARC.L": [{"year": 2025, "title": "Barclays PLC Annual Report 2025", "url": "https://home.barclays/content/dam/home-barclays/documents/investor-relations/reports-and-events/annual-reports/2025/Barclays-PLC-Annual-Report-2025.pdf"}],
    "BEZ.L": [{"year": 2025, "title": "Beazley Annual Report and Accounts 2025", "url": "https://prod.dxp.beazley.com/globalassets/ir-documents/annual-reports/annual-report-2025/annual-report-and-accounts.pdf"}],
    "BT-A.L": [{"year": 2026, "title": "BT Group plc Annual Report 2026", "url": "https://www.bt.com/content/dam/bt-plc/assets/documents/investors/financial-reporting-and-news/annual-reports/2026/2026-bt-group-annual-report.pdf"}],
    "CCH.L": [{"year": 2025, "title": "Coca-Cola HBC Integrated Annual Report 2025", "url": "https://www.coca-colahellenic.com/content/dam/cch/us/documents/oar2025/Coca-Cola-HBC-Integrated-Annual-Report-2025.pdf.downloadasset.pdf"}],
    "CPG.L": [{"year": 2025, "title": "Compass Group Annual Report 2025", "url": "https://www.compass-group.com/content/dam/compass-group/corporate/oar-2025/oar-page/annual-report-2025.pdf"}],
    "CTEC.L": [{"year": 2025, "title": "Convatec Annual Report and Accounts 2025", "url": "https://www.convatecgroup.com/siteassets/investors/ec1372707_convatec-ar-2025_aw_interactive.pdf"}],
    "EZJ.L": [{"year": 2025, "title": "easyJet Annual Report and Accounts 2025", "url": "https://s203.q4cdn.com/522538739/files/doc_financials/2025/ar/easyJetARA25_DIGITAL_sm.pdf"}],
    "EXPN.L": [{"year": 2026, "title": "Experian Annual Report 2026", "url": "https://www.experianplc.com/content/dam/marketing/global/plc/en/assets/documents/reports/2026/experian-annual-report-2026.pdf"}],
    "FCIT.L": [{"year": 2025, "title": "F&C Investment Trust Annual Report and Accounts 2025", "url": "https://docs.columbiathreadneedle.com/documents/FandC%20-%20Annual%20Report%20and%20Accounts.pdf?inline=true"}],
    "GAW.L": [{"year": 2026, "title": "Games Workshop Group Annual Report 2026", "url": "https://assets.ctfassets.net/ost7hseic9hc/1C4g3JYVr04ZCkPb39ygIA/93055850ed01cc1fa1da261e29a57fa5/Accounts_2025-26_FINAL_v2.pdf"}],
    "HLN.L": [{"year": 2025, "title": "Haleon Annual Report and Form 20-F 2025", "url": "https://www.haleon.com/content/dam/haleon/corporate/documents/investors/oar-2025/Annual-Report-and-Form-20-F-2025.pdf.downloadasset.pdf"}],
    "HWDN.L": [{"year": 2025, "title": "Howden Joinery Group Annual Report and Accounts 2025", "url": "https://www.howdenjoinerygroupplc.com/docs/librariesprovider25/archives/annual-reports/2025-annual-report.pdf"}],
    "IMI.L": [{"year": 2025, "title": "IMI plc Annual Report 2025", "url": "https://www.imiplc.com/media/1i2ddtki/imi_ar_2025.pdf"}],
    "IMB.L": [{"year": 2025, "title": "Imperial Brands Annual Report and Accounts 2025", "url": "https://www.imperialbrandsplc.com/content/dam/imperialbrands/corporate/documents/investor-hub/reports/oar-2025/imperial-brands-2025-annual-report.pdf"}],
    "LLOY.L": [{"year": 2025, "title": "Lloyds Banking Group Annual Report and Accounts 2025", "url": "https://www.lloydsbankinggroup.com/assets/pdfs/investors/financial-performance/lloyds-banking-group-plc/2025/q4/2025-lbg-annual-report.pdf"}],
    "LGEN.L": [{"year": 2025, "title": "Legal & General Group Annual Report and Accounts 2025", "url": "https://group.legalandgeneral.com/asset/4984d6/globalassets/group/reporting-hub/reports/2026/annual-report-and-accounts-2025/annual-report-and-accounts-2025-accessible-pdf.pdf"}],
    "MKS.L": [{"year": 2026, "title": "Marks and Spencer Group Annual Report and Financial Statements 2026", "url": "https://corporate.marksandspencer.com/sites/marksandspencer/files/marksandspencer/annual-report/m-and-s-annual-report-and-financial-statements-2026.pdf"}],
    "PSON.L": [{"year": 2025, "title": "Pearson plc Annual Report and Accounts 2025", "url": "https://plc.pearson.com/sites/pearson-corp/files/annual-reports/2025/financial-statements-2025.pdf"}],
    "SBRY.L": [{"year": 2026, "title": "J Sainsbury plc Annual Report and Financial Statements 2026", "url": "https://corporate.sainsburys.co.uk/media/sx2bk5c5/j-sainsbury-plc-annual-report-and-financial-statements-2026-interactive.pdf"}],
}


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


def load_existing_index(path: Path) -> dict[str, Any]:
    existing = read_json(path, None)
    if isinstance(existing, dict):
        return existing
    if path == DEFAULT_INDEX_FILE:
        try:
            status, body, _, _ = fetch_url(DEFAULT_SEED_INDEX_URL, 20)
            if status == 200 and body:
                seeded = json.loads(body)
                if isinstance(seeded, dict):
                    return seeded
        except (ValueError, json.JSONDecodeError, urllib.error.URLError):
            pass
    return {"companies": {}}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


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
    domain = domain.split("/", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def candidate_source_urls(company: dict[str, Any]) -> list[str]:
    urls: list[str] = list(OFFICIAL_SOURCE_OVERRIDES.get(str(company.get("ticker") or ""), []))
    for field in ("annualReportsUrl", "annualReportUrl", "reportsUrl", "investorRelationsUrl"):
        value = str(company.get(field) or "").strip()
        if value and value.startswith(("http://", "https://")):
            urls.append(value)

    domain = clean_domain(company.get("domain"))
    if domain:
        paths = (
            "/investors/results-reports-and-presentations",
            "/investors/reports-and-presentations",
            "/investors/annual-reports",
            "/investors/annual-report",
            "/investors/reports-results",
            "/investors/results",
            "/investors/results-centre",
            "/investors/financial-results",
            "/investors",
            "/investor-relations/annual-reports",
            "/investor-relations",
            "/annual-reports",
            "/",
        )
        for host in (f"www.{domain}", domain):
            urls.extend(f"https://{host}{path}" for path in paths)

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
        generated_urls = candidate_source_urls(company)
        existing_urls = [
            str(url).replace("://www.www.", "://www.")
            for url in (record.get("sourceUrls") or [])
            if str(url).startswith(("http://", "https://"))
        ]
        # Generated URLs begin with curated first-party overrides, so keep them
        # ahead of stale guesses accumulated by earlier monitor runs.
        record["sourceUrls"] = list(dict.fromkeys([*generated_urls, *existing_urls]))
        record.setdefault("notes", "")
    existing["generatedAt"] = datetime.now(timezone.utc).isoformat()
    existing["sourceCount"] = len(companies_by_slug)
    return existing


def fetch_url(url: str, timeout: int) -> tuple[int | None, str, str, str | None]:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.7",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    try:
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.URLError as error:
            if not isinstance(error.reason, ssl.SSLCertVerificationError):
                raise
            response = urllib.request.urlopen(
                request,
                timeout=timeout,
                context=ssl._create_unverified_context(),  # noqa: SLF001
            )
        with response:
            content_type = response.headers.get("content-type", "")
            final_url = response.geturl()
            if "application/pdf" in content_type.lower():
                return response.status, "", final_url, None
            body = response.read(MAX_SOURCE_PAGE_BYTES).decode("utf-8", errors="replace")
            return response.status, body, final_url, None
    except urllib.error.HTTPError as error:
        return error.code, "", url, str(error.reason)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return None, "", url, str(error)


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
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key.lower() not in {"hash", "rev"}]
    canonical = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query), fragment=""))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


def embedded_document_links(source_url: str, raw_html: str) -> list[dict[str, str]]:
    decoded = html.unescape(raw_html).replace("\\/", "/").replace("\\u0026", "&")
    pattern = re.compile(
        r'''(?i)(?P<url>(?:https?:)?//[^"'<>\s]+?\.(?:pdf|ashx)(?:\?[^"'<>\s]*)?|/[^"'<>\s]+?\.(?:pdf|ashx)(?:\?[^"'<>\s]*)?)'''
    )
    links: list[dict[str, str]] = []
    for match in pattern.finditer(decoded):
        url = urllib.parse.urljoin(source_url, match.group("url"))
        title = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)
        links.append({"href": url, "text": title})
    return links


def parse_report_links(source_url: str, html: str, current_year: int) -> list[dict[str, Any]]:
    parser = LinkParser()
    parser.feed(html)
    reports: list[dict[str, Any]] = []
    all_links = [*parser.links, *embedded_document_links(source_url, html)]
    for link in all_links:
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


def parse_report_hub_links(source_url: str, raw_html: str) -> list[str]:
    parser = LinkParser()
    parser.feed(raw_html)
    source_host = urllib.parse.urlparse(source_url).netloc.lower().removeprefix("www.")
    hubs: list[str] = []
    for link in parser.links:
        absolute_url = urllib.parse.urljoin(source_url, link["href"])
        parsed = urllib.parse.urlparse(absolute_url)
        target_host = parsed.netloc.lower().removeprefix("www.")
        if target_host != source_host or parsed.path.lower().endswith((".pdf", ".ashx", ".zip")):
            continue
        haystack = f"{link['text']} {absolute_url}".lower()
        if any(keyword in haystack for keyword in REPORT_HUB_KEYWORDS):
            hubs.append(urllib.parse.urlunparse(parsed._replace(fragment="")))
    return list(dict.fromkeys(hubs))


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
    ticker = str(company.get("ticker") or "")
    discovered_reports: list[dict[str, Any]] = [
        {
            "id": link_id(seed["url"], seed["title"]),
            "year": seed["year"],
            "title": seed["title"],
            "url": seed["url"],
            "sourceUrl": OFFICIAL_SOURCE_OVERRIDES.get(ticker, [seed["url"]])[0],
            "detectedAt": datetime.now(timezone.utc).isoformat(),
            "status": "verified_official_seed",
        }
        for seed in OFFICIAL_REPORT_SEEDS.get(ticker, [])
    ]
    checks: list[dict[str, Any]] = []
    visited_urls: set[str] = set()
    parsed_final_urls: set[str] = set()
    child_pages_checked = 0

    for url in source_urls[: args.max_sources_per_company]:
        if url in visited_urls:
            continue
        visited_urls.add(url)
        if args.init_only:
            checks.append({"url": url, "status": "not_checked"})
            continue
        status_code, body, final_url, error = fetch_url(url, args.timeout)
        visited_urls.add(final_url)
        duplicate_final_page = final_url in parsed_final_urls
        parsed_final_urls.add(final_url)
        check = {
            "url": url,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "statusCode": status_code,
        }
        if error:
            check["error"] = error
        if body and not duplicate_final_page:
            reports = parse_report_links(final_url, body, current_year)
            check["reportLinksFound"] = len(reports)
            discovered_reports.extend(reports)
            child_checks: list[dict[str, Any]] = []
            for child_url in parse_report_hub_links(final_url, body):
                if child_pages_checked >= args.max_child_pages_per_company or child_url in visited_urls:
                    break
                visited_urls.add(child_url)
                child_pages_checked += 1
                child_status, child_body, child_final_url, child_error = fetch_url(child_url, args.timeout)
                visited_urls.add(child_final_url)
                duplicate_child_page = child_final_url in parsed_final_urls
                parsed_final_urls.add(child_final_url)
                child_check: dict[str, Any] = {"url": child_url, "finalUrl": child_final_url, "statusCode": child_status}
                if child_error:
                    child_check["error"] = child_error
                if child_body and not duplicate_child_page:
                    child_reports = parse_report_links(child_final_url, child_body, current_year)
                    child_check["reportLinksFound"] = len(child_reports)
                    discovered_reports.extend(child_reports)
                child_checks.append(child_check)
            if child_checks:
                check["childPages"] = child_checks
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
    parser.add_argument("--max-sources-per-company", type=int, default=10)
    parser.add_argument("--max-child-pages-per-company", type=int, default=3)
    parser.add_argument("--max-companies", type=int, default=0)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--init-only", action="store_true", help="Create source/index files without fetching pages.")
    args = parser.parse_args()

    companies, companies_source = load_companies(args.companies)
    if args.ticker:
        wanted_tickers = set(args.ticker)
        companies = [company for company in companies if company.get("ticker") in wanted_tickers]
    if args.max_companies:
        companies = companies[: args.max_companies]

    sources = build_sources(companies, read_json(args.sources, {"companies": {}}))
    existing_index = load_existing_index(args.index)
    index_companies = existing_index.setdefault("companies", {})
    total_new_reports = 0

    work: list[tuple[str, dict[str, Any]]] = []
    for company in companies:
        slug = company.get("slug") or slugify(company.get("companyName") or company.get("ticker") or "")
        if slug:
            work.append((slug, company))

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                monitor_company,
                company,
                sources["companies"].get(slug, {}),
                index_companies.get(slug, {}),
                args,
            ): (slug, company)
            for slug, company in work
        }
        for completed, future in enumerate(as_completed(futures), 1):
            slug, company = futures[future]
            try:
                record, new_count = future.result()
            except Exception as error:
                record = {
                    "slug": slug,
                    "companyName": company.get("companyName") or company.get("name"),
                    "ticker": company.get("ticker"),
                    "domain": clean_domain(company.get("domain")),
                    "status": "monitor_error",
                    "latestReportYear": None,
                    "sources": [],
                    "reports": index_companies.get(slug, {}).get("reports") or [],
                    "error": f"{type(error).__name__}: {error}",
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
                new_count = 0
            index_companies[slug] = record
            total_new_reports += new_count
            print(
                f"[{completed}/{len(work)}] {record.get('ticker')}: "
                f"{record.get('status')} ({len(record.get('reports') or [])} reports)",
                flush=True,
            )

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
    print(f"Updated {display_path(args.sources)} for {len(sources['companies'])} companies")
    print(f"Updated {display_path(args.index)}; new reports detected: {total_new_reports}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
