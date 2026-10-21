"""Refresh dated UK share-price returns. Split-adjusted, excluding dividends."""
import concurrent.futures
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def anniversary(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def calculate_returns(prices, current, as_of):
    values, evidence = {}, {}
    for years in (1, 2, 3, 5, 10):
        key = f'{years}y'
        target = anniversary(as_of, years)
        eligible = [p for p in prices if p['date'] <= target.isoformat()]
        prior = eligible[-1] if eligible else None
        age = (target - datetime.fromisoformat(prior['date']).date()).days if prior else 999
        if prior and 0 <= age <= 7 and prior['close'] > 0:
            values[key] = round((current / prior['close'] - 1) * 100, 6)
            evidence[key] = dict(targetDate=target.isoformat(), startDate=prior['date'],
                                 startPrice=prior['close'], endDate=as_of.isoformat(), endPrice=current)
        else:
            values[key] = None
    return values, evidence


def fetch_company(company):
    ticker = company['ticker']
    start = anniversary(datetime.now(timezone.utc).date(), 11)
    query = urllib.parse.urlencode(dict(
        period1=int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp()),
        period2=int(time.time()) + 86400, interval='1d'))
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?{query}'
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 StockLayer'})
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)['chart']['result'][0]
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    meta = result['meta']
    if meta.get('symbol', '').upper() != ticker.upper():
        raise ValueError(f'Unexpected symbol: {meta.get("symbol")}')
    raw_currency = meta.get('currency', '')
    scale = 100 if raw_currency in ('GBp', 'GBX', 'GBx', 'ILA', 'ZAc') else 1
    currency = {'GBp': 'GBP', 'GBX': 'GBP', 'GBx': 'GBP', 'ILA': 'ILS', 'ZAc': 'ZAR'}.get(raw_currency, raw_currency)
    closes = result['indicators']['quote'][0]['close']
    prices = [dict(date=datetime.fromtimestamp(ts, timezone.utc).date().isoformat(), close=round(close / scale, 8))
              for ts, close in zip(result['timestamp'], closes)
              if isinstance(close, (int, float)) and math.isfinite(close) and close > 0]
    prices = sorted({p['date']: p for p in prices}.values(), key=lambda p: p['date'])
    if not prices:
        raise ValueError('No valid prices')
    timestamp, current = meta.get('regularMarketTime'), meta.get('regularMarketPrice')
    if not timestamp or not isinstance(current, (int, float)) or current <= 0:
        raise ValueError('Missing current quote')
    current /= scale
    as_of = datetime.fromtimestamp(timestamp, timezone.utc)
    if (datetime.now(timezone.utc) - as_of).days > 7:
        raise ValueError(f'Stale quote: {as_of.isoformat()}')
    prior = [p for p in prices if p['date'] < as_of.date().isoformat()]
    previous = prior[-1]['close'] if prior else None
    returns, evidence = calculate_returns(prices, current, as_of.date())
    updated = dict(company, currentPrice=current, currency=currency, priceDate=as_of.isoformat(),
                   priceSource='Yahoo Finance chart API', previousClose=previous,
                   dayChange=round(current - previous, 8) if previous else None,
                   dayChangePercent=round((current / previous - 1) * 100, 6) if previous else None,
                   previousGrowth=returns, previousGrowthEvidence=evidence,
                   previousGrowthSource='Yahoo Finance split-adjusted closes; excludes dividends',
                   previousGrowthDate=as_of.isoformat())
    history = dict(ticker=ticker, currency=currency, lastUpdated=datetime.now(timezone.utc).isoformat(),
                   source='Yahoo Finance', sourceUrl=url, adjustment='Split-adjusted price; excludes dividends', prices=prices)
    return updated, history


