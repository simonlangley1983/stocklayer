"""
StockLayer Level 1 AI summary generator.

This script creates simple baseline StockLayer narrative feeds for companies
that do not yet have an ai/{slug}-summary.json file.

It is deliberately template-based rather than API-based:
- No OpenAI/API key required
- No cost
- Repeatable output
- Safe to run in GitHub Actions
- Does not overwrite curated Cisco-style summaries by default

What it does:
- Reads companies.json
- Reads companies/{slug}.json where available for richer market data
- Creates ai/{slug}-summary.json if missing
- Skips existing ai summaries unless --force is used

Usage:
    python automation/generate_ai_summaries.py

Force overwrite existing generated summaries:
    python automation/generate_ai_summaries.py --force

Overwrite everything except Cisco:
    python automation/generate_ai_summaries.py --force --preserve cisco

Generate for one company only:
    python automation/generate_ai_summaries.py --only microsoft
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
AI_DIR = ROOT / "ai"


DEFAULT_PRESERVE_SLUGS = {"cisco"}


SECTOR_LABELS = {
    "Technology": "technology",
    "Communication Services": "communication services",
    "Consumer Cyclical": "consumer-facing growth",
    "Consumer Defensive": "defensive consumer",
    "Financial Services": "financial services",
    "Healthcare": "healthcare",
    "Energy": "energy",
    "Industrials": "industrial",
    "Basic Materials": "materials",
    "Real Estate": "real estate",
    "Utilities": "utilities",
}


SECTOR_OWNERSHIP_THEMES = {
    "Technology": [
        "Exposure to digital infrastructure and software demand",
        "Potential to benefit from AI, cloud or enterprise technology spending",
        "Scale advantages in global technology markets",
    ],
    "Communication Services": [
        "Large global user or customer base",
        "Exposure to digital advertising, platforms or content demand",
        "Strong network effects where the business retains user attention",
    ],
    "Consumer Cyclical": [
        "Exposure to consumer spending and brand demand",
        "Potential operating leverage when growth is strong",
        "Scale advantages in large addressable markets",
    ],
    "Consumer Defensive": [
        "Resilient demand through economic cycles",
        "Large distribution footprint or trusted consumer brands",
        "Defensive characteristics compared with more cyclical sectors",
    ],
    "Financial Services": [
        "Scale and trust in financial markets",
        "Exposure to payments, credit, banking or capital market activity",
        "Potential benefit from strong consumer and business transaction volumes",
    ],
    "Healthcare": [
        "Exposure to long-term healthcare demand",
        "Potential pricing power from differentiated products or services",
        "Defensive characteristics from non-discretionary healthcare spending",
    ],
    "Energy": [
        "Exposure to global energy demand",
        "Potential cash generation during favourable commodity cycles",
        "Strategic importance in energy supply and infrastructure",
    ],
    "Industrials": [
        "Exposure to infrastructure, manufacturing and capital investment",
        "Potential benefit from long-term automation or productivity trends",
        "Established customer relationships in industrial markets",
    ],
}


SECTOR_BULL_CASE = {
    "Technology": [
        "AI, cloud and software demand continue to support growth",
        "Scale allows the company to protect margins and reinvest heavily",
    ],
    "Communication Services": [
        "Advertising, platform engagement or content demand remains resilient",
        "Large user bases create opportunities for monetisation and product expansion",
    ],
    "Consumer Cyclical": [
        "Consumer demand remains stronger than expected",
        "The company continues to take share in large addressable markets",
    ],
    "Consumer Defensive": [
        "Defensive demand supports earnings even in a weaker economy",
        "Scale and distribution advantages protect profitability",
    ],
    "Financial Services": [
        "Transaction volumes, lending activity or market activity remain supportive",
        "Scale and brand strength help the company defend returns",
    ],
    "Healthcare": [
        "Demand for healthcare products and services continues to grow",
        "The company sustains pricing power or pipeline momentum",
    ],
    "Energy": [
        "Energy prices and demand remain supportive",
        "Capital discipline supports cash generation and shareholder returns",
    ],
    "Industrials": [
        "Infrastructure and capital investment cycles remain supportive",
        "Operational leverage improves margins as demand grows",
    ],
}


SECTOR_BEAR_CASE = {
    "Technology": [
        "Valuation leaves limited room for disappointment",
        "Growth slows if cloud, AI or enterprise technology spending weakens",
    ],
    "Communication Services": [
        "Advertising or consumer engagement slows",
        "Regulation, competition or platform fatigue weighs on growth",
    ],
    "Consumer Cyclical": [
        "Consumer spending weakens in a slower economy",
        "Margin pressure rises if costs increase or demand softens",
    ],
    "Consumer Defensive": [
        "Growth may be slower than more cyclical or technology-led companies",
        "Input cost pressure or price sensitivity could weigh on margins",
    ],
    "Financial Services": [
        "Credit quality, regulation or interest rate changes pressure returns",
        "Market volatility or weaker transaction volumes reduce earnings momentum",
    ],
    "Healthcare": [
        "Regulatory, pricing or patent risks create earnings uncertainty",
        "Pipeline disappointments or competition pressure future growth",
    ],
    "Energy": [
        "Commodity price weakness can quickly reduce earnings and cash flow",
        "Energy transition, regulation or capital intensity creates long-term uncertainty",
    ],
    "Industrials": [
        "Demand weakens if the economic cycle slows",
        "Input costs, supply chains or capital spending delays pressure margins",
    ],
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


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def company_file(slug: str) -> Path:
    return COMPANIES_DIR / f"{slug}.json"


def ai_file(slug: str) -> Path:
    return AI_DIR / f"{slug}-summary.json"


def merge_company_data(index_entry: Dict[str, Any]) -> Dict[str, Any]:
    slug = index_entry.get("slug")
    if not slug:
        return index_entry

    detail = read_json(company_file(slug), {})
    return {**index_entry, **detail}


def market_cap_category(market_cap: Any) -> str:
    text = str(market_cap or "").upper().strip()

    if text.endswith("T"):
        value = safe_float(text[:-1])
        if value is not None and value >= 1:
            return "mega-cap"

    if text.endswith("B"):
        value = safe_float(text[:-1])
        if value is not None:
            if value >= 200:
                return "mega-cap"
            if value >= 10:
                return "large-cap"

    return "large listed"


def valuation_phrase(pe_ratio: Any) -> str:
    pe = safe_float(pe_ratio)

    if pe is None or pe <= 0:
        return "Valuation needs to be considered alongside the quality and durability of earnings."

    if pe >= 60:
        return "The valuation appears demanding, so investors are likely pricing in strong future growth or unusually durable earnings."
    if pe >= 35:
        return "The valuation is elevated, meaning expectations are meaningful and the company has less room for disappointment."
    if pe >= 20:
        return "The valuation is not obviously cheap, but may be justified if the company can sustain quality growth."
    if pe >= 10:
        return "The valuation appears more moderate than many high-growth market leaders, though this may reflect slower expected growth."
    return "The valuation appears low, which may reflect cyclical concerns, slower growth expectations or company-specific risks."


def dividend_phrase(dividend_yield: Any) -> str:
    yield_value = safe_float(dividend_yield)

    if yield_value is None or yield_value <= 0:
        return "The investment case is likely to rely more on capital growth than income."

    if yield_value >= 4:
        return "The dividend yield is relatively high, so income and cash generation may form a larger part of the investor case."
    if yield_value >= 2:
        return "The dividend yield provides some income support, alongside the wider growth and quality story."
    if yield_value >= 0.5:
        return "The dividend yield is modest, so investors are likely focused more on growth, quality and resilience."
    return "The dividend yield is low, so the investor case is primarily about growth and long-term value creation."


def get_sector_list(mapping: Dict[str, List[str]], sector: str, fallback: List[str]) -> List[str]:
    return mapping.get(sector, fallback)


def build_headline(company: Dict[str, Any]) -> str:
    name = company.get("companyName") or company.get("name") or company.get("ticker") or "This company"
    sector = company.get("sector") or "listed company"
    sector_label = SECTOR_LABELS.get(sector, sector.lower())
    cap_category = market_cap_category(company.get("marketCap"))

    return f"{name} is a {cap_category} {sector_label} company with a market position that makes it relevant for StockLayer tracking."


def build_stocklayer_view(company: Dict[str, Any]) -> str:
    name = company.get("companyName") or company.get("name") or company.get("ticker") or "This company"
    sector = company.get("sector") or "Unknown"
    industry = company.get("industry")
    market_cap = company.get("marketCap")
    pe_ratio = company.get("peRatio")
    dividend_yield = company.get("dividendYield")

    sector_label = SECTOR_LABELS.get(sector, sector.lower())
    cap_category = market_cap_category(market_cap)

    industry_text = f" Its specific industry exposure is {industry}." if industry else ""

    parts = [
        f"{name} is a {cap_category} {sector_label} business.",
        industry_text.strip(),
        valuation_phrase(pe_ratio),
        dividend_phrase(dividend_yield),
        "The key StockLayer question is whether the company can keep turning scale, competitive position and execution into attractive shareholder returns."
    ]

    return " ".join(part for part in parts if part)


def build_why_investors_own_it(company: Dict[str, Any]) -> List[str]:
    sector = company.get("sector") or "Unknown"
    fallback = [
        "Large scale and market relevance",
        "Potential to compound value over time",
        "Exposure to long-term business or consumer demand",
    ]

    items = get_sector_list(SECTOR_OWNERSHIP_THEMES, sector, fallback)

    market_cap = company.get("marketCap")
    if market_cap:
        items = [f"Significant scale, with a market capitalisation of around ${market_cap}"] + items[:2]

    return items[:3]


def build_bull_case(company: Dict[str, Any]) -> List[str]:
    sector = company.get("sector") or "Unknown"
    fallback = [
        "The company continues to defend or expand its competitive position",
        "Revenue growth and margins prove more resilient than expected",
    ]

    items = get_sector_list(SECTOR_BULL_CASE, sector, fallback)
    items.append("Investor confidence improves if execution remains strong and guidance is delivered.")

    return items[:3]


def build_bear_case(company: Dict[str, Any]) -> List[str]:
    sector = company.get("sector") or "Unknown"
    fallback = [
        "Growth slows or margins come under pressure",
        "Competition, regulation or execution issues reduce investor confidence",
    ]

    items = get_sector_list(SECTOR_BEAR_CASE, sector, fallback)
    items.append("The share price could remain vulnerable if valuation expectations are too high.")

    return items[:3]


def build_summary(company: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    name = company.get("companyName") or company.get("name") or company.get("ticker") or "Unknown company"

    return {
        "generatedBy": "StockLayer template generator",
        "generatedAt": now,
        "companyName": name,
        "ticker": company.get("ticker"),
        "headline": build_headline(company),
        "stockLayerView": build_stocklayer_view(company),
        "whyInvestorsOwnIt": build_why_investors_own_it(company),
        "bullCase": build_bull_case(company),
        "bearCase": build_bear_case(company),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate baseline StockLayer AI summary feeds.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing AI summary files, except preserved slugs.",
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

    AI_DIR.mkdir(exist_ok=True)

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

        output_path = ai_file(slug)

        if slug in preserve_slugs and not args.include_preserved:
            print(f"Preserving curated AI summary: {slug}")
            skipped += 1
            continue

        if output_path.exists() and not args.force:
            print(f"AI summary already exists, skipped: {slug}")
            skipped += 1
            continue

        company = merge_company_data(index_entry)
        summary = build_summary(company)

        write_json(output_path, summary)
        print(f"Wrote AI summary: {output_path.relative_to(ROOT)}")
        created += 1

    print(f"StockLayer AI summary generation complete. Created: {created}. Skipped: {skipped}.")


if __name__ == "__main__":
    main()
