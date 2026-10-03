"""Refresh issuer market-cap ranks with identity, freshness and coverage checks.

Ranks describe the covered provider universe, not every company in existence.
Historic mixed-provider ranks are retained as evidence but not compared to this
new series. Never publish a partial snapshot or label an old value as current.
"""
import argparse
import base64
import copy
import json
import math
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from curl_cffi import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://assetmarketcap.com/data/daily.json'
REPO = 'repos/simonlangley1983/stocklayer'
ALIASES = {
    '3i': ['3i Group Ord'], 'auto-trader': ['Autotrader'],
    'british-american-tobacco': ['British American Tobacco p.l.c'],
    'bp': ['BP p.l.c'], 'lseg': ['London Stock Exchange'],
    'lloyds': ['Lloyds Banking'], 'alliance-witan': ['Alliance Witan Ord'],
    'croda-international': ['Croda'], 'dcc': ['DCC Energy'],
    'fandc-investment-trust': ['F&C Investment Trust Ord'],
    'intermediate-capital': ['ICG'],
    'international-consolidated-airlines': ['International Consolidated Airlines Group S.A'],
    'sage': ['The Sage'], 'scottish-mortgage-investment-trust': ['Scottish Mortgage Ord'],
    'vodafone': ['Vodafone Group Public'],
    'polar-capital-technology-trust': ['Polar Capital Technology Ord'],
    'ig-group': ['IG'], 'tritax-big-box': ['Tritax Big Box Ord'],
    'babcock-international': ['Babcock'], 'phoenix': ['Standard Life'],
    'investec': ['Investec Group'],
}
# Investec's dual-listed entities/ADRs have different capitalisations: use the
# requested London security, never an arbitrary same-name overseas record.
YAHOO_ONLY = {'investec'}

# Reviewed historical listing identities: never match a naked ticker to an
# unrelated issuer (e.g. PRU is Prudential Financial in the US).
HISTORICAL_IDENTITIES = {
    'astrazeneca': ('ZEG.DE', 'AstraZeneca PLC R'),
    'lloyds': ('LYG', 'Lloyds Banking Group Plc American'),
    'barratt-redrow': ('BTDPF', 'Barratt Developments'),
    'fandc-investment-trust': ('FCT.NZ', 'F&C Investment Trust Ordinary S'),
    'international-consolidated-airlines': ('BABWF', 'International Consolidated Airl'),
    'segro': ('SEGXF', 'Segro Plc REIT'),
    'smith-and-nephew': ('SNN', 'Smith & Nephew SNATS'),
    'st-jamess-place': ('STJPF', 'St. James Place Capital'),
    'lion-finance-group': ('BDGSF', 'BANK OF GEORGIA'),
    'aberdeen-group': ('SLFPF', 'ABRDN'),
    'auto-trader': ('ATDRF', 'AUTO TRADER'),
    'investec': ('IVTJF', 'Investec'),
}


def historical_record(company, records, as_of):
    identity = HISTORICAL_IDENTITIES.get(company['slug'])
    if identity:
        matches = [r for r in records if r.get('Ticker') == identity[0]
                   and normalise(r.get('Name', '')) == normalise(identity[1])]
        if len(matches) == 1 and positive(matches[0].get('MarketCap')) and fresh(matches[0].get('LastSeen'), as_of):
            return matches[0]
    return select_record(company, records, as_of)