def refresh(root, fetcher=fetch_company):
    companies = json.loads((root / 'uk-companies.json').read_text(encoding='utf-8'))
    results, errors = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetcher, c): c for c in companies}
        for future in concurrent.futures.as_completed(futures):
            company = futures[future]
            try:
                results[company['slug']] = future.result()
                print(f'OK {company["slug"]}', flush=True)
            except Exception as error:
                errors[company['slug']] = str(error)
                print(f'FAIL {company["slug"]}: {error}', flush=True)
    checked_at = datetime.now(timezone.utc).isoformat()
    # Keep failures dated and visible without freezing healthy companies.
    # A widespread provider outage must not replace the published dataset.
    if len(results) < math.ceil(len(companies) * 0.9):
        raise RuntimeError(f'Refresh not published: only {len(results)}/{len(companies)} valid quotes')
    # Refresh display conversion rates together with prices, instead of retaining stale FX.
    fx_rates = {}
    for updated, _ in results.values():
        display = updated.get('displayCurrency')
        currency = updated.get('currency')
        if not display or display == currency:
            continue
        pair = currency + display
        if pair not in fx_rates:
            request = urllib.request.Request(
                f'https://query1.finance.yahoo.com/v8/finance/chart/{pair}=X?range=1d&interval=1d',
                headers={'User-Agent': 'Mozilla/5.0 StockLayer'})
            with urllib.request.urlopen(request, timeout=30) as response:
                meta = json.load(response)['chart']['result'][0]['meta']
            rate = float(meta['regularMarketPrice'])
            stamp = datetime.fromtimestamp(meta['regularMarketTime'], timezone.utc)
            if rate <= 0 or (datetime.now(timezone.utc) - stamp).days > 7:
                raise ValueError(f'Invalid or stale FX for {pair}')
            fx_rates[pair] = rate, stamp.isoformat()
        updated['displayFxRate'], updated['displayFxRateDate'] = fx_rates[pair]
        updated['displayFxRateSource'] = 'Yahoo Finance chart API'
    (root / 'history').mkdir(exist_ok=True)
    for slug, (company, history) in results.items():
        company.update(priceStatus='current', priceCheckedAt=checked_at)
        company.pop('priceRefreshError', None)
        (root / 'history' / f'{slug}-history.json').write_text(json.dumps(history, separators=(',', ':')) + '\n', encoding='utf-8')
    updated = [results[c['slug']][0] if c['slug'] in results else
               dict(c, priceStatus='stale', priceCheckedAt=checked_at,
                    priceRefreshError=errors[c['slug']]) for c in companies]
    (root / 'uk-companies.json').write_text(json.dumps(updated, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    sync_legacy_quotes(root, updated)
    status = dict(checkedAt=checked_at, status='degraded' if errors else 'current',
                  updated=len(results), total=len(companies), errors=errors)
    (root / 'uk-price-refresh-status.json').write_text(json.dumps(status, indent=2) + '\n', encoding='utf-8')
    print(f'Updated {len(updated)} UK companies and histories', flush=True)
    return status


def sync_legacy_quotes(root, companies):
    """Keep deployed older pages on the same dated quote feed."""
    fields = ('currentPrice', 'currency', 'priceDate', 'priceSource', 'priceStatus',
              'priceCheckedAt', 'priceRefreshError', 'previousClose', 'dayChange',
              'dayChangePercent', 'previousGrowth', 'previousGrowthDate',
              'previousGrowthEvidence', 'previousGrowthSource', 'marketCap',
              'marketCapValue', 'marketCapDate', 'marketCapSource',
              'globalMarketCapRank', 'globalMarketCapRankDate', 'globalMarketCapRankStatus')
    by_slug = {c['slug']: c for c in companies}
    snapshot = root / 'ftse100.json'
    if snapshot.exists():
        saved = json.loads(snapshot.read_text(encoding='utf-8'))
        for company in saved:
            latest = by_slug.get(company.get('slug'))
            if latest:
                for field in fields:
                    if field in latest:
                        company[field] = latest[field]
                    elif field == 'priceRefreshError':
                        company.pop(field, None)
        snapshot.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    for company in companies:
        detail = root / 'companies' / (company['slug'] + '.json')
        if detail.exists():
            saved = json.loads(detail.read_text(encoding='utf-8'))
            for field in fields:
                if field in company:
                    saved[field] = company[field]
                elif field == 'priceRefreshError':
                    saved.pop(field, None)
            detail.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def main():
    return refresh(Path(__file__).resolve().parents[1])


if __name__ == '__main__':
    main()

