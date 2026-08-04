#!/usr/bin/env python3
"""Snapshot StockLayer growth confidence and build validation history."""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPANIES_FILE = ROOT / "uk-companies.json"
LOCAL_COMPANIES_FALLBACK = ROOT / "ftse100.json"
HISTORY_DIR = ROOT / "history" / "growth-confidence"
HISTORY_FILE = HISTORY_DIR / "history.json"
FORWARD_RETURN_DAYS = (30, 90, 180, 365)
WEIGHTS = {
    "momentum": 0.35,
    "annualReportHealth": 0.30,
    "marketPosition": 0.20,
    "riskBalance": 0.15,
}


def clamp_score(value: float | int | None, fallback: float = 50) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        value = fallback
    return max(0, min(100, float(value)))


def company_hash(company: dict[str, Any]) -> int:
    key = str(company.get("slug") or company.get("ticker") or company.get("companyName") or "")
    return sum(ord(character) for character in key)


def previous_growth(company: dict[str, Any], period: str) -> float | None:
    values = company.get("previousGrowth") or company.get("previousGrowthPercent") or {}
    if isinstance(values, dict):
        value = values.get(period)
        if isinstance(value, (int, float)) and math.isfinite(value):
            return float(value)
    return None


def has_annual_report_intel(company: dict[str, Any]) -> bool:
    keywords = company.get("annualReportKeywords")
    if isinstance(keywords, dict) and keywords:
        return True
    reports = company.get("annualReports") or company.get("reports") or company.get("reportUrls")
    if isinstance(reports, list):
        for report in reports:
            if not isinstance(report, dict):
                continue
            if any(report.get(field) is not None for field in ("keywordCounts", "keywords", "wordCount", "words")):
                return True
    return False


def latest_annual_report_year(company: dict[str, Any], snapshot_year: int) -> int:
    for field in ("latestAnnualReportYear", "annualReportYear", "reportYear", "latestReportYear"):
        value = company.get(field)
        if isinstance(value, (int, float)) and value > 2000:
            return int(value)

    reports = company.get("annualReports") or company.get("reports") or company.get("reportUrls")
    if isinstance(reports, list):
        years: list[int] = []
        for report in reports:
            if not isinstance(report, dict):
                continue
            raw_year = report.get("year") or report.get("reportYear")
            if raw_year is None and isinstance(report.get("date"), str):
                raw_year = report["date"][:4]
            try:
                parsed = int(raw_year)
            except (TypeError, ValueError):
                continue
            if parsed > 2000:
                years.append(parsed)
        if years:
            return max(years)

    keyed_reports = company.get("annualReportUrls") or company.get("annualReportLinks")
    if isinstance(keyed_reports, dict):
        years = []
        for raw_year in keyed_reports:
            try:
                parsed = int(raw_year)
            except (TypeError, ValueError):
                continue
            if parsed > 2000:
                years.append(parsed)
        if years:
            return max(years)

    return snapshot_year - 1


def annual_report_recency(company: dict[str, Any], snapshot_year: int) -> dict[str, Any]:
    latest_year = latest_annual_report_year(company, snapshot_year)
    age = max(0, snapshot_year - latest_year)
    confidence = 1.0
    if age == 1:
        confidence = 0.82
    elif age == 2:
        confidence = 0.64
    elif age == 3:
        confidence = 0.46
    elif age >= 4:
        confidence = 0.30
    return {
        "latestYear": latest_year,
        "age": age,
        "confidence": confidence,
        "label": "current report" if age == 0 else f"{latest_year} report",
    }


def apply_annual_report_recency(raw_score: float, recency: dict[str, Any]) -> float:
    confidence = float(recency.get("confidence") or 1)
    uncertainty_penalty = (1 - confidence) * 18
    return clamp_score(50 + (raw_score - 50) * confidence - uncertainty_penalty)


def annual_report_intel_snapshot(company: dict[str, Any]) -> dict[str, Any]:
    hash_value = company_hash(company)
    keyword_values = ("ai", "cloud", "dataCenter", "regulation", "competition", "marginPressure")
    keywords = []
    for index, keyword in enumerate(keyword_values):
        base = 6 + ((hash_value + index * 13) % 28)
        slope = ((hash_value + index * 7) % 9) - 3
        latest = max(0, round(base + slope * 4 + ((hash_value + 4 + index) % 5)))
        keywords.append({"keyword": keyword, "latest": latest, "change": slope * 4})

    growth_terms = {"ai", "cloud", "dataCenter"}
    pressure_terms = {"regulation", "competition", "marginPressure", "restructuring"}
    growth_trend = sum(item["change"] for item in keywords if item["keyword"] in growth_terms)
    pressure_trend = sum(item["change"] for item in keywords if item["keyword"] in pressure_terms)
    return {
        "keywordTrend": growth_trend - pressure_trend * 0.35,
        "healthKeywordBalance": growth_trend - max(0, pressure_trend),
    }


