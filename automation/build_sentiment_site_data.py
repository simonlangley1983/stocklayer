"""Build the stable, join-ready sentiment feeds consumed by the StockLayer site."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "universes" / "uk-100" / "companies.json"
DEFAULT_LATEST = ROOT / "sentiment" / "latest.json"
DEFAULT_SUMMARY = ROOT / "sentiment" / "site-summary.json"
DEFAULT_MANIFEST = ROOT / "data-manifest.json"

SUMMARY_FIELDS = (
    "companyName",
    "ticker",
    "slug",
    "date",
    "dailyScore",
    "dailyLabel",
    "rollingScore",
    "confidence",
    "confidenceBand",
    "coverageStatus",
    "storyCount",
    "sourceCount",
    "changeFromPreviousScoredDay",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def company_list(payload: Any) -> list[dict[str, Any]]:
    companies = payload if isinstance(payload, list) else payload.get("companies", [])
    if not isinstance(companies, list):
        raise ValueError("Company registry must be an array or contain a companies array")
    return companies


def validate_unique_slugs(companies: list[dict[str, Any]], label: str) -> set[str]:
    slugs = [str(company.get("slug", "")).strip() for company in companies]
    if any(not slug for slug in slugs):
        raise ValueError(f"{label} contains a company without a slug")
    if len(slugs) != len(set(slugs)):
        raise ValueError(f"{label} contains duplicate slugs")
    return set(slugs)


def build_registry(source: Any) -> dict[str, Any]:
    companies = company_list(source)
    validate_unique_slugs(companies, "UK company source")
    return {
        "schemaVersion": 1,
        "id": "uk-100",
        "name": "StockLayer UK 100",
        "joinKey": "slug",
        "companyCount": len(companies),
        "companies": companies,
    }


def compact_flags(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ("type", "direction", "severity", "status", "detail")
    return [
        {field: flag[field] for field in fields if field in flag}
        for flag in flags
    ]


def build_site_summary(
    registry: dict[str, Any], latest: dict[str, Any]
) -> dict[str, Any]:
    companies = company_list(registry)
    registry_slugs = validate_unique_slugs(companies, "UK company registry")
    latest_companies = latest.get("companies", {})
    if not isinstance(latest_companies, dict):
        raise ValueError("sentiment/latest.json companies must be keyed by slug")
    latest_slugs = set(latest_companies)
    if registry_slugs != latest_slugs:
        missing = sorted(registry_slugs - latest_slugs)
        extra = sorted(latest_slugs - registry_slugs)
        raise ValueError(
            "Company/sentiment slug mismatch: "
            f"missing sentiment={missing}; unknown sentiment={extra}"
        )

    summaries: dict[str, dict[str, Any]] = {}
    for company in companies:
        slug = company["slug"]
        observation = latest_companies[slug]
        summary = {
            field: observation.get(field)
            for field in SUMMARY_FIELDS
            if field in observation
        }
        summary["flags"] = compact_flags(observation.get("flags", []))
        summaries[slug] = summary

    return {
        "schemaVersion": 1,
        "methodologyVersion": latest.get("methodologyVersion"),
        "universe": registry["id"],
        "joinKey": "slug",
        "generatedAt": latest.get("generatedAt"),
        "asOfDate": latest.get("asOfDate"),
        "companyCount": len(summaries),
        "companies": summaries,
    }


def build_manifest(registry: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "generatedAt": summary.get("generatedAt"),
        "joinKey": "slug",
        "universes": {
            registry["id"]: {
                "name": registry["name"],
                "companyCount": registry["companyCount"],
                "companies": "universes/uk-100/companies.json",
            }
        },
        "newsSentiment": {
            "methodologyVersion": summary.get("methodologyVersion"),
            "asOfDate": summary.get("asOfDate"),
            "homepageSummary": "sentiment/site-summary.json",
            "homepageSummarySchema": "sentiment/site-summary-schema-v1.json",
            "latestWithStories": "sentiment/latest.json",
            "historyTemplate": "sentiment/history/{slug}.json",
            "runStatus": "sentiment/run-status.json",
            "methodology": "sentiment/methodology.json",
            "observationSchema": "sentiment/schema-v1.json",
            "scoreRange": [0, 100],
            "noCoverageScore": None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="Optional source company JSON used to initialise/refresh the UK registry",
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--latest", type=Path, default=DEFAULT_LATEST)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    registry = build_registry(read_json(args.source)) if args.source else read_json(args.registry)
    summary = build_site_summary(registry, read_json(args.latest))
    manifest = build_manifest(registry, summary)

    if args.source:
        write_json_atomic(args.registry, registry)
    write_json_atomic(args.summary, summary)
    write_json_atomic(args.manifest, manifest)
    print(
        f"Published {summary['companyCount']} joined sentiment summaries "
        f"for {summary['asOfDate']}"
    )


if __name__ == "__main__":
    main()
