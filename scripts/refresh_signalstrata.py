#!/usr/bin/env python3
"""Refresh static SignalStrata intelligence without exposing API credentials.

The site consumes the generated JSON files. This script is intended for GitHub
Actions and reads SIGNALSTRATA_API_KEY only from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_BASE = "https://api.signalstrata.io/api/v1"
LONDON = ZoneInfo("Europe/London")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, data: Any) -> bool:
    """Write only when changed, using replace so incomplete files are impossible."""
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    temporary.replace(path)
    return True


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalise_ticker(value: Any) -> str:
    return str(value or "").upper().strip().removesuffix(".L")


def valid_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except ValueError:
        return text if len(text) == 4 and text.isdigit() else None


def first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def list_value(record: dict[str, Any], *keys: str) -> list[Any]:
    value = first_value(record, *keys)
    return value if isinstance(value, list) else []


def normalise_evidence(record: dict[str, Any]) -> dict[str, Any]:
    source = first_value(record, "source_document", "sourceDocument", "source", "document")
    return {
        "id": first_value(record, "id", "evidence_id", "evidenceId"),
        "title": first_value(record, "title", "name", "headline"),
        "summary": first_value(record, "summary", "description"),
        "quote": first_value(record, "quote", "quotation", "supporting_quote", "supportingQuote"),
        "date": valid_date(first_value(record, "publication_date", "publicationDate", "event_date", "eventDate", "date")),
        "fiscalYear": first_value(record, "fiscal_year", "fiscalYear", "year"),
        "sourceDocument": source,
        "sourceUrl": first_value(record, "source_url", "sourceUrl", "url"),
        "pageNumber": first_value(record, "page_number", "pageNumber", "page"),
        "activityStage": first_value(record, "activity_stage", "activityStage"),
        "activityType": first_value(record, "activity_type", "activityType"),
    }


def evidence_key(item: dict[str, Any]) -> str:
    if item.get("id"):
        return f"id:{item['id']}"
    return "|".join(str(item.get(field) or "") for field in ("title", "date", "sourceUrl", "sourceDocument"))


def normalise_initiative(record: dict[str, Any], index: int) -> dict[str, Any]:
    evidence = [normalise_evidence(item) for item in list_value(record, "evidence", "evidences", "supporting_evidence", "supportingEvidence") if isinstance(item, dict)]
    return {
        "id": str(first_value(record, "id", "initiative_id", "initiativeId") or f"unidentified-{index}"),
        "name": first_value(record, "name", "initiative_name", "initiativeName", "title") or "Untitled initiative",
        "category": first_value(record, "category", "signal_category", "signalCategory") or "Other",
        "summary": first_value(record, "summary", "description"),
        "confidence": first_value(record, "confidence"),
        "relevancyScore": first_value(record, "relevancy_score", "relevancyScore", "relevance_score", "relevanceScore"),
        "investmentSummary": first_value(record, "investment_summary", "investmentSummary", "investment"),
        "timeline": first_value(record, "timeline"),
        "activityStage": first_value(record, "activity_stage", "activityStage"),
        "activityType": first_value(record, "activity_type", "activityType"),
        "fiscalYears": list_value(record, "fiscal_years", "fiscalYears"),
        "partners": list_value(record, "partners"),
        "products": list_value(record, "products"),
        "platforms": list_value(record, "platforms"),
        "entities": list_value(record, "entities"),
        "evidence": evidence,
    }


def merge_exact_duplicates(initiatives: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only merge matching provider IDs. Similar names are intentionally retained."""
    grouped: dict[str, dict[str, Any]] = {}
    for initiative in initiatives:
        key = initiative["id"]
        if key not in grouped:
            grouped[key] = initiative
            continue
        existing = grouped[key]
        seen = {evidence_key(item) for item in existing["evidence"]}
        existing["evidence"].extend(item for item in initiative["evidence"] if evidence_key(item) not in seen)
    return list(grouped.values())