def risk_level(company: dict[str, Any]) -> str:
    ticker = str(company.get("ticker") or "").upper()
    pe_ratio = company.get("peRatio")
    score = 0
    if isinstance(pe_ratio, (int, float)) and math.isfinite(pe_ratio):
        if pe_ratio >= 100:
            score = max(score, 3)
        elif pe_ratio >= 45:
            score = max(score, 2)
        elif pe_ratio >= 28:
            score = max(score, 1)
    if ticker in {"TSLA", "PLTR"}:
        score = max(score, 3)
    if ticker in {"NVDA", "AVGO", "ASML"}:
        score = max(score, 2)
    return ("low", "medium", "high", "ultra")[score]


def growth_components(company: dict[str, Any], snapshot_year: int) -> dict[str, Any]:
    hash_value = company_hash(company)
    one_year = previous_growth(company, "1y")
    three_year = previous_growth(company, "3y")
    global_rank = company.get("globalMarketCapRank")
    risk = risk_level(company)
    report_recency = annual_report_recency(company, snapshot_year)
    report_intel_loaded = has_annual_report_intel(company)
    intel = annual_report_intel_snapshot(company)

    momentum_score = clamp_score(
        50
        + (one_year * 0.45 if one_year is not None else ((hash_value % 30) - 10))
        + (three_year * 0.08 if three_year is not None else 0)
    )
    raw_report_score = (
        clamp_score(52 + intel["keywordTrend"] * 1.8 + intel["healthKeywordBalance"] * 1.2)
        if report_intel_loaded
        else 50
    )
    annual_report_score = apply_annual_report_recency(raw_report_score, report_recency) if report_intel_loaded else 50
    market_position_score = clamp_score(
        90 - min(55, math.log10(global_rank) * 18)
        if isinstance(global_rank, (int, float)) and global_rank > 0
        else 48 + (hash_value % 22)
    )
    risk_penalty = {"low": 8, "medium": 0, "high": -10, "ultra": -22}.get(risk, 0)
    risk_score = clamp_score(62 + risk_penalty)

    return {
        "momentum": round(momentum_score),
        "annualReportHealth": round(annual_report_score),
        "marketPosition": round(market_position_score),
        "riskBalance": round(risk_score),
        "annualReportIntelLoaded": report_intel_loaded,
        "annualReportRecency": report_recency,
    }


def decisive_score(base_score: float, components: dict[str, Any]) -> int:
    momentum_signal = components["momentum"] - 50
    market_signal = components["marketPosition"] - 50
    report_signal = components["annualReportHealth"] - 50
    risk_signal = components["riskBalance"] - 50
    blended_signal = (
        momentum_signal * 0.46
        + market_signal * 0.24
        + report_signal * 0.16
        + risk_signal * 0.14
    )
    stretched = 50 + blended_signal * 1.65
    anchored = stretched * 0.78 + base_score * 0.22
    return round(clamp_score(anchored))


def score_label(score: int) -> str:
    if score >= 76:
        return "Strong"
    if score >= 58:
        return "Positive"
    if score >= 42:
        return "Caution"
    return "Weak"


def growth_summary(score: int, components: dict[str, Any]) -> str:
    signals = [
        ("momentum", components["momentum"]),
        ("market position", components["marketPosition"]),
        ("annual reports", components["annualReportHealth"]),
        ("risk balance", components["riskBalance"]),
    ]
    strongest = sorted(signals, key=lambda item: abs(item[1] - 50), reverse=True)[:2]
    positive = [name for name, value in strongest if value >= 58]
    negative = [name for name, value in strongest if value <= 42]
    pending = not components.get("annualReportIntelLoaded")
    suffix = "; annual reports are neutral pending analysis" if pending else ""
    label = score_label(score)
    if positive:
        return f"{label}: lifted by {' and '.join(positive)}{suffix}."
    if negative:
        return f"{label}: held back by {' and '.join(negative)}{suffix}."
    return f"{label}: balanced signals{suffix}."


