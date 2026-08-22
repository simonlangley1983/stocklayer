"""Build the compact sentiment company universe from StockLayer's UK company data."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ALIAS_OVERRIDES = {
    "shell": ["Shell plc"],
    "hsbc": ["HSBC Holdings", "HSBC Bank"],
    "barclays": ["Barclays PLC", "Barclays Bank"],
    "berkeley": ["Berkeley Group"],
    "british-american-tobacco": ["British American Tobacco", "BAT"],
    "bp": ["BP plc", "British Petroleum"],
    "rolls-royce": ["Rolls-Royce Holdings", "Rolls Royce"],
    "lseg": ["London Stock Exchange Group", "LSEG"],
    "national-grid": ["National Grid plc"],
    "compass": ["Compass Group plc"],
    "bae-systems": ["BAE Systems plc"],
    "lloyds": ["Lloyds Banking Group", "Lloyds Bank"],
    "3i": ["3i Group plc"],
    "admiral": ["Admiral Group insurance"],
    "next": ["Next plc retailer"],
    "phoenix": ["Phoenix Group Holdings"],
    "sage": ["Sage Group plc"],
    "unite": ["Unite Group student accommodation"],
    "sse": ["SSE plc energy"],
    "mandg": ["M&G plc"],
    "imi": ["IMI plc engineering"],
    "dcc": ["DCC plc"],
    "land-securities": ["Landsec", "Land Securities Group"],
    "bt": ["BT Group plc", "British Telecom"],
    "wpp": ["WPP plc advertising"],
    "pearson": ["Pearson plc education"],
    "kingfisher": ["Kingfisher plc retailer"],
    "standard-chartered": ["Standard Chartered plc"],
}

AMBIGUOUS_ALIASES = {
    "admiral",
    "bp",
    "bt",
    "compass",
    "dcc",
    "diploma",
    "hsbc",
    "imi",
    "next",
    "phoenix",
    "sage",
    "shell",
    "barclays",
    "berkeley",
    "sse",
    "unite",
}

CONTEXT_OVERRIDES = {
    "admiral": ["insurance", "insurer"],
    "bp": ["oil", "gas", "energy"],
    "bt": ["telecom", "broadband"],
    "compass": ["catering", "foodservice"],
    "dcc": ["distribution", "energy", "technology"],
    "diploma": ["distribution", "industrial"],
    "imi": ["engineering", "industrial"],
    "next": ["retail", "retailer", "fashion", "clothing"],
    "phoenix": ["insurance", "insurer", "pensions"],
    "sage": ["software", "accounting"],
    "shell": ["oil", "gas", "energy"],
    "sse": ["energy", "power", "utility", "renewables"],
    "unite": ["student", "accommodation", "property"],
}

SUFFIX_RE = re.compile(
    r"\s+(?:plc|ag|sa|ltd|group plc|holdings plc|group holdings plc)$",
    re.IGNORECASE,
)


def read_companies(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("companies", [])
    if not isinstance(data, list):
        raise ValueError("Company source must be an array or contain a companies array")
    return data


def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def compact_company(company: dict[str, Any]) -> dict[str, Any]:
    name = str(company["companyName"]).strip()
    slug = str(company["slug"]).strip()
    canonical = SUFFIX_RE.sub("", name).strip()
    aliases = unique_strings([name, canonical, *ALIAS_OVERRIDES.get(slug, [])])
    return {
        "companyName": name,
        "ticker": str(company.get("ticker", "")).strip(),
        "lseTicker": str(company.get("lseTicker", "")).strip(),
        "slug": slug,
        "sector": str(company.get("sector", "Unclassified")).strip(),
        "domain": str(company.get("domain", "")).strip().lower(),
        "aliases": aliases,
        "requireHeadlineAlias": slug in AMBIGUOUS_ALIASES,
        "contextTerms": CONTEXT_OVERRIDES.get(slug, []),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    companies = [compact_company(item) for item in read_companies(args.source)]
    slugs = [item["slug"] for item in companies]
    if len(slugs) != len(set(slugs)):
        raise ValueError("Duplicate company slugs in source")

    payload = {
        "schemaVersion": 1,
        "coverage": "StockLayer UK",
        "companyCount": len(companies),
        "companies": companies,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(companies)} companies to {args.output}")


if __name__ == "__main__":
    main()
