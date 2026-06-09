"""
StockLayer event timeline generator.

This script creates baseline event feeds for companies that do not yet have an
events/{slug}-events.json file.

It is deliberately rules/template-based:
- No API key required
- No cost
- Repeatable output
- Safe to run in GitHub Actions
- Does not overwrite curated Cisco-style event timelines by default

What it does:
- Reads companies.json
- Reads companies/{slug}.json where available
- Reads leadership/{slug}-ceo.json where available
- Reads acquisitions/{slug}-acquisitions.json where available
- Reads history/{slug}-history.json where available
- Creates events/{slug}-events.json if missing
- Skips existing event files unless --force is used
- Preserves Cisco by default

Usage:
    python automation/generate_events.py

Force overwrite existing generated event files:
    python automation/generate_events.py --force

Overwrite everything except Cisco:
    python automation/generate_events.py --force --preserve cisco

Generate for one company only:
    python automation/generate_events.py --only microsoft
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
COMPANIES_INDEX = ROOT / "companies.json"
COMPANIES_DIR = ROOT / "companies"
EVENTS_DIR = ROOT / "events"
LEADERSHIP_DIR = ROOT / "leadership"
ACQUISITIONS_DIR = ROOT / "acquisitions"
HISTORY_DIR = ROOT / "history"


DEFAULT_PRESERVE_SLUGS = {"cisco"}


SECTOR_EVENT_LESSONS = {
    "Technology": "Technology leaders are often judged by whether they can keep turning product relevance, platform scale and innovation into durable growth.",
    "Communication Services": "Platform companies are often judged by whether user engagement, regulation and monetisation remain supportive over time.",
    "Consumer Cyclical": "Consumer-facing companies are highly sensitive to brand strength, spending cycles and execution during periods of changing demand.",
    "Consumer Defensive": "Defensive consumer companies are judged by resilience, pricing power and their ability to sustain demand through different economic cycles.",
    "Financial Services": "Financial companies are judged by confidence, regulation, credit quality, interest rates and the durability of returns through cycles.",
    "Healthcare": "Healthcare companies are judged by regulation, innovation, product durability and the ability to convert demand into long-term earnings.",
    "Energy": "Energy companies are judged by commodity cycles, capital discipline, geopolitical risk and the durability of cash generation.",
    "Industrials": "Industrial companies are judged by capital cycles, operational execution, supply chains and long-term infrastructure demand.",
}


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback

    text = path.read_text(encoding="utf-8").strip()

    if not text:
        return fallback

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"WARNING: Invalid JSON skipped: {path}")
        return fallback


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None

    text = str(value).strip()

    formats = [
        "%Y-%m-%d",
        "%B %Y",
        "%b %Y",
        "%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    # Handle strings like "July 2015"
    try:
        return datetime.strptime(text, "%B %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def date_to_string(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def company_file(slug: str) -> Path:
    return COMPANIES_DIR / f"{slug}.json"


def events_file(slug: str) -> Path:
    return EVENTS_DIR / f"{slug}-events.json"


def leadership_file(slug: str) -> Path:
    return LEADERSHIP_DIR / f"{slug}-ceo.json"


def acquisitions_file(slug: str) -> Path:
    return ACQUISITIONS_DIR / f"{slug}-acquisitions.json"


def history_file(slug: str) -> Path:
    return HISTORY_DIR / f"{slug}-history.json"


def merge_company_data(index_entry: Dict[str, Any]) -> Dict[str, Any]:
    slug = index_entry.get("slug")

    if not slug:
        return index_entry

    detail = read_json(company_file(slug), {})
    return {**index_entry, **detail}


def normalise_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    date = event.get("date")

    if not date:
        return None

    return {
        "date": str(date),
        "type": event.get("type") or "company_event",
        "title": event.get("title") or "Company event",
        "summary": event.get("summary") or "",
        "stockLayerLesson": event.get("stockLayerLesson") or "",
    }


def dedupe_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []

    for event in events:
        key = (
            event.get("date"),
            event.get("type"),
            event.get("title"),
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(event)

    return deduped


def sort_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(events, key=lambda item: item.get("date", ""))


def build_baseline_event(company: Dict[str, Any]) -> Dict[str, Any]:
    name = company.get("companyName") or company.get("ticker") or "Company"
    sector = company.get("sector") or "Unknown"
    last_updated = company.get("lastUpdated")

    parsed = parse_date(last_updated)
    date = date_to_string(parsed) if parsed else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return {
        "date": date,
        "type": "market_data_refresh",
        "title": f"{name} added to StockLayer coverage",
        "summary": f"{name} is now included in StockLayer's market data, risk and company tracking layer.",
        "stockLayerLesson": SECTOR_EVENT_LESSONS.get(
            sector,
            "The key investor question is whether the company can sustain its competitive position and turn it into durable shareholder returns.",
        ),
    }


def build_leadership_event(company: Dict[str, Any], leadership: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not leadership:
        return None

    ceo_name = leadership.get("ceoName")
    ceo_since = leadership.get("ceoSince")
    company_name = company.get("companyName") or company.get("ticker") or "Company"

    if not ceo_name or not ceo_since:
        return None

    parsed = parse_date(ceo_since)

    if not parsed:
        return None

    return {
        "date": date_to_string(parsed),
        "type": "leadership",
        "title": f"{ceo_name} becomes CEO",
        "summary": f"{ceo_name} begins leading {company_name}.",
        "stockLayerLesson": "Leadership changes matter because strategy, capital allocation and execution can materially reshape long-term shareholder returns.",
    }


def build_acquisition_events(company: Dict[str, Any], acquisitions: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = acquisitions.get("majorAcquisitions") if isinstance(acquisitions, dict) else None

    if not isinstance(items, list):
        return []

    company_name = company.get("companyName") or company.get("ticker") or "Company"
    events = []

    for acquisition in items:
        acquisition_name = acquisition.get("name")
        acquisition_date = acquisition.get("acquisitionDate") or acquisition.get("date")

        if not acquisition_name or not acquisition_date:
            continue

        parsed = parse_date(acquisition_date)

        if not parsed:
            continue

        value = acquisition.get("value")
        value_text = f" for {value}" if value else ""

        events.append(
            {
                "date": date_to_string(parsed),
                "type": "acquisition",
                "title": f"{company_name} acquires {acquisition_name}",
                "summary": f"{company_name} acquired {acquisition_name}{value_text}.",
                "stockLayerLesson": "Acquisitions can reshape the investment case, but the key question is whether the deal improves growth, resilience or strategic positioning.",
            }
        )

    return events


def build_best_price_event(company: Dict[str, Any], history: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    prices = history.get("prices") if isinstance(history, dict) else None

    if not isinstance(prices, list) or not prices:
        return None

    best = None

    for item in prices:
        try:
            close = float(item.get("close"))
        except (TypeError, ValueError):
            continue

        date = item.get("date")

        if not date:
            continue

        if best is None or close > best["close"]:
            best = {"date": date, "close": close}

    if not best:
        return None

    company_name = company.get("companyName") or company.get("ticker") or "Company"
    currency = company.get("currency") or history.get("currency") or "USD"

    return {
        "date": best["date"],
        "type": "market_peak",
        "title": f"{company_name} reaches best available historical close",
        "summary": f"{company_name} reached its best available historical closing price of {currency} {best['close']:.2f}.",
        "stockLayerLesson": "A best-ever price can signal strong momentum, but future returns still depend on valuation, earnings delivery and expectations.",
    }


def build_events(company: Dict[str, Any]) -> Dict[str, Any]:
    slug = company.get("slug")

    leadership = read_json(leadership_file(slug), {}) if slug else {}
    acquisitions = read_json(acquisitions_file(slug), {}) if slug else {}
    history = read_json(history_file(slug), {}) if slug else {}

    events: List[Dict[str, Any]] = []

    events.append(build_baseline_event(company))

    leadership_event = build_leadership_event(company, leadership)
    if leadership_event:
        events.append(leadership_event)

    events.extend(build_acquisition_events(company, acquisitions))

    best_price_event = build_best_price_event(company, history)
    if best_price_event:
        events.append(best_price_event)

    normalised = []

    for event in events:
        clean = normalise_event(event)
        if clean:
            normalised.append(clean)

    return {
        "generatedBy": "StockLayer event timeline generator",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "events": sort_events(dedupe_events(normalised)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate baseline StockLayer event feeds.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing event files, except preserved slugs.",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Generate one company only by slug.",
    )
    parser.add_argument(
        "--preserve",
        nargs="*",
        default=sorted(DEFAULT_PRESERVE_SLUGS),
        help="Slugs to preserve even when --force is used. Defaults to cisco.",
    )
    parser.add_argument(
        "--include-preserved",
        action="store_true",
        help="Allow preserved slugs to be generated/overwritten.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    companies = read_json(COMPANIES_INDEX, [])

    if not isinstance(companies, list) or not companies:
        raise RuntimeError("companies.json is missing or does not contain a company list")

    EVENTS_DIR.mkdir(exist_ok=True)

    preserve_slugs = set(args.preserve or [])

    created = 0
    skipped = 0

    for index_entry in companies:
        slug = index_entry.get("slug")

        if not slug:
            print(f"Skipping company with missing slug: {index_entry}")
            skipped += 1
            continue

        if args.only and slug != args.only:
            skipped += 1
            continue

        output_path = events_file(slug)

        if slug in preserve_slugs and not args.include_preserved:
            print(f"Preserving curated events file: {slug}")
            skipped += 1
            continue

        if output_path.exists() and not args.force:
            print(f"Events file already exists, skipped: {slug}")
            skipped += 1
            continue

        company = merge_company_data(index_entry)
        events = build_events(company)

        write_json(output_path, events)
        print(f"Wrote events file: {output_path.relative_to(ROOT)}")
        created += 1

    print(f"StockLayer event generation complete. Created: {created}. Skipped: {skipped}.")


if __name__ == "__main__":
    main()