def historical_fallback(company, day):
    cutoff = datetime.fromisoformat(day + 'T23:59:59+00:00')
    if company['slug'] == 'jd-sports-fashion':
        url = 'https://companiesmarketcap.com/jd-sports/marketcap/'
        response = requests.get(url, timeout=40)
        response.raise_for_status()
        if 'JD Sports' not in response.text:
            raise ValueError('JD Sports historical source identity mismatch')
        match = re.search(r'\bdata\s*=\s*(\[.*?\])\s*;', response.text, re.S)
        points = json.loads(match[1]) if match else []
        points = [p for p in points if p['d'] <= cutoff.timestamp() and positive(p.get('m'))]
        point = max(points, key=lambda p:p['d']) if points else None
        if not point or cutoff.timestamp() - point['d'] > 14*86400:
            raise ValueError('No timely historical JD Sports market cap')
        return dict(marketCapUsd=point['m']*100000, observedAt=datetime.fromtimestamp(point['d'], timezone.utc).isoformat(),
                    source=url, providerName='JD Sports Fashion', providerTicker='JD.L', matchMethod='dated-market-cap-series')
    if company['slug'] == 'phoenix':
        # Use dated outstanding shares, not today's count applied backwards.
        start = (cutoff-timedelta(days=45)).date().isoformat()
        end = (cutoff+timedelta(days=1)).date().isoformat()
        shares = yf.Ticker('PNXGF').get_shares_full(start=start, end=end)
        if shares is None or shares.empty:
            raise ValueError('Historical Standard Life share count unavailable')
        shares = shares[shares.index.tz_convert('UTC') <= cutoff]
        if shares.empty:
            raise ValueError('No share count on/before target date')
        shares = shares.sort_index()
        def close(ticker):
            prices = yf.Ticker(ticker).history(start=(cutoff-timedelta(days=7)).date().isoformat(), end=end, auto_adjust=False)
            if prices.empty:
                raise ValueError('Historical close missing: '+ticker)
            return float(prices.iloc[-1]['Close']), prices.index[-1].date().isoformat()
        price, price_day = close('SDLF.L')
        fx, fx_day = close('GBPUSD=X')
        count = float(shares.iloc[-1])
        return dict(marketCapUsd=count*price/100*fx, observedAt=price_day,
                    source='https://finance.yahoo.com/quote/SDLF.L/history/', providerName='Standard Life',
                    providerTicker='SDLF.L', matchMethod='historical-shares-times-unadjusted-close',
                    shares=count, sharesObservedAt=shares.index[-1].isoformat(), closeGBp=price,
                    fxRate=fx, fxObservedAt=fx_day, estimated=True)
    raise ValueError('Unmatched historical issuer: '+company['slug'])


def add_year_comparison(companies, result, snapshot, day):
    as_of = datetime.fromisoformat(day+'T23:59:59+00:00')
    if str(snapshot.get('metadata', {}).get('asOf', ''))[:10] != day:
        raise ValueError('Wrong historical snapshot date')
    records = [r for r in snapshot['data'] if r.get('AssetType') == 'Company']
    universe = {}
    for r in records:
        key = normalise(r.get('Name', ''))
        if key and positive(r.get('MarketCap')) and fresh(r.get('LastSeen'), as_of):
            universe[key] = max(r['MarketCap'], universe.get(key, 0))
    if len(universe) < 10000:
        raise ValueError('Incomplete historical universe')
    observations = {}
    for company in companies:
        record = historical_record(company, records, as_of)
        if record:
            obs = dict(marketCapUsd=record['MarketCap'], observedAt=record['LastSeen'],
                       source='https://assetmarketcap.com/data/history/'+day,
                       providerName=record['Name'], providerTicker=record['Ticker'], matchMethod='verified-historical-issuer')
        else:
            obs = historical_fallback(company, day)
        current_cap = result['companies'][company['slug']]['marketCapsUsd']['current']
        if not positive(obs['marketCapUsd']) or not 0.02 < obs['marketCapUsd']/current_cap < 50:
            raise ValueError('Historical market cap sanity check: '+company['slug'])
        observations[company['slug']] = obs
        aliases = names(company) | {normalise(obs['providerName'])}
        if company['slug'] in HISTORICAL_IDENTITIES:
            aliases.add(normalise(HISTORICAL_IDENTITIES[company['slug']][1]))
        # Preserve the unrelated Canadian Aberdeen issuer rather than treating
        # its small cap as the UK asset manager's prior capitalisation.
        if company['slug'] == 'aberdeen-group':
            aliases.discard('aberdeen')
        for alias in aliases:
            universe.pop(alias, None)
        universe['stocklayer:'+company['slug']] = obs['marketCapUsd']
    for company in companies:
        obs = observations[company['slug']]
        rank = 1 + sum(cap > obs['marketCapUsd'] for cap in universe.values())
        entry = result['companies'][company['slug']]
        entry['ranks']['1y'] = rank
        entry['marketCapsUsd']['1y'] = obs['marketCapUsd']
        entry['marketCapDates']['1y'] = obs['observedAt'][:10]
        entry['historicalEvidence'] = {'1y':obs}
        company['globalMarketCapRankChange'] = rank-entry['ranks']['current']
        company['globalMarketCapRankHistoryComparable'] = True
    result['dates']['1y'] = day
    result['coverage']['1y'] = len(companies)
    result['unmatched']['1y'] = []
    result['rankHistoryComparable'] = True
    result['historicalUniverseCount'] = {'1y':len(universe)}
    result['methodology'] = ('Current and 12-month USD issuer market caps ranked within each dated, fresh, name-deduplicated AssetMarketCap company universe, supplemented with dated verified issuer values. Provider coverage changes over time. Standard Life historical market cap is estimated from dated shares and unadjusted price; JD Sports uses a dated market-cap series. Older mixed-method comparisons remain archived.')