def company_observation(company: dict[str, Any], snapshot_date: date) -> dict[str, Any]:
    components = growth_components(company, snapshot_date.year)
    base_score = sum(components[key] * weight for key, weight in WEIGHTS.items())
    score = decisive_score(base_score, components)
    return {
        "date": snapshot_date.isoformat(),
        "slug": company.get("slug"),
        "companyName": company.get("companyName") or company.get("name"),
        "ticker": company.get("ticker"),
        "ftseRank": company.get("ftseRank"),
        "sharePrice": company.get("currentPrice"),
        "sharePriceDate": company.get("priceDate"),
        "marketCap": company.get("marketCap"),
        "marketCapValue": company.get("marketCapValue"),
        "globalMarketCapRank": company.get("globalMarketCapRank"),
        "growthConfidence": score,
        "growthConfidenceLabel": score_label(score),
        "growthConfidenceSummary": growth_summary(score, components),
        "components": components,
    }


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def add_forward_returns(history: dict[str, Any], latest_by_slug: dict[str, dict[str, Any]], latest_date: date) -> None:
    for company in history.get("companies", {}).values():
        observations = company.get("observations") or []
        for observation in observations:
            observation_date = parse_date(observation.get("date"))
            start_price = observation.get("sharePrice")
            latest_observation = latest_by_slug.get(observation.get("slug") or company.get("slug"))
            latest_price = latest_observation.get("sharePrice") if latest_observation else None
            if not observation_date or not isinstance(start_price, (int, float)) or not isinstance(latest_price, (int, float)):
                continue
            elapsed_days = (latest_date - observation_date).days
            forward_returns = observation.setdefault("forwardReturns", {})
            for days in FORWARD_RETURN_DAYS:
                if elapsed_days >= days:
                    value = ((latest_price - start_price) / start_price) * 100
                    forward_returns[f"{days}d"] = round(value, 2)


def build_history(snapshot: dict[str, Any]) -> dict[str, Any]:
    history = load_json(HISTORY_FILE, {"generatedAt": None, "methodology": {}, "companies": {}})
    companies = history.setdefault("companies", {})
    snapshot_date = parse_date(snapshot["date"]) or date.today()

    for observation in snapshot["companies"]:
        slug = observation.get("slug")
        if not slug:
            continue
        company_history = companies.setdefault(
            slug,
            {
                "slug": slug,
                "companyName": observation.get("companyName"),
                "ticker": observation.get("ticker"),
                "observations": [],
            },
        )
        company_history["companyName"] = observation.get("companyName")
        company_history["ticker"] = observation.get("ticker")
        existing = [item for item in company_history.get("observations", []) if item.get("date") != snapshot["date"]]
        existing.append(observation)
        company_history["observations"] = sorted(existing, key=lambda item: item.get("date", ""))

    latest_by_slug = {item["slug"]: item for item in snapshot["companies"] if item.get("slug")}
    add_forward_returns(history, latest_by_slug, snapshot_date)
    history["generatedAt"] = snapshot["generatedAt"]
    history["methodology"] = snapshot["methodology"]
    history["validationWindows"] = [f"{days}d" for days in FORWARD_RETURN_DAYS]
    return history


def build_snapshot(companies: list[dict[str, Any]], snapshot_date: date, source_file: Path) -> dict[str, Any]:
    observations = [company_observation(company, snapshot_date) for company in companies]
    return {
        "date": snapshot_date.isoformat(),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": source_file.name,
        "companyCount": len(observations),
        "methodology": {
            "name": "StockLayer growth confidence validation snapshot",
            "scoreRange": "0-100",
            "weights": WEIGHTS,
            "forwardReturnWindows": [f"{days}d" for days in FORWARD_RETURN_DAYS],
            "note": "Daily score snapshots are compared with later share-price observations once enough time has elapsed.",
        },
        "companies": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", type=Path, default=DEFAULT_COMPANIES_FILE)
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()

    snapshot_date = date.fromisoformat(args.date)
    source_file = args.companies
    if not source_file.exists() and args.companies == DEFAULT_COMPANIES_FILE and LOCAL_COMPANIES_FALLBACK.exists():
        source_file = LOCAL_COMPANIES_FALLBACK

    companies = load_json(source_file, [])
    if not isinstance(companies, list) or not companies:
        raise SystemExit(f"No companies found in {source_file}")

    snapshot = build_snapshot(companies, snapshot_date, source_file)
    daily_file = HISTORY_DIR / f"{snapshot_date.isoformat()}.json"
    history = build_history(snapshot)

    write_json(daily_file, snapshot)
    write_json(HISTORY_FILE, history)
    print(f"Snapshotted {len(snapshot['companies'])} companies to {daily_file.relative_to(ROOT)}")
    print(f"Updated rolling history at {HISTORY_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
