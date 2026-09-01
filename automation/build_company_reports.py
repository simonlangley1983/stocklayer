#!/usr/bin/env python3
"""Build customer-facing company intelligence reports from auditable data feeds."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = ROOT / "universes" / "uk-100" / "companies.json"
SENTIMENT_HISTORY_DIR = ROOT / "sentiment" / "history"
ANNUAL_HISTORY_PATH = ROOT / "annual-reports" / "extracted-keywords-history.json"
ANNUAL_LATEST_PATH = ROOT / "annual-reports" / "extracted-keywords.json"
OUTPUT_DIR = ROOT / "company-reports"

KEYWORDS = {
    "ai_mentions": "AI",
    "cloud_mentions": "Cloud",
    "cybersecurity_mentions": "Cybersecurity",
    "regulation_mentions": "Regulation",
    "competition_mentions": "Competition",
    "supply_chain_mentions": "Supply chain",
    "china_mentions": "China",
    "margin_pressure_mentions": "Margin pressure",
    "restructuring_mentions": "Restructuring",
    "data_center_mentions": "Data centres",
}
GROWTH_KEYWORDS = {
    "ai_mentions",
    "cloud_mentions",
    "cybersecurity_mentions",
    "data_center_mentions",
}
PRESS_FLAG_TYPES_TO_SKIP = {"low_confidence", "no_coverage", "collection_degraded"}


def read_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def keyword_density(report: dict[str, Any], key: str) -> float:
    words = max(1, int(report.get("extracted_word_count") or 0))
    return round(float(report.get(key) or 0) * 10_000 / words, 3)


def positivity_indicators(reports: list[dict[str, Any]]) -> list[float | None]:
    """Return a transparent thematic-direction proxy, not linguistic sentiment.

    A score above 50 means growth-theme density strengthened relative to the
    preceding report more than pressure/risk-theme density did. The first
    report has no baseline and is therefore null.
    """
    results: list[float | None] = [None]
    for previous, current in zip(reports, reports[1:]):
        growth_delta = sum(
            keyword_density(current, key) - keyword_density(previous, key)
            for key in GROWTH_KEYWORDS
        )
        pressure_delta = sum(
            keyword_density(current, key) - keyword_density(previous, key)
            for key in KEYWORDS
            if key not in GROWTH_KEYWORDS and key != "china_mentions"
        )
        results.append(round(clamp(50 + (growth_delta - pressure_delta * 0.5) * 6), 1))
    return results


def build_annual_section(reports: list[dict[str, Any]]) -> dict[str, Any]:
    reports = sorted(reports, key=lambda item: int(item.get("report_year") or 0))
    indicators = positivity_indicators(reports) if reports else []
    rendered: list[dict[str, Any]] = []
    for report, indicator in zip(reports, indicators):
        keywords = [
            {
                "key": key.removesuffix("_mentions"),
                "label": label,
                "mentions": int(report.get(key) or 0),
                "per10kWords": keyword_density(report, key),
            }
            for key, label in KEYWORDS.items()
        ]
        tone_score = report.get("tone_positivity_score")
        rendered.append(
            {
                "year": int(report.get("report_year") or 0),
                "title": report.get("report_title"),
                "url": report.get("report_url"),
                "sourceUrl": report.get("report_source_url"),
                "wordCount": int(report.get("extracted_word_count") or 0),
                "pageCount": report.get("page_count"),
                "status": report.get("report_data_status"),
                "keywords": keywords,
                "positivityScore": (
                    round(float(tone_score), 1) if tone_score is not None else indicator
                ),
                "positivityMethod": (
                    "lexical annual-report tone"
                    if tone_score is not None
                    else "year-on-year thematic direction proxy"
                ),
            }
        )

    emerging: list[dict[str, Any]] = []
    if len(rendered) >= 2:
        previous = {item["key"]: item for item in rendered[-2]["keywords"]}
        for item in rendered[-1]["keywords"]:
            prior = previous[item["key"]]["per10kWords"]
            emerging.append(
                {
                    **item,
                    "previousPer10kWords": prior,
                    "changePer10kWords": round(item["per10kWords"] - prior, 3),
                }
            )
        emerging.sort(key=lambda item: item["changePer10kWords"], reverse=True)

    latest = rendered[-1] if rendered else None
    return {
        "reportCount": len(rendered),
        "reports": rendered,
        "emergingKeywords": emerging[:5],
        "latestPositivity": latest.get("positivityScore") if latest else None,
        "latestPositivityMethod": latest.get("positivityMethod") if latest else None,
        "methodology": (
            "Keyword rates are mentions per 10,000 extracted words. Until lexical tone is "
            "available, positivity is a labelled proxy comparing year-on-year growth-theme "
            "density with pressure/risk-theme density; it is not management guidance or a recommendation."
        ),
    }


def press_events(observations: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for observation in observations:
        event_flags = [
            item
            for item in observation.get("flags", [])
            if item.get("type") not in PRESS_FLAG_TYPES_TO_SKIP
        ]
        flag = event_flags[0] if event_flags else None
        for story in observation.get("topStories", [])[:2]:
            title = str(story.get("title") or "").strip()
            if not title:
                continue
            key = title.casefold()
            significance = abs(float(story.get("polarity") or 0))
            significance += min(3, int(story.get("publisherCount") or 1)) * 0.15
            significance += 0.5 if flag else 0
            candidate = {
                "date": str(story.get("publishedAt") or observation.get("date") or "")[:10],
                "type": flag.get("type") if flag else "press_story",
                "title": title,
                "source": story.get("source"),
                "url": story.get("url"),
                "direction": flag.get("direction") if flag else story.get("sentiment"),
                "detail": flag.get("detail") if flag else None,
                "significance": round(significance, 3),
            }
            if key not in candidates or candidate["significance"] > candidates[key]["significance"]:
                candidates[key] = candidate
    ranked = sorted(
        candidates.values(),
        key=lambda item: (item["significance"], item["date"]),
        reverse=True,
    )[:limit]
    return sorted(ranked, key=lambda item: item["date"])


def build_press_section(payload: dict[str, Any] | None) -> dict[str, Any]:
    observations = list((payload or {}).get("observations", []))
    series = [
        {
            "date": item.get("date"),
            "dailyScore": item.get("dailyScore"),
            "rollingScore": item.get("rollingScore"),
            "storyCount": int(item.get("storyCount") or 0),
            "sourceCount": int(item.get("sourceCount") or 0),
            "confidence": item.get("confidence"),
            "coverageStatus": item.get("coverageStatus"),
        }
        for item in observations
    ]
    scored = [item for item in series if item["dailyScore"] is not None]
    latest = scored[-1] if scored else None
    return {
        "periodStart": series[0]["date"] if series else None,
        "periodEnd": series[-1]["date"] if series else None,
        "observationCount": len(series),
        "scoredDayCount": len(scored),
        "latestScore": latest["dailyScore"] if latest else None,
        "latestScoreDate": latest["date"] if latest else None,
        "series": series,
        "stories": press_events(observations),
    }


def company_intro(company: dict[str, Any]) -> str:
    name = company.get("companyName") or company.get("ticker") or "This company"
    sector = company.get("sector") or "listed"
    ticker = company.get("ticker") or "its London ticker"
    rank = company.get("ftseRank")
    rank_text = f" It is ranked #{rank} in the StockLayer UK 100 by market capitalisation." if rank else ""
    return f"{name} is a London-listed {sector.lower()} company tracked as {ticker}.{rank_text}"


def build_company_report(
    company: dict[str, Any],
    sentiment: dict[str, Any] | None,
    annual_reports: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    annual = build_annual_section(annual_reports)
    press = build_press_section(sentiment)
    annual_events = [
        {
            "date": f"{item['year']}-12-31",
            "type": "annual_report",
            "title": item.get("title") or f"Annual report {item['year']}",
            "url": item.get("url"),
            "direction": None,
            "detail": "Report-year marker; the exact publication date is not available in the source feed.",
            "approximateDate": True,
        }
        for item in annual["reports"]
    ]
    events = sorted([*press["stories"], *annual_events], key=lambda item: item["date"])
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "company": {
            "companyName": company.get("companyName"),
            "ticker": company.get("ticker"),
            "slug": company.get("slug"),
            "sector": company.get("sector"),
            "domain": company.get("domain"),
            "ftseRank": company.get("ftseRank"),
            "marketCap": company.get("marketCap"),
            "introduction": company_intro(company),
        },
        "pressCoverage": press,
        "annualReportAnalysis": annual,
        "events": events,
    }


def load_annual_reports() -> dict[str, list[dict[str, Any]]]:
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    for path in (ANNUAL_HISTORY_PATH, ANNUAL_LATEST_PATH):
        payload = read_json(path, {}) or {}
        for report in payload.get("reports", []):
            slug = report.get("company_slug")
            year = int(report.get("report_year") or 0)
            if slug and year:
                merged[(slug, year)] = report
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (slug, _), report in merged.items():
        grouped[slug].append(report)
    return grouped


def main() -> int:
    universe = read_json(UNIVERSE_PATH, {}) or {}
    companies = universe.get("companies", [])
    annual_by_slug = load_annual_reports()
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_companies = []
    for company in companies:
        slug = company["slug"]
        sentiment = read_json(SENTIMENT_HISTORY_DIR / f"{slug}.json", {})
        report = build_company_report(
            company,
            sentiment,
            annual_by_slug.get(slug, []),
            generated_at,
        )
        (OUTPUT_DIR / f"{slug}.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        manifest_companies.append(
            {
                "slug": slug,
                "annualReportCount": report["annualReportAnalysis"]["reportCount"],
                "sentimentObservationCount": report["pressCoverage"]["observationCount"],
                "eventCount": len(report["events"]),
            }
        )
    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "companyCount": len(manifest_companies),
        "companies": manifest_companies,
    }
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Built {len(manifest_companies)} customer company reports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