def normalise(name):
    name = re.sub(r'\bp\.l\.c\b', 'plc', name.lower()).replace('&', ' and ')
    words = re.sub(r'[^a-z0-9]+', ' ', name).split()
    return ' '.join(w for w in words if w not in {
        'plc', 'group', 'holdings', 'holding', 'limited', 'ltd', 'inc',
        'corporation', 'corp', 'company', 'co', 'sa', 'ag', 'nv',
    })


def positive(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def fresh(value, now):
    if not value:
        return False
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
        return -timedelta(hours=1) <= now - stamp <= timedelta(days=5)
    except ValueError:
        return False


def names(company):
    return {normalise(n) for n in [company['companyName'], *ALIASES.get(company['slug'], [])]}


def select_record(company, records, now):
    if company['slug'] in YAHOO_ONLY:
        return None
    allowed = names(company)
    matches = [r for r in records if normalise(r.get('Name', '')) in allowed
               and positive(r.get('MarketCap')) and fresh(r.get('LastSeen'), now)]
    exact = [r for r in matches if r.get('Ticker') == company['ticker']]
    if len(exact) == 1:
        return exact[0]
    # Only a single issuer listing is safe as an alternate-ticker match.
    return matches[0] if len(matches) == 1 else None


def api(path, data=None, method='POST'):
    command = ['gh', 'api', REPO + '/' + path]
    if data is not None:
        command += ['--method', method, '--input', '-']
    result = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                            capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


def remote_json(path):
    row = api('contents/' + path)
    if not row.get('content'):
        row = api('git/blobs/' + row['sha'])
    return json.loads(base64.b64decode(row['content']))


def build(companies, old, snapshot, now, quote_loader=None):
    if not fresh(snapshot.get('metadata', {}).get('asOf'), now):
        raise ValueError('Provider snapshot missing or stale')
    records = [r for r in snapshot['data'] if r.get('AssetType') == 'Company']
    universe = {}
    for record in records:
        key = normalise(record.get('Name', ''))
        cap = record.get('MarketCap')
        if key and positive(cap) and fresh(record.get('LastSeen'), now):
            universe[key] = max(cap, universe.get(key, 0))
    if len(universe) < 10000:
        raise ValueError('Provider universe unexpectedly incomplete')
    quote_loader = quote_loader or (lambda ticker: yf.Ticker(ticker).get_info())
    fx_info = None
    observations = {}
    for company in companies:
        record = select_record(company, records, now)
        if record:
            observation = dict(marketCapUsd=record['MarketCap'], source=SOURCE,
                               providerName=record['Name'], providerTicker=record['Ticker'],
                               observedAt=record['LastSeen'], matchMethod='verified-name-and-listing')
        else:
            info = quote_loader(company['ticker'])
            if not ({normalise(info.get('longName', '')), normalise(info.get('shortName', ''))} & names(company)):
                raise ValueError('Yahoo issuer identity mismatch: ' + company['slug'])
            cap = info.get('marketCap')
            stamp = datetime.fromtimestamp(info.get('regularMarketTime', 0), timezone.utc).isoformat()
            if not positive(cap) or not fresh(stamp, now):
                raise ValueError('Missing/stale Yahoo cap: ' + company['slug'])
            currency = info.get('currency')
            # Yahoo marketCap is in major units even when its share price is GBp.
            if currency in ('GBp', 'GBX', 'GBP'):
                fx_info = fx_info or quote_loader('GBPUSD=X')
                rate = fx_info.get('regularMarketPrice')
                fx_stamp = datetime.fromtimestamp(fx_info.get('regularMarketTime', 0), timezone.utc).isoformat()
                if not positive(rate) or not fresh(fx_stamp, now):
                    raise ValueError('Missing/stale GBP/USD conversion')
                usd = cap * rate
            elif currency == 'USD':
                usd = cap
            else:
                raise ValueError('Unsupported market cap currency: ' + str(currency))
            observation = dict(marketCapUsd=usd, source='https://finance.yahoo.com/quote/' + company['ticker'] + '/',
                               providerName=info['longName'], providerTicker=company['ticker'],
                               observedAt=stamp, matchMethod='verified-yahoo-issuer',
                               nativeMarketCap=cap, nativeCurrency='GBP' if currency in ('GBp', 'GBX') else currency)
            if currency in ('GBp', 'GBX', 'GBP'):
                observation.update(fxRate=rate, fxObservedAt=fx_stamp)
        observations[company['slug']] = observation
        # Replace every known alias once, so multiple listings do not count as
        # multiple issuers or make an issuer rank against a second copy of itself.
        for key in names(company) | {normalise(observation['providerName'])}:
            universe.pop(key, None)
        universe['stocklayer:' + company['slug']] = observation['marketCapUsd']
    caps = list(universe.values())
    result = copy.deepcopy(old)
    # Keep the old series intact as archived evidence. Do not expose mixed-date,
    # mixed-universe comparisons to older deployed website versions either.
    if 'historicalArchive' not in result:
        result['historicalArchive'] = {k: copy.deepcopy(old.get(k)) for k in
                                       ('generatedAt', 'source', 'dates', 'coverage', 'companies')}
    result.update(generatedAt=now.isoformat(), source='AssetMarketCap / Yahoo Finance',
                  currentSource='AssetMarketCap / Yahoo Finance', rankHistoryComparable=False,
                  methodology='Current USD issuer market caps ranked within the fresh, name-deduplicated AssetMarketCap company universe, supplemented by verified Yahoo Finance issuers. Provider-universe rank, not an exhaustive worldwide census. Historical ranks use an older methodology and are not comparable.',
                  universeCount=len(caps), sourceAsOf=snapshot['metadata']['asOf'])
    result.setdefault('dates', {})['current'] = snapshot['metadata']['asOf'][:10]
    result.setdefault('coverage', {})['current'] = len(companies)
    result.setdefault('unmatched', {})['current'] = []
    for period in ('1y', '2y', '3y', '5y', '10y'):
        result['coverage'][period] = 0
        result['unmatched'][period] = [c['slug'] for c in companies]
    entries = result.setdefault('companies', {})
    for entry in entries.values():
        for field in ('ranks', 'marketCapsUsd', 'marketCapDates'):
            for period in ('1y', '2y', '3y', '5y', '10y'):
                entry.setdefault(field, {})[period] = None
    for company in companies:
        obs = observations[company['slug']]
        rank = 1 + sum(cap > obs['marketCapUsd'] for cap in caps)
        entry = entries.setdefault(company['slug'], {})
        entry.update(companyName=company['companyName'], ticker=company['ticker'], currentEvidence=obs)
        entry.setdefault('ranks', {})['current'] = rank
        entry.setdefault('marketCapsUsd', {})['current'] = obs['marketCapUsd']
        entry.setdefault('marketCapDates', {})['current'] = obs['observedAt'][:10]
        company.update(globalMarketCapRank=rank, globalMarketCapRankSource=result['source'],
                       globalMarketCapRankDate=obs['observedAt'], globalMarketCapRankChange=None,
                       globalMarketCapRankHistoryComparable=False, globalMarketCapUsd=obs['marketCapUsd'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote-input', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=ROOT)
    args = parser.parse_args()
    read = remote_json if args.remote_input else lambda p: json.loads((ROOT / p).read_text(encoding='utf-8'))
    companies = read('uk-companies.json')
    old = read('history/global-market-cap-ranks.json')
    response = requests.get(SOURCE, timeout=60)
    response.raise_for_status()
    result = build(companies, old, response.json(), datetime.now(timezone.utc))
    today = datetime.fromisoformat(result['dates']['current']).date()
    try:
        prior_day = today.replace(year=today.year-1).isoformat()
    except ValueError:
        prior_day = today.replace(year=today.year-1, day=28).isoformat()
    prior = requests.get('https://assetmarketcap.com/data/history/'+prior_day, timeout=60)
    prior.raise_for_status()
    add_year_comparison(companies, result, prior.json(), prior_day)
    # No files changed until every issuer has passed checks.
    for name, data in [('uk-companies.json', companies), ('history/global-market-cap-ranks.json', result)]:
        path = args.output_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'matched':len(companies), 'universe':result['universeCount'], 'asOf':result['sourceAsOf']}))


if __name__ == '__main__':
    main()