def extract_initiatives(payload: Any, ticker: str) -> list[dict[str, Any]]:
    """Accept documented and safely-detected response envelopes without guessing data."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    candidate = payload
    for key in ("data", "result", "company"):
        if isinstance(candidate.get(key), dict):
            candidate = candidate[key]
    for key in ("initiatives", "signals", "items", "results"):
        if isinstance(candidate.get(key), list):
            return [item for item in candidate[key] if isinstance(item, dict)]
    ticker_payload = payload.get(ticker) or payload.get(ticker.upper())
    if isinstance(ticker_payload, dict):
        return extract_initiatives(ticker_payload, ticker)
    return []


def latest_evidence_date(initiatives: list[dict[str, Any]]) -> str | None:
    dates = [item["date"] for initiative in initiatives for item in initiative["evidence"] if item.get("date")]
    return max(dates) if dates else None


def request_json(url: str, api_key: str, timeout: int, retries: int) -> Any:
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers={"X-API-Key": api_key, "Accept": "application/json", "User-Agent": "StockLayer SignalStrata refresh"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            retry_after = error.headers.get("Retry-After") if error.headers else None
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == retries:
                raise RuntimeError(f"SignalStrata returned HTTP {error.code}") from error
            delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(60, 2 ** attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            if attempt == retries:
                raise RuntimeError("SignalStrata request failed or returned invalid JSON") from error
            delay = min(60, 2 ** attempt)
        time.sleep(delay)
    raise RuntimeError("SignalStrata request exhausted retries")


def payloads_for_batch(payload: Any, tickers: list[str]) -> dict[str, Any]:
    if isinstance(payload, dict):
        for key in ("companies", "data", "results", "signals"):
            envelope = payload.get(key)
            if isinstance(envelope, dict):
                return {ticker: envelope.get(ticker) or envelope.get(ticker.upper()) or {} for ticker in tickers}
            if isinstance(envelope, list):
                mapped = {normalise_ticker(item.get("ticker")): item for item in envelope if isinstance(item, dict)}
                if mapped:
                    return {ticker: mapped.get(ticker, {}) for ticker in tickers}
        return {ticker: payload.get(ticker) or payload.get(ticker.upper()) or {} for ticker in tickers}
    return {ticker: {} for ticker in tickers}


def company_ticker(company: dict[str, Any], overrides: dict[str, str]) -> str:
    slug = str(company.get("slug") or "")
    return normalise_ticker(overrides.get(slug) or company.get("lseTicker") or company.get("ticker"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies", default="ftse100.json")
    parser.add_argument("--out-dir", default="strategic-intelligence")
    parser.add_argument("--ticker-map", default="strategic-intelligence/ticker-map.json")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--only-london-hour", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.only_london_hour is not None and not args.force and datetime.now(LONDON).hour != args.only_london_hour:
        print("Outside the scheduled London refresh hour. Nothing to do.")
        return 0

    api_key = os.environ.get("SIGNALSTRATA_API_KEY")
    if not api_key:
        print("SIGNALSTRATA_API_KEY is not configured.", file=sys.stderr)
        return 2

    companies = read_json(ROOT / args.companies, [])
    ticker_map = read_json(ROOT / args.ticker_map, {}).get("overrides", {})
    if not isinstance(companies, list):
        raise RuntimeError("Company universe must be a JSON list")

    universe = [(item, company_ticker(item, ticker_map)) for item in companies if isinstance(item, dict)]
    universe = [(company, ticker) for company, ticker in universe if ticker and company.get("slug")]
    out_dir = ROOT / args.out_dir
    successful_tickers: list[str] = []
    failed_tickers: list[str] = []
    latest_dates: list[str] = []
    changed = 0

    for start in range(0, len(universe), max(1, min(args.batch_size, 20))):
        batch = universe[start:start + max(1, min(args.batch_size, 20))]
        tickers = [ticker for _, ticker in batch]
        url = f"{args.api_base.rstrip('/')}/signals?{urllib.parse.urlencode({'tickers': ','.join(tickers)})}"
        try:
            payload = request_json(url, api_key, args.timeout, args.retries)
            by_ticker = payloads_for_batch(payload, tickers)
        except RuntimeError as error:
            print(f"Batch {', '.join(tickers)} failed: {error}", file=sys.stderr)
            failed_tickers.extend(tickers)
            continue

        synchronised = iso_now()
        for company, ticker in batch:
            raw = extract_initiatives(by_ticker.get(ticker, {}), ticker)
            initiatives = merge_exact_duplicates(normalise_initiative(item, index) for index, item in enumerate(raw))
            latest = latest_evidence_date(initiatives)
            document = {
                "schemaVersion": 1,
                "provider": "SignalStrata",
                "slug": company["slug"],
                "ticker": ticker,
                "status": "available",
                "lastSynchronised": synchronised,
                "latestEvidenceDate": latest,
                "initiatives": initiatives,
            }
            changed += int(write_json_atomic(out_dir / f"{company['slug']}.json", document))
            successful_tickers.append(ticker)
            if latest:
                latest_dates.append(latest)

    previous = read_json(out_dir / "metadata.json", {})
    metadata = {
        "schemaVersion": 1,
        "provider": "SignalStrata",
        "lastSuccessfulSynchronisation": iso_now() if successful_tickers else previous.get("lastSuccessfulSynchronisation"),
        "latestEvidenceDate": max(latest_dates) if latest_dates else previous.get("latestEvidenceDate"),
        "companyCount": len(universe),
        "availableCompanyCount": len(successful_tickers),
        "failedTickers": sorted(set(failed_tickers)),
        "notes": "Latest evidence reflects provider evidence dates, not the retrieval date.",
    }
    changed += int(write_json_atomic(out_dir / "metadata.json", metadata))
    print(f"SignalStrata refresh complete: {len(successful_tickers)} succeeded, {len(failed_tickers)} failed, {changed} files changed.")
    return 0 if successful_tickers else 1


if __name__ == "__main__":
    raise SystemExit(main())
