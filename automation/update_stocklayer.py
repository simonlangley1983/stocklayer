"""
StockLayer automated data updater.

This script is designed to run from GitHub Actions.

What it does first:
- Reads companies.json
- Uses each company's ticker
- Updates companies/{slug}.json with current price, P/E ratio and dividend yield where available
- Updates history/{slug}-history.json with available daily price history
- Writes everything back to JSON so the static site can render it

It deliberately does not touch:
- ai/
- risk/
- leadership/
- acquisitions/
- events/

Those are StockLayer judgement/content layers and can be automated later once the core market data pipeline is stable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
COMPANIES_INDEX = ROOT / "companies.json"
COMPANIES_DIR = ROOT / "companies"
HISTORY_DIR = ROOT / "history"


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
        return float(value)
    except (TypeError, ValueError):
        return None


def round_or_none(value: Any, decimals: int = 2) -> Optional[float]:
    number = safe_float(value)
    if number is None:
        return None
    return round(number, decimals)


def format_market_cap(value: Any) -> Optional[str]:
    number = safe_float(value)

    if number is None:
        return None

    if number >= 1_000_000_000_000:
        return f"{number / 1_000_000_000_000:.2f}T"

    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"

    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"

    return str(round(number, 2))


def normalise_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def get_company_file(slug: str) -> Path:
    return COMPANIES_DIR / f"{slug}.json"


def get_history_file(slug: str) -> Path:
    return HISTORY_DIR / f"{slug}-history.json"


def get_fast_info(ticker: str) -> Dict[str, Any]:
    stock = yf.Ticker(ticker)

    try:
        fast_info = dict(stock.fast_info)
    except Exception as exc:
        print(f"WARNING: Could not read fast_info for {ticker}: {exc}")
        fast_info = {}

    try:
        info = stock.info or {}
    except Exception as exc:
        print(f"WARNING: Could not read info for {ticker}: {exc}")
        info = {}

    return {
        "currentPrice": (
            fast_info.get("lastPrice")
            or info.get("currentPrice")
            or info.get("regularMarketPrice")
        ),
        "marketCapRaw": fast_info.get("marketCap") or info.get("marketCap"),
        "peRatio": info.get("trailingPE"),
        "forwardPeRatio": info.get("forwardPE"),
        "dividendYield": info.get("dividendYield"),
        "currency": fast_info.get("currency") or info.get("currency") or "USD",
    }


def update_company_json(index_entry: Dict[str, Any]) -> None:
    slug = index_entry.get("slug")
    ticker = index_entry.get("ticker")

    if not slug or not ticker:
        print(f"Skipping company with missing slug/ticker: {index_entry}")
        return

    ticker = normalise_ticker(ticker)
    company_path = get_company_file(slug)

    company = read_json(company_path, {})
    company = {**index_entry, **company}

    print(f"Updating company data: {ticker} ({slug})")

    market_data = get_fast_info(ticker)

    current_price = round_or_none(market_data.get("currentPrice"))
    market_cap = format_market_cap(market_data.get("marketCapRaw"))
    pe_ratio = round_or_none(market_data.get("peRatio"))
    dividend_yield_raw = safe_float(market_data.get("dividendYield"))

    if current_price is not None:
        company["currentPrice"] = current_price

    if market_cap is not None:
        company["marketCap"] = market_cap

    if pe_ratio is not None:
        company["peRatio"] = pe_ratio

    if dividend_yield_raw is not None:
        # yfinance usually returns dividend yield as a decimal, e.g. 0.0186
        company["dividendYield"] = round(dividend_yield_raw * 100, 2)

    company["ticker"] = ticker
    company["slug"] = slug
    company["currency"] = market_data.get("currency") or company.get("currency", "USD")
    company["lastUpdated"] = datetime.now(timezone.utc).isoformat()

    write_json(company_path, company)


def update_history_json(index_entry: Dict[str, Any]) -> None:
    slug = index_entry.get("slug")
    ticker = index_entry.get("ticker")

    if not slug or not ticker:
        return

    ticker = normalise_ticker(ticker)
    history_path = get_history_file(slug)

    print(f"Updating history data: {ticker} ({slug})")

    stock = yf.Ticker(ticker)

    try:
        # max is useful for Cisco acquisitions back to the 1990s.
        history = stock.history(period="max", interval="1d", auto_adjust=False)
    except Exception as exc:
        print(f"WARNING: Could not download history for {ticker}: {exc}")
        return

    if history.empty:
        print(f"WARNING: Empty history returned for {ticker}")
        return

    prices: List[Dict[str, Any]] = []

    for date, row in history.iterrows():
        close = row.get("Close")

        if close is None:
            continue

        close_number = safe_float(close)

        if close_number is None:
            continue

        prices.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "close": round(close_number, 4),
            }
        )

    data = {
        "ticker": ticker,
        "currency": "USD",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "prices": prices,
    }

    write_json(history_path, data)


def update_companies_index(companies: List[Dict[str, Any]]) -> None:
    updated_entries = []

    for entry in companies:
        slug = entry.get("slug")

        if not slug:
            updated_entries.append(entry)
            continue

        company = read_json(get_company_file(slug), {})
        updated_entry = {**entry}

        for key in [
            "companyName",
            "ticker",
            "slug",
            "sector",
            "industry",
            "domain",
            "currency",
            "currentPrice",
            "marketCap",
            "marketCapRank",
            "peRatio",
            "dividendYield",
            "volatilityRating",
            "volatilityLabel",
        ]:
            if key in company:
                updated_entry[key] = company[key]

        updated_entries.append(updated_entry)

    write_json(COMPANIES_INDEX, updated_entries)


def main() -> None:
    companies = read_json(COMPANIES_INDEX, [])

    if not isinstance(companies, list) or not companies:
        raise RuntimeError("companies.json is missing or does not contain a company list")

    COMPANIES_DIR.mkdir(exist_ok=True)
    HISTORY_DIR.mkdir(exist_ok=True)

    for entry in companies:
        update_company_json(entry)
        update_history_json(entry)

    update_companies_index(companies)

    print("StockLayer update complete.")


if __name__ == "__main__":
    main()
