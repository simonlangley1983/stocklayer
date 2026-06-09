"""
StockLayer leadership profile generator.

This script creates baseline leadership feeds for companies that do not yet have
a leadership/{slug}-ceo.json file.

It is deliberately conservative:
- No API key required
- No cost
- Repeatable output
- Safe to run in GitHub Actions
- Does not overwrite curated Cisco-style leadership profiles by default
- Does not invent CEO names, ages or tenure dates

Important:
This first version creates a valid placeholder CEO profile so the website can
render consistently across companies. Proper CEO data can be added later through
a curated or researched enrichment process.

What it does:
- Reads companies.json
- Reads companies/{slug}.json where available
- Creates leadership/{slug}-ceo.json if missing
- Skips existing leadership profiles unless --force is used
- Preserves Cisco by default

Usage:
    python automation/generate_leadership_profiles.py

Force overwrite existing generated leadership profiles:
    python automation/generate_leadership_profiles.py --force

Overwrite everything except Cisco:
    python automation/generate_leadership_profiles.py --force --preserve cisco

Generate for one company only:
    python automation/generate_leadership_profiles.py --only microsoft
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
COMPANIES_INDEX = ROOT / "companies.json"
COMPANIES_DIR = ROOT / "companies"
LEADERSHIP_DIR = ROOT / "leadership"


DEFAULT_PRESERVE_SLUGS = {"cisco"}


SECTOR_WATCH_POINTS = {
    "Technology": [
        "Ability to sustain growth as technology cycles change",
        "Execution on AI, cloud, software or infrastructure strategy",
        "Capital allocation discipline and margin resilience",
        "Whether valuation expectations remain supported by delivery",
    ],
    "Communication Services": [
        "Ability to sustain user engagement and monetisation",
        "Regulatory pressure and platform governance",
        "AI impact on search, content, advertising or discovery",
        "Whether growth remains strong enough to support valuation",
    ],
    "Consumer Cyclical": [
        "Consumer demand and brand resilience",
        "Margin pressure from costs, competition or weaker spending",
        "Execution on product, pricing and distribution strategy",
        "Whether growth remains attractive through the economic cycle",
    ],
    "Consumer Defensive": [
        "Pricing power and demand resilience",
        "Margin management through inflation or cost pressure",
        "Brand strength and distribution execution",
        "Whether defensive qualities justify the valuation",
    ],
    "Financial Services": [
        "Credit quality, regulation and capital strength",
        "Sensitivity to interest rates and market conditions",
        "Execution on digital, payments or platform strategy",
        "Whether returns remain resilient through the cycle",
    ],
    "Healthcare": [
        "Product pipeline, regulation and pricing pressure",
        "Ability to defend margins and sustain innovation",
        "Execution on acquisitions, R&D or portfolio strategy",
        "Whether growth remains resilient over the long term",
    ],
    "Energy": [
        "Commodity price sensitivity and capital discipline",
        "Cash generation through the cycle",
        "Energy transition strategy and regulatory pressure",
        "Whether shareholder returns remain sustainable",
    ],
    "Industrials": [
        "Execution through capital spending and infrastructure cycles",
        "Supply chain, margin and operational discipline",
        "Exposure to automation, reshoring or long-term investment themes",
        "Whether demand remains resilient if the economy slows",
    ],
}


SECTOR_IMPACT_SUMMARIES = {
    "Technology": "Leadership matters because technology companies must continually adapt to platform shifts, product cycles, AI disruption and changing customer demand.",
    "Communication Services": "Leadership matters because platform companies face constant pressure around user engagement, monetisation, regulation, AI disruption and competition.",
    "Consumer Cyclical": "Leadership matters because consumer-facing companies are exposed to demand cycles, brand execution, pricing pressure and changing customer behaviour.",
    "Consumer Defensive": "Leadership matters because defensive consumer companies rely on pricing power, distribution strength, brand trust and margin discipline.",
    "Financial Services": "Leadership matters because financial companies depend heavily on risk management, capital allocation, regulation, trust and cycle discipline.",
    "Healthcare": "Leadership matters because healthcare companies must balance innovation, regulation, pricing pressure, product cycles and long-term investment.",
    "Energy": "Leadership matters because energy companies are shaped by commodity cycles, capital discipline, geopolitical risk and the transition to lower-carbon systems.",
    "Industrials": "Leadership matters because industrial companies depend on operational execution, capital cycles, supply chains and long-term customer investment.",
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


def company_file(slug: str) -> Path:
    return COMPANIES_DIR / f"{slug}.json"


def leadership_file(slug: str) -> Path:
    return LEADERSHIP_DIR / f"{slug}-ceo.json"


def merge_company_data(index_entry: Dict[str, Any]) -> Dict[str, Any]:
    slug = index_entry.get("slug")

    if not slug:
        return index_entry

    detail = read_json(company_file(slug), {})
    return {**index_entry, **detail}


def build_watch_points(company: Dict[str, Any]) -> list[str]:
    sector = company.get("sector") or "Unknown"

    return SECTOR_WATCH_POINTS.get(
        sector,
        [
            "Ability to sustain competitive advantage",
            "Execution on growth, margins and capital allocation",
            "Exposure to sector-specific disruption or regulation",
            "Whether valuation expectations remain supported by delivery",
        ],
    )


def build_impact_summary(company: Dict[str, Any]) -> str:
    sector = company.get("sector") or "Unknown"
    company_name = company.get("companyName") or company.get("ticker") or "This company"

    sector_summary = SECTOR_IMPACT_SUMMARIES.get(
        sector,
        "Leadership matters because strategy, execution, capital allocation and risk management can materially affect long-term shareholder returns.",
    )

    return f"{company_name} does not yet have a curated CEO Watch profile. {sector_summary}"


def build_leadership_profile(company: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    return {
        "generatedBy": "StockLayer leadership profile generator",
        "generatedAt": now,
        "ceoName": "To be confirmed",
        "role": "Chief Executive Officer",
        "age": "To be confirmed",
        "ceoSince": "To be confirmed",
        "chairSince": "To be confirmed",
        "tenureYears": "To be confirmed",
        "sharePriceAtStart": None,
        "ceoWatchScore": None,
        "likelyRemainingTenure": "To be confirmed",
        "impactSummary": build_impact_summary(company),
        "watchPoints": build_watch_points(company),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate baseline StockLayer leadership feeds.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing leadership files, except preserved slugs.",
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

    LEADERSHIP_DIR.mkdir(exist_ok=True)

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

        output_path = leadership_file(slug)

        if slug in preserve_slugs and not args.include_preserved:
            print(f"Preserving curated leadership profile: {slug}")
            skipped += 1
            continue

        if output_path.exists() and not args.force:
            print(f"Leadership profile already exists, skipped: {slug}")
            skipped += 1
            continue

        company = merge_company_data(index_entry)
        profile = build_leadership_profile(company)

        write_json(output_path, profile)
        print(f"Wrote leadership profile: {output_path.relative_to(ROOT)}")
        created += 1

    print(f"StockLayer leadership profile generation complete. Created: {created}. Skipped: {skipped}.")


if __name__ == "__main__":
    main()
