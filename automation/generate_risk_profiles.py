"""
StockLayer risk profile generator.

This script creates baseline risk feeds for companies that do not yet have a
risk/{slug}-risk.json file.

It is deliberately template/rules-based:
- No API key required
- No cost
- Repeatable output
- Safe to run in GitHub Actions
- Does not overwrite curated Cisco-style risk profiles by default

What it does:
- Reads companies.json
- Reads companies/{slug}.json where available for richer market data
- Creates risk/{slug}-risk.json if missing
- Skips existing risk profiles unless --force is used
- Preserves Cisco by default

Usage:
    python automation/generate_risk_profiles.py

Force overwrite existing generated risk profiles:
    python automation/generate_risk_profiles.py --force

Overwrite everything except Cisco:
    python automation/generate_risk_profiles.py --force --preserve cisco

Generate for one company only:
    python automation/generate_risk_profiles.py --only microsoft
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
RISK_DIR = ROOT / "risk"
ACQUISITIONS_DIR = ROOT / "acquisitions"


DEFAULT_PRESERVE_SLUGS = {"cisco"}


SECTOR_RISK_BASELINES = {
    "Technology": {
        "volatility": 5,
        "ai": 6,
        "globalConflict": 5,
        "financialMarkets": 5,
        "acquisitionDependency": 4,
        "volatilityLabel": "Moderate",
    },
    "Communication Services": {
        "volatility": 5,
        "ai": 6,
        "globalConflict": 4,
        "financialMarkets": 5,
        "acquisitionDependency": 4,
        "volatilityLabel": "Moderate",
    },
    "Consumer Cyclical": {
        "volatility": 6,
        "ai": 4,
        "globalConflict": 4,
        "financialMarkets": 6,
        "acquisitionDependency": 3,
        "volatilityLabel": "Moderate / Higher",
    },
    "Consumer Defensive": {
        "volatility": 3,
        "ai": 3,
        "globalConflict": 4,
        "financialMarkets": 3,
        "acquisitionDependency": 3,
        "volatilityLabel": "Lower / Stable",
    },
    "Financial Services": {
        "volatility": 5,
        "ai": 4,
        "globalConflict": 4,
        "financialMarkets": 7,
        "acquisitionDependency": 3,
        "volatilityLabel": "Moderate",
    },
    "Healthcare": {
        "volatility": 4,
        "ai": 4,
        "globalConflict": 3,
        "financialMarkets": 4,
        "acquisitionDependency": 4,
        "volatilityLabel": "Moderate / Stable",
    },
    "Energy": {
        "volatility": 6,
        "ai": 2,
        "globalConflict": 7,
        "financialMarkets": 6,
        "acquisitionDependency": 3,
        "volatilityLabel": "Moderate / Higher",
    },
    "Industrials": {
        "volatility": 5,
        "ai": 4,
        "globalConflict": 5,
        "financialMarkets": 5,
        "acquisitionDependency": 3,
        "volatilityLabel": "Moderate",
    },
}


AI_EXPLANATIONS = {
    "Technology": "AI is likely to be both an opportunity and a disruption risk. The company may benefit from AI demand, but could also face faster product cycles and changing customer priorities.",
    "Communication Services": "AI could reshape advertising, search, content creation and user engagement. The risk is not simply whether AI helps the company, but whether it changes how users interact with its core platforms.",
    "Consumer Cyclical": "AI is less likely to be an existential risk, but it may affect personalisation, logistics, pricing and customer acquisition over time.",
    "Consumer Defensive": "AI is more likely to be an efficiency and analytics opportunity than a direct disruption risk, although retailers and consumer brands still face changing digital behaviour.",
    "Financial Services": "AI may improve fraud detection, underwriting, automation and customer service, but also increases model, regulatory and cyber risk.",
    "Healthcare": "AI may support research, diagnostics, operations and productivity, but healthcare adoption is likely to be shaped by regulation, trust and clinical validation.",
    "Energy": "AI is not a direct existential threat, but it may influence energy demand, grid investment, trading, maintenance and operational efficiency.",
    "Industrials": "AI may improve automation, maintenance, design and productivity, but the disruption risk is more gradual than in software-led sectors.",
}


GLOBAL_CONFLICT_EXPLANATIONS = {
    "Technology": "The company may be exposed to global supply chains, export controls, data sovereignty and geopolitical tensions around chips, cloud infrastructure or enterprise technology.",
    "Communication Services": "The company may face cross-border regulatory pressure, data sovereignty issues and restrictions in certain markets, but direct physical supply chain risk is usually lower than in hardware-heavy sectors.",
    "Consumer Cyclical": "Global conflict can affect consumer confidence, supply chains, input costs and international demand.",
    "Consumer Defensive": "Demand is usually more resilient, but global conflict can still affect supply chains, input costs and international operations.",
    "Financial Services": "The company may be exposed through market volatility, sanctions, cross-border capital flows, credit conditions and economic confidence.",
    "Healthcare": "Healthcare demand is defensive, but global conflict can affect supply chains, regulation, manufacturing and access to international markets.",
    "Energy": "The company is directly exposed to geopolitical risk because conflict can affect energy prices, supply routes, production and regulation.",
    "Industrials": "The company may be exposed to supply chains, defence spending, infrastructure demand, logistics disruption and capital investment cycles.",
}


FINANCIAL_MARKETS_EXPLANATIONS = {
    "Technology": "Technology valuations are sensitive to interest rates, growth expectations and investor appetite for long-duration earnings.",
    "Communication Services": "The company is exposed to advertising cycles, consumer engagement, regulation and investor appetite for platform growth.",
    "Consumer Cyclical": "The company is exposed to consumer confidence, discretionary spending, financing conditions and economic cycles.",
    "Consumer Defensive": "The company is usually more resilient in downturns, but margins can still be affected by inflation, pricing pressure and input costs.",
    "Financial Services": "The company is directly exposed to interest rates, credit conditions, market activity, regulation and investor confidence.",
    "Healthcare": "The company is less cyclical than many sectors, but pricing, regulation, patent cycles and pipeline expectations can still move valuation.",
    "Energy": "The company is highly sensitive to commodity prices, capital discipline, demand expectations and geopolitical developments.",
    "Industrials": "The company is exposed to capital spending cycles, supply chains, infrastructure demand and broader economic confidence.",
}


ACQUISITION_EXPLANATIONS = {
    "Technology": "Technology companies often use acquisitions to add products, talent, platforms or market access. The risk is whether acquired assets are integrated well and contribute to durable growth.",
    "Communication Services": "Acquisitions can help expand platforms, content, advertising technology or capabilities, but regulatory scrutiny may limit larger deals.",
    "Consumer Cyclical": "Acquisition dependency is usually lower unless the company relies on buying brands, channels or new categories to sustain growth.",
    "Consumer Defensive": "Acquisitions can add brands or distribution, but the core investment case is usually less dependent on dealmaking than in technology or healthcare.",
    "Financial Services": "Acquisition dependency varies by business model, but regulators can limit major consolidation and integration risk is important.",
    "Healthcare": "Healthcare companies can rely on acquisitions to refresh pipelines, add products or expand capabilities, making deal quality an important long-term risk.",
    "Energy": "Acquisitions may reshape reserves, production or infrastructure exposure, but commodity prices and capital discipline are usually more important.",
    "Industrials": "Acquisitions can add capabilities or market access, but integration discipline matters because industrial businesses can be complex.",
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


def clamp_score(value: Any) -> int:
    number = safe_float(value)

    if number is None:
        return 5

    return max(1, min(10, int(round(number))))


def score_label(score: int) -> str:
    if score <= 3:
        return "Low"
    if score <= 6:
        return "Medium"
    return "High"


def company_file(slug: str) -> Path:
    return COMPANIES_DIR / f"{slug}.json"


def risk_file(slug: str) -> Path:
    return RISK_DIR / f"{slug}-risk.json"


def acquisitions_file(slug: str) -> Path:
    return ACQUISITIONS_DIR / f"{slug}-acquisitions.json"


def merge_company_data(index_entry: Dict[str, Any]) -> Dict[str, Any]:
    slug = index_entry.get("slug")

    if not slug:
        return index_entry

    detail = read_json(company_file(slug), {})
    return {**index_entry, **detail}


def baseline_for_sector(sector: str) -> Dict[str, Any]:
    return SECTOR_RISK_BASELINES.get(
        sector,
        {
            "volatility": 5,
            "ai": 5,
            "globalConflict": 5,
            "financialMarkets": 5,
            "acquisitionDependency": 4,
            "volatilityLabel": "Moderate",
        },
    )


def market_cap_to_number(value: Any) -> Optional[float]:
    if value is None:
        return None

    text = str(value).strip().upper().replace("$", "")

    if not text:
        return None

    multiplier = 1

    if text.endswith("T"):
        multiplier = 1_000_000_000_000
        text = text[:-1]
    elif text.endswith("B"):
        multiplier = 1_000_000_000
        text = text[:-1]
    elif text.endswith("M"):
        multiplier = 1_000_000
        text = text[:-1]

    number = safe_float(text)

    if number is None:
        return None

    return number * multiplier


def adjust_for_scale(score: int, market_cap: Any) -> int:
    market_cap_number = market_cap_to_number(market_cap)

    if market_cap_number is None:
        return score

    if market_cap_number >= 1_000_000_000_000:
        return max(1, score - 1)

    if market_cap_number < 100_000_000_000:
        return min(10, score + 1)

    return score


def has_acquisition_feed(slug: str) -> bool:
    data = read_json(acquisitions_file(slug), {})
    items = data.get("majorAcquisitions") if isinstance(data, dict) else None
    return isinstance(items, list) and len(items) > 0


def acquisition_score(company: Dict[str, Any], base_score: int) -> int:
    slug = company.get("slug")

    if slug and has_acquisition_feed(slug):
        return min(10, max(base_score, 6))

    sector = company.get("sector") or ""

    if sector in {"Technology", "Healthcare"}:
        return max(base_score, 4)

    return base_score


def volatility_explanation(company: Dict[str, Any], score: int) -> str:
    name = company.get("companyName") or company.get("ticker") or "The company"
    sector = company.get("sector") or "its sector"
    market_cap = company.get("marketCap")

    scale_text = f" with a market capitalisation of around ${market_cap}" if market_cap else ""

    if score <= 3:
        return f"{name} is a large, established {sector.lower()} company{scale_text}, so expected share price volatility is lower than for smaller or more speculative businesses."
    if score <= 6:
        return f"{name} is a significant {sector.lower()} company{scale_text}. Volatility is likely to be moderate, shaped by earnings expectations, valuation and sector sentiment."
    return f"{name} has a higher expected volatility profile, reflecting sensitivity to growth expectations, valuation changes and sector-specific risk."


def marker(
    category: str,
    score: int,
    explanation: str,
) -> Dict[str, Any]:
    clean_score = clamp_score(score)

    return {
        "category": category,
        "score": clean_score,
        "label": score_label(clean_score),
        "explanation": explanation,
    }


def build_risk_profile(company: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    sector = company.get("sector") or "Unknown"
    baseline = baseline_for_sector(sector)

    volatility_score = adjust_for_scale(
        clamp_score(baseline.get("volatility")),
        company.get("marketCap"),
    )

    ai_score = clamp_score(baseline.get("ai"))
    global_conflict_score = clamp_score(baseline.get("globalConflict"))
    financial_markets_score = clamp_score(baseline.get("financialMarkets"))
    acquisition_dependency_score = acquisition_score(
        company,
        clamp_score(baseline.get("acquisitionDependency")),
    )

    return {
        "generatedBy": "StockLayer risk profile generator",
        "generatedAt": now,
        "stockLayerVolatilityRating": {
            "score": volatility_score,
            "label": baseline.get("volatilityLabel") or score_label(volatility_score),
            "explanation": volatility_explanation(company, volatility_score),
        },
        "vulnerabilityMarkers": [
            marker(
                "AI disruption",
                ai_score,
                AI_EXPLANATIONS.get(
                    sector,
                    "AI may create both efficiency opportunities and disruption risk, depending on how quickly the company and its competitors adapt.",
                ),
            ),
            marker(
                "Global conflict",
                global_conflict_score,
                GLOBAL_CONFLICT_EXPLANATIONS.get(
                    sector,
                    "The company may be exposed to geopolitical risk through supply chains, regulation, demand or cross-border operations.",
                ),
            ),
            marker(
                "Financial markets",
                financial_markets_score,
                FINANCIAL_MARKETS_EXPLANATIONS.get(
                    sector,
                    "The company is exposed to investor sentiment, earnings expectations, interest rates and broader market conditions.",
                ),
            ),
            marker(
                "Acquisition dependency",
                acquisition_dependency_score,
                ACQUISITION_EXPLANATIONS.get(
                    sector,
                    "Acquisitions can support growth, but also create integration, valuation and execution risk.",
                ),
            ),
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate baseline StockLayer risk feeds.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing risk files, except preserved slugs.",
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

    RISK_DIR.mkdir(exist_ok=True)

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

        output_path = risk_file(slug)

        if slug in preserve_slugs and not args.include_preserved:
            print(f"Preserving curated risk profile: {slug}")
            skipped += 1
            continue

        if output_path.exists() and not args.force:
            print(f"Risk profile already exists, skipped: {slug}")
            skipped += 1
            continue

        company = merge_company_data(index_entry)
        profile = build_risk_profile(company)

        write_json(output_path, profile)
        print(f"Wrote risk profile: {output_path.relative_to(ROOT)}")
        created += 1

    print(f"StockLayer risk profile generation complete. Created: {created}. Skipped: {skipped}.")


if __name__ == "__main__":
    main()
