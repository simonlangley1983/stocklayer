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
    # No files changed until every issuer has passed checks.
    for name, data in [('uk-companies.json', companies), ('history/global-market-cap-ranks.json', result)]:
        path = args.output_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'matched':len(companies), 'universe':result['universeCount'], 'asOf':result['sourceAsOf']}))


if __name__ == '__main__':
    main()
