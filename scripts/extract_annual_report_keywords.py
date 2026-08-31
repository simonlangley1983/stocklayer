#!/usr/bin/env python3
"""Download genuine annual reports and extract auditable keyword counts.

The input is the report index produced by ``monitor_annual_reports.py``.  One
best full-report candidate is selected for every company/report-year pair.
PDFs are parsed with pypdf; digital HTML reports are used only when no PDF can
be discovered from the report page.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
LOCAL_INDEX = ROOT / "annual-reports" / "reports-index.json"
DEFAULT_INDEX_URL = (
    "https://raw.githubusercontent.com/simonlangley1983/stocklayer/"
    "main/annual-reports/reports-index.json"
)
DEFAULT_INDEX = str(LOCAL_INDEX if LOCAL_INDEX.exists() else DEFAULT_INDEX_URL)
DEFAULT_OUTPUT = ROOT / "annual-reports" / "extracted-keywords.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
)
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024

KEYWORD_PATTERNS: dict[str, re.Pattern[str]] = {
    "ai_mentions": re.compile(r"\b(?:AI|artificial intelligence)\b", re.IGNORECASE),
    "cloud_mentions": re.compile(r"\bcloud(?:s|\s+computing)?\b", re.IGNORECASE),
    "cybersecurity_mentions": re.compile(r"\bcyber[\s-]?secur(?:ity|ities)\b", re.IGNORECASE),
    "regulation_mentions": re.compile(r"\bregulat(?:e|ed|es|ing|ion|ions|ory)\b", re.IGNORECASE),
    "competition_mentions": re.compile(r"\bcompet(?:e|ed|es|ing|ition|itions|itive|itively|itor|itors)\b", re.IGNORECASE),
    "supply_chain_mentions": re.compile(r"\bsupply[\s-]+chains?\b", re.IGNORECASE),
    "china_mentions": re.compile(r"\b(?:China|Chinese)\b", re.IGNORECASE),
    "margin_pressure_mentions": re.compile(r"\bmargin[\s-]+pressures?\b", re.IGNORECASE),
    "restructuring_mentions": re.compile(r"\brestructur(?:e|ed|es|ing)\b", re.IGNORECASE),
    "data_center_mentions": re.compile(r"\bdata[\s-]+cent(?:er|re)s?\b", re.IGNORECASE),
}

# Official full-report links that the monitor's first crawl missed because the
# issuer page renders its download controls client-side.
FULL_REPORT_OVERRIDES: dict[tuple[str, int], str] = {
    (
        "AAF.L",
        2023,
    ): "https://cdn-webportal.airtelstream.net/website/investor/main/pdf/annual-report/Airtel_Africa_Annual_Report_FY_2022_2023.pdf",
    (
        "FRES.L",
        2024,
    ): "https://www.fresnilloplc.com/media/zgcbodxt/46566-fresnillo-ar24-web.pdf",
    (
        "ICG.L",
        2025,
    ): "https://www.icgam.com/wp-content/uploads/2025/06/ICG_AR2025_Interactive_Final.pdf",
    (
        "ICG.L",
        2021,
    ): "https://www.icgam.com/wp-content/uploads/2022/02/ICGAnnualReport-Complete_2021.pdf",
    (
        "ICG.L",
        2020,
    ): "https://www.icgam.com/wp-content/uploads/2022/02/ICGAnnualReport-Complete-2020.pdf",
    (
        "ICG.L",
        2019,
    ): "https://www.icgam.com/wp-content/uploads/2022/02/ICGAnnualReportAccounts2019.pdf",
    (
        "LLOY.L",
        2022,
    ): "https://www.lloydsbankinggroup.com/assets/pdfs/investors/financial-performance/lloyds-banking-group-plc/2022/full-year/2022-lbg-annual-report.pdf",
    (
        "BARC.L",
        2025,
    ): "https://home.barclays/content/dam/home-barclays/documents/investor-relations/reports-and-events/annual-reports/2025/Barclays-PLC-Annual-Report-2025.pdf",
    (
        "MKS.L",
        2026,
    ): "https://corporate.marksandspencer.com/sites/marksandspencer/files/marksandspencer/annual-report/m-and-s-annual-report-and-financial-statements-2026.pdf",
    (
        "AAL.L",
        2025,
    ): "https://www.angloamerican.com/~/media/Files/A/Anglo-American-Group-v9/PLC/investors/annual-reporting/2025/aa-annual-report-full-2025.pdf",
    (
        "AZN.L",
        2025,
    ): "https://www.astrazeneca.com/content/dam/az/Investor_Relations/annual-report-2025/pdf/AstraZeneca_AR_2025.pdf",
    (
        "BATS.L",
        2025,
    ): "https://www.bat.com/content/dam/batcom/global/main-nav/investors-and-reporting/reporting/combined-annual-and-sustainability-report/BAT_Annual_Report_2025.pdf",
    (
        "BTRW.L",
        2025,
    ): "https://www.barrattredrow.co.uk/~/media/Files/B/Barratt-Developments-V2/documents/annual-report-2025/barratt-redrow-plc-annual-report-and-accounts-2025.pdf",
    (
        "HSBA.L",
        2025,
    ): "https://www.hsbc.com/-/files/hsbc/investors/hsbc-results/2025/annual/pdfs/hsbc-holdings-plc/260225-annual-report-and-accounts-2025.pdf?download=1",
    (
        "IHG.L",
        2025,
    ): "https://www.ihgplc.com/~/media/Files/I/Ihg-Plc/investors/annual-report/2025/ihg-ar25-interactive.pdf",
    (
        "SDR.L",
        2025,
    ): "https://mybrand.schroders.com/m/74f3d9a5f99e1565/original/Schroders-Annual-Report-FY25-Full-report-interactive.pdf",
    (
        "SPX.L",
        2025,
    ): "https://content.spiraxgroup.com/-/media/engineering/documents/results-and-agm-notices/2025/ara/spirax-group-plc-annual-report-2025.ashx?rev=4264817ad6cb42e7b009ed3640dc1b22&hash=A7FAE47E7BC21AA3BA71D088BCA0A28D",
    (
        "UTG.L",
        2025,
    ): "https://www.unitegroup.com/wp-content/uploads/2026/05/UNITE_AR25-WEB.pdf",
    (
        "VOD.L",
        2025,
    ): "https://www.vodafone.com/~/media/Files/V/vodafone/corp/documents/performance/financial-results/2025/form-20-f-2025.pdf",
    ("SHEL.L", 2025): "https://www.shell.com/investors/results-and-reporting/annual-report-archive/_jcr_content/root/main/section_812377294/tabs/tab_copy_copy/text.multi.stream/1774544186011/5727c329a58b5eb7a54442c0a03f562a5aef1159/shell-annual-report-2025-interactive.pdf",
    ("ULVR.L", 2025): "https://www.unilever.com/files/unilever-annual-report-and-accounts-2025.pdf",
    ("ADM.L", 2025): "https://www.admiralgroup.co.uk/static-files/b4032ccb-738f-454d-92a1-ec2e95677423",
    ("ABF.L", 2025): "https://www.abf.co.uk/content/dam/abf/corporate/Documents/investors/annual-and-interim-reports/2025/abf-annual-report-2025.pdf.downloadasset.pdf",
    ("BA.L", 2025): "https://data.fca.org.uk/artefacts/NSM/DirectUpload/NI-000141487/reports/8SVCSVKSGDWMW2QHOH83-2025-12-31-T01_ixbrl.html",
    ("CCH.L", 2025): "https://www.coca-colahellenic.com/content/dam/cch/us/documents/oar2025/549300EFP3TNG7JGVE49-2025-12-31-T01.xhtml.downloadasset.xhtml",
    ("CPG.L", 2025): "https://www.compass-group.com/content/dam/compass-group/corporate/oar-2025/oar-page/annual-report-2025.pdf",
    ("CTEC.L", 2025): "https://data.fca.org.uk/artefacts/NSM/DirectUpload/NI-000140472/NI-000140472.pdf",
    ("HLN.L", 2025): "https://www.haleon.com/content/dam/haleon/corporate/documents/investors/oar-2025/Annual-Report-and-Form-20-F-2025.pdf.downloadasset.pdf",
    ("HL.L", 2025): "https://www.hl.co.uk/__data/assets/pdf_file/0006/20662503/2025-Report-and-Financial-Statements.pdf",
    ("HSX.L", 2025): "https://www.hiscoxgroup.com/sites/group/files/2026-03/Hiscox_report_and_accounts_2025.pdf",
    ("IMI.L", 2025): "https://data.fca.org.uk/artefacts/NSM/DirectUpload/NI-000142148/reports/2138002W9Q21PF751R30-2025-12-31-T01_ixbrl.html",
    ("IMB.L", 2025): "https://www.imperialbrandsplc.com/content/dam/imperialbrands/corporate/documents/investor-hub/reports/oar-2025/imperial-brands-2025-annual-report.pdf",
    ("ITRK.L", 2025): "https://www.intertek.com/siteassets/investors/2026/ara/intertek-annual-report--accounts-2025-combined.pdf",
    ("MRO.L", 2025): "https://www.melroseplc.net/media/qc0eu5o1/melrose-annual-report25.pdf",
    ("MNDI.L", 2025): "https://www.mondigroup.com/globalassets/mondigroup.com/investors/results-reports-and-presentations/2025/integrated-report/mondi-group-integrated-report-and-financial-statements-jaws-accessibility-2025.pdf",
    ("PSH.L", 2025): "https://assets.pershingsquareholdings.com/wp-content/uploads/2026/02/18175039/Pershing-Square-Holdings-Ltd.-2025-Annual-Report.pdf",
    ("PRU.L", 2025): "https://www.prudentialplc.com/content/dam/prudential-plc/investor/results-and-reports/lastest-annual-report/prudential-plc-ar-2025.pdf.coredownload.inline.pdf",
    ("RMV.L", 2025): "https://plc.rightmove.co.uk/content/uploads/2026/03/RIG004-V2-2025-ARA-WEB-READY-260323.pdf",
    ("SGE.L", 2025): "https://data.fca.org.uk/artefacts/NSM/DirectUpload/NI-000134916/reports/2138005RN5XYLTF8G138-2025-09-30-T01_ixbrl.html",
    ("SMT.L", 2026): "https://www.scottishmortgage.com/api/document-download?bg-url=%2Fen%2Fuk%2Findividual-investors%2Fliterature-library%2Ffunds%2Finvestment-trusts%2Fscottish-mortgage%2Fannual-reports%2Fscottish-mortgage-annual-report-including-the-notice-of-agm-march-2026%2F",
    ("SVT.L", 2026): "https://www.severntrent.com/content/dam/stw-plc/fy-results-26/full-annual-report-and-accounts-2026.pdf",
    ("SN.L", 2025): "https://smith-nephew.stylelabs.cloud/api/public/content/9bf6ddda064f489c852f0ecab120f281?v=273de580&download=true",
    ("STJ.L", 2025): "https://www.sjp.co.uk/sites/sjp-corp/files/SJP/shareholders/reports-presentations-webcasts/2026/SJP_AR_2025.pdf",
}

REFERER_OVERRIDES = {
    "static.aviva.io": "https://www.aviva.com/investors/annual-report/",
    "www.abf.co.uk": "https://www.abf.co.uk/investors/results-reports-presentations/annual-reports",
    "www.coca-colahellenic.com": "https://www.coca-colahellenic.com/en/investor-relations/2025-integrated-annual-report",
    "www.compass-group.com": "https://www.compass-group.com/en/investors/annual-reports.html",
    "www.convatecgroup.com": "https://www.convatecgroup.com/investors/reports-results-and-presentations/",
    "smith-nephew.stylelabs.cloud": "https://www.smith-nephew.com/en/who-we-are/investors/annual-report-2025",
    "www.sage.com": "https://www.sage.com/investors/financial-information/annual-report/",
}


class LinkAndTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "noscript"}:
            self._ignored_depth += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self.text.append(data)
        if self._href:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, normalise_text(" ".join(self._anchor_text))))
            self._href = None
            self._anchor_text = []


def normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def request_bytes(url: str, timeout: int) -> tuple[bytes, str, str]:
    parsed_url = urllib.parse.urlparse(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    referer = REFERER_OVERRIDES.get(parsed_url.netloc.lower())
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.URLError as error:
        # A small number of issuer CDNs present an incomplete/self-signed chain
        # to Python while browsers accept the same public document. Retry only
        # that certificate-specific failure; the SHA-256 retained in the output
        # still makes the downloaded source auditable.
        if not isinstance(error.reason, ssl.SSLCertVerificationError):
            raise
        response = urllib.request.urlopen(
            request,
            timeout=timeout,
            context=ssl._create_unverified_context(),  # noqa: SLF001
        )
    with response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"download exceeds {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limit")
        body = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(body) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"download exceeds {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limit")
        return body, response.headers.get("Content-Type", ""), response.geturl()


def request_bytes_browser_compatible(url: str, timeout: int) -> tuple[bytes, str, str]:
    """Retry issuer CDNs that require a current browser TLS fingerprint."""
    from curl_cffi import requests as curl_requests

    response = curl_requests.get(
        url,
        headers={"Accept": "application/pdf,text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
        impersonate="chrome",
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    body = bytes(response.content)
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"download exceeds {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limit")
    return body, response.headers.get("Content-Type", ""), str(response.url)


def read_index(location: str, timeout: int) -> dict[str, Any]:
    if location.startswith(("http://", "https://")):
        body, _, _ = request_bytes(location, timeout)
        return json.loads(body.decode("utf-8"))
    return json.loads(Path(location).read_text(encoding="utf-8"))


def unwrap_pdf_viewer(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    src = query.get("src", [""])[0]
    if src.lower().endswith(".pdf"):
        return urllib.parse.urljoin(url, src)
    return url.split("#", 1)[0]


def candidate_score(report: dict[str, Any]) -> int:
    title = str(report.get("title") or "").lower()
    url = unwrap_pdf_viewer(str(report.get("url") or ""))
    path = urllib.parse.urlparse(url).path.lower()
    filename = urllib.parse.unquote(Path(path).name)
    # Parent folders are commonly named "annual-reporting", which must not
    # make every unrelated PDF in the archive look like a full annual report.
    haystack = f"{title} {filename}"
    score = 0
    if path.endswith(".pdf") or path.endswith(".ashx"):
        score += 100
    if "annual report" in haystack or "annual-report" in haystack:
        score += 35
    if "full report" in haystack or "full-report" in haystack or "annual-report-full" in haystack:
        score += 20
    if "accounts" in haystack:
        score += 8
    if "download" in title or "interactive pdf" in title or "black & white pdf" in title:
        score += 8
    for bad in (
        "xbrl", "esef", ".zip", "climate", "sustainability", "strategic-report",
        "financial-statements", "financial statements", "remuneration", "data_report",
        "factsheet", "chinese", "glossary", "pillar-3", "payments-to-government", "section",
        "modern-slavery", "modern slavery", "pay-gap", "country-snapshot", "esg report", "esg-report",
        "sainsburys-bank", "sainsbury's bank", "sainsburys bank",
        "notice-of-annual-general-meeting", "notice of annual general meeting", "notice-of-agm",
        "tax-strategy", "tax strategy", "risk-supplement", "risk supplement", "g-sii",
        "fair-pay", "fair pay", "mandatory-information", "mandatory information",
        "de-beers-uk-limited", "de beers uk limited",
        "governance-report", "governance report", "tax-and-economic", "tax and economic",
        "ore-reserves", "ore reserves", "mineral-resources", "mineral resources",
        "financial-statements", "financial statements", "chairs-statement", "chair's statement",
        "ceo-review", "ceo review", "at-a-glance", "at a glance", "development-pipeline",
        "development pipeline", "patent-expiries", "patent expiries", "notice-of-meeting",
        "notice of meeting", "country-by-country", "country by country",
    ):
        if bad in haystack:
            score -= 60
    if "#page=" in str(report.get("url") or ""):
        score -= 4
    return score


def is_known_non_report_candidate(report: dict[str, Any]) -> bool:
    haystack = f"{report.get('title') or ''} {report.get('url') or ''}".lower()
    normalised = re.sub(r"[_-]+", " ", haystack)
    if any(marker in normalised for marker in (
        "annual report", "report and financial statements", "integrated report",
    )) and not any(marker in normalised for marker in ("mockup", "test images")):
        return False
    return any(marker in haystack or re.sub(r"[_-]+", " ", marker) in normalised for marker in (
        "test-images", "annual-report-mockup", "mockup-annual-report",
        "payments-to-government", "de-beers-inc-estma", "ihg_data_report",
        "modern-slavery", "modern slavery", "pay-gap", "country-snapshot",
        "sainsburys-bank", "sainsbury's bank", "sainsburys bank", "download esg report", "esg-report",
        "notice-of-annual-general-meeting", "notice of annual general meeting", "notice-of-agm",
        "tax-strategy", "tax strategy", "risk-supplement", "risk supplement", "g-sii",
        "fair-pay", "fair pay", "mandatory-information", "mandatory information",
        "de-beers-uk-limited", "de beers uk limited",
        "governance-report", "governance report", "tax-and-economic", "tax and economic",
        "ore-reserves", "ore reserves", "mineral-resources", "mineral resources",
        "financial-statements", "financial statements", "chairs-statement", "chair's statement",
        "ceo-review", "ceo review", "at-a-glance", "at a glance", "development-pipeline",
        "development pipeline", "patent-expiries", "patent expiries", "notice-of-meeting",
        "notice of meeting", "country-by-country", "country by country",
        "strategic-report", "strategic report",
    ))


def infer_document_year(title: str | None, url: str | None, fallback: int) -> int:
    path = urllib.parse.unquote(urllib.parse.urlparse(str(url or "")).path)
    haystack = f"{title or ''} {Path(path).name}".lower().replace("_", " ")
    fiscal_range = re.search(r"\b(20\d{2})\s*[-/]\s*(?:20)?(\d{2})\b", haystack)
    if fiscal_range:
        first = int(fiscal_range.group(1))
        second = int(fiscal_range.group(2))
        if second < 100:
            second = (first // 100) * 100 + second
        if first <= second <= first + 1:
            return second
    patterns = (
        r"\b(20\d{2})\b[^\n]{0,35}\b(?:integrated\s+)?annual[\s-]+report\b",
        r"\b(?:integrated\s+)?annual[\s-]+report\b[^\n]{0,35}\b(20\d{2})\b",
        r"\b(?:fy|ar|ara)[\s-]?(20\d{2}|\d{2})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, haystack)
        if match:
            year = int(match.group(1))
            return 2000 + year if year < 100 else year
    return fallback


def pdf_link_candidates(page_url: str, html: str) -> list[dict[str, Any]]:
    parser = LinkAndTextParser()
    parser.feed(html)
    candidates: list[dict[str, Any]] = []
    for href, title in parser.links:
        url = unwrap_pdf_viewer(urllib.parse.urljoin(page_url, href))
        path = urllib.parse.urlparse(url).path.lower()
        if path.endswith((".pdf", ".ashx")) or ".pdf" in url.lower():
            candidate = {"title": title, "url": url}
            if not is_known_non_report_candidate(candidate):
                candidates.append(candidate)
    return sorted(candidates, key=candidate_score, reverse=True)


def extract_pdf(body: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(body), strict=False)
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages), len(reader.pages)


def extract_html(body: bytes, content_type: str) -> tuple[str, LinkAndTextParser]:
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    encoding = match.group(1).strip('"\'') if match else "utf-8"
    html = body.decode(encoding, errors="replace")
    parser = LinkAndTextParser()
    parser.feed(html)
    return normalise_text(" ".join(parser.text)), parser


def keyword_counts(text: str) -> dict[str, int]:
    return {name: len(pattern.findall(text)) for name, pattern in KEYWORD_PATTERNS.items()}


def extract_group(company: dict[str, Any], year: int, reports: list[dict[str, Any]], timeout: int) -> dict[str, Any]:
    attempts: list[dict[str, str]] = []
    queue = sorted(reports, key=candidate_score, reverse=True)
    override = FULL_REPORT_OVERRIDES.get((str(company.get("ticker") or ""), year))
    if override:
        queue.insert(0, {"title": f"Official full annual report {year}", "url": override})
    visited: set[str] = set()
    while queue and len(visited) < 8:
        candidate = queue.pop(0)
        requested_url = unwrap_pdf_viewer(str(candidate.get("url") or ""))
        if not requested_url or requested_url in visited:
            continue
        visited.add(requested_url)
        try:
            try:
                body, content_type, final_url = request_bytes(requested_url, timeout)
            except Exception:
                body, content_type, final_url = request_bytes_browser_compatible(requested_url, timeout)
            is_pdf = body.startswith(b"%PDF-") or "application/pdf" in content_type.lower()
            if is_pdf:
                text, pages = extract_pdf(body)
                text = normalise_text(text)
                word_count = len(re.findall(r"\b\w+\b", text))
                if pages < 50 or word_count < 20_000:
                    raise ValueError(
                        f"document is too short to be a full annual report ({pages} pages, {word_count} words)"
                    )
                return build_record(company, year, candidate, final_url, "pdf", pages, body, text, attempts)

            if "text/plain" in content_type.lower():
                html_text = normalise_text(body.decode("utf-8", errors="replace"))
            else:
                html_text, _ = extract_html(body, content_type)
            word_count = len(re.findall(r"\b\w+\b", html_text))
            is_digital_report = urllib.parse.urlparse(requested_url).path.lower().endswith((".html", ".xhtml"))
            if word_count >= 20_000 and is_digital_report:
                source_url = str(candidate.get("sourceUrl") or final_url)
                return build_record(
                    company, year, candidate, source_url,
                    "html", None, body, html_text, attempts,
                )
            discovered = pdf_link_candidates(final_url, body.decode("utf-8", errors="replace"))
            queue.extend(item for item in discovered[:8] if item["url"] not in visited)
            queue.sort(key=candidate_score, reverse=True)
            attempts.append({"url": requested_url, "error": "report page did not expose a usable full report"})
        except Exception as error:
            attempts.append({"url": requested_url, "error": f"{type(error).__name__}: {error}"})

    return {
        "company_name": company.get("companyName"),
        "ticker": company.get("ticker"),
        "company_slug": company.get("slug"),
        "report_year": year,
        "report_data_status": "extraction_failed",
        "attempts": attempts,
    }


def build_record(
    company: dict[str, Any],
    year: int,
    candidate: dict[str, Any],
    final_url: str,
    document_format: str,
    page_count: int | None,
    source_bytes: bytes,
    text: str,
    attempts: list[dict[str, str]],
) -> dict[str, Any]:
    document_year = infer_document_year(candidate.get("title"), final_url, year)
    return {
        "company_name": company.get("companyName"),
        "ticker": company.get("ticker"),
        "company_slug": company.get("slug"),
        "report_year": document_year,
        "index_group_year": year,
        "report_title": candidate.get("title"),
        "report_url": final_url,
        "report_source_url": candidate.get("sourceUrl"),
        "report_data_status": "extracted",
        "document_format": document_format,
        "page_count": page_count,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "extracted_word_count": len(re.findall(r"\b\w+\b", text)),
        "keyword_count_method": "case-insensitive keyword-family regex over extracted full-report text",
        **keyword_counts(text),
        "attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--merge-input", type=Path, action="append", default=[])
    parser.add_argument(
        "--exclude-input",
        type=Path,
        action="append",
        default=[],
        help="Skip ticker/report-year pairs already present as extracted rows in these JSON files.",
    )
    parser.add_argument("--latest-only", action="store_true")
    parser.add_argument("--skip-groups", type=int, default=0)
    parser.add_argument("--max-groups", type=int, default=0)
    args = parser.parse_args()

    if args.merge_input:
        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in args.merge_input]
        reports_by_company_year: dict[tuple[str, int], dict[str, Any]] = {}
        for payload in payloads:
            for report in payload.get("reports") or []:
                if report.get("report_data_status") != "extracted":
                    continue
                report["report_year"] = infer_document_year(
                    report.get("report_title"), report.get("report_url"), int(report.get("report_year") or 0)
                )
                ticker = str(report.get("ticker") or "")
                year = int(report.get("report_year") or 0)
                if ticker and year:
                    reports_by_company_year[(ticker, year)] = report
        reports = sorted(
            reports_by_company_year.values(),
            key=lambda item: (item.get("ticker") or "", -(item.get("report_year") or 0)),
        )
        if args.latest_only:
            latest_by_ticker: dict[str, dict[str, Any]] = {}
            for report in reports:
                latest_by_ticker.setdefault(str(report.get("ticker") or ""), report)
            reports = list(latest_by_ticker.values())
        extracted = sum(item.get("report_data_status") == "extracted" for item in reports)
        merged_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_index": "annual-reports/reports-index.json" if str(args.index) == str(LOCAL_INDEX) else str(args.index),
            "source_index_generated_at": payloads[-1].get("source_index_generated_at") if payloads else None,
            "methodology": payloads[0].get("methodology") if payloads else {},
            "group_count": len(reports),
            "extracted_count": extracted,
            "failed_count": len(reports) - extracted,
            "reports": reports,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(merged_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Merged {len(args.merge_input)} extraction files into {args.output} ({extracted}/{len(reports)} extracted)")
        return 0 if extracted else 1

    excluded_company_years: set[tuple[str, int]] = set()
    for path in args.exclude_input:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for report in payload.get("reports") or []:
            if report.get("report_data_status") != "extracted":
                continue
            ticker = str(report.get("ticker") or "")
            year = int(report.get("report_year") or 0)
            if ticker and year:
                excluded_company_years.add((ticker, year))

    index = read_index(args.index, args.timeout)
    groups: list[tuple[dict[str, Any], int, list[dict[str, Any]]]] = []
    for company in index.get("companies", {}).values():
        if args.ticker and company.get("ticker") not in set(args.ticker):
            continue
        by_year: dict[int, list[dict[str, Any]]] = {}
        for report in company.get("reports") or []:
            if is_known_non_report_candidate(report):
                continue
            year = report.get("year")
            if isinstance(year, int):
                by_year.setdefault(year, []).append(report)
        years = sorted(by_year, reverse=True)
        if args.latest_only:
            years = years[:1]
        for year in years:
            if (str(company.get("ticker") or ""), year) in excluded_company_years:
                continue
            groups.append((company, year, by_year[year]))
    groups.sort(key=lambda item: (item[0].get("ticker") or "", -item[1]))
    if args.skip_groups:
        groups = groups[args.skip_groups :]
    if args.max_groups:
        groups = groups[: args.max_groups]

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(extract_group, company, year, reports, args.timeout): (company, year)
            for company, year, reports in groups
        }
        for completed, future in enumerate(as_completed(futures), 1):
            company, year = futures[future]
            try:
                record = future.result()
            except Exception as error:
                record = {
                    "company_name": company.get("companyName"),
                    "ticker": company.get("ticker"),
                    "company_slug": company.get("slug"),
                    "report_year": year,
                    "report_data_status": "extraction_failed",
                    "attempts": [{"error": f"{type(error).__name__}: {error}"}],
                }
            results.append(record)
            print(
                f"[{completed}/{len(groups)}] {record.get('ticker')} {year}: "
                f"{record.get('report_data_status')}",
                flush=True,
            )

    results.sort(key=lambda item: (item.get("ticker") or "", -(item.get("report_year") or 0)))
    extracted = sum(item.get("report_data_status") == "extracted" for item in results)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_index": "annual-reports/reports-index.json" if str(args.index) == str(LOCAL_INDEX) else args.index,
        "source_index_generated_at": index.get("generatedAt"),
        "methodology": {
            "unit": "one selected full annual report per company and report year",
            "selection": "prefer a full PDF whose title/URL identifies an annual report; follow report-page PDF links when needed",
            "text_extraction": "pypdf for PDFs; visible HTML text only when no report PDF is exposed",
            "keyword_patterns": {name: pattern.pattern for name, pattern in KEYWORD_PATTERNS.items()},
        },
        "group_count": len(results),
        "extracted_count": extracted,
        "failed_count": len(results) - extracted,
        "reports": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} ({extracted}/{len(results)} reports extracted)")
    return 0 if extracted else 1


if __name__ == "__main__":
    raise SystemExit(main())
