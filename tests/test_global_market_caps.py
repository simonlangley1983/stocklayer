import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from update_global_market_caps import build, fresh, positive, select_record, add_year_comparison, historical_record

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


class GlobalMarketCapTests(unittest.TestCase):
    def test_ticker_collision_rejected(self):
        company = dict(slug='anglo-american', companyName='Anglo American PLC', ticker='AAL.L')
        wrong = dict(Name='American Airlines', Ticker='AAL.L', MarketCap=100, LastSeen=NOW.isoformat())
        self.assertIsNone(select_record(company, [wrong], NOW))

    def test_exact_name_and_exchange(self):
        company = dict(slug='bp', companyName='BP PLC', ticker='BP.L')
        record = dict(Name='BP p.l.c', Ticker='BP.L', MarketCap=100, LastSeen=NOW.isoformat())
        self.assertEqual(select_record(company, [record], NOW), record)

    def test_stale_and_invalid_values(self):
        self.assertFalse(fresh('2026-08-06', NOW))
        self.assertFalse(fresh('2030-01-01', NOW))
        for value in (0, -1, None, float('nan'), float('inf'), True):
            self.assertFalse(positive(value))

    def test_incomplete_universe_fails(self):
        with self.assertRaises(ValueError):
            build([], {}, {'metadata':{'asOf':NOW.isoformat()}, 'data':[]}, NOW)

    def test_dual_listing_not_guessed(self):
        company = dict(slug='investec', companyName='Investec PLC', ticker='INVP.L')
        record = dict(Name='Investec', Ticker='ITCFY', MarketCap=100, LastSeen=NOW.isoformat())
        self.assertIsNone(select_record(company, [record], NOW))

    def test_complete_snapshot_and_major_currency_units(self):
        companies = [dict(slug='admiral', companyName='Admiral Group PLC', ticker='ADM.L')]
        records = [dict(Name=f'Issuer {i}', AssetType='Company', Ticker=f'{i}.X',
                        MarketCap=100000+i, LastSeen=NOW.isoformat()) for i in range(10001)]
        def quote(ticker):
            return (dict(regularMarketPrice=1.3, regularMarketTime=NOW.timestamp()) if ticker == 'GBPUSD=X'
                    else dict(longName='Admiral Group plc', marketCap=1000, currency='GBp', regularMarketTime=NOW.timestamp()))
        result = build(companies, {}, {'metadata':{'asOf':NOW.isoformat()}, 'data':records}, NOW, quote)
        self.assertEqual(result['coverage']['current'], 1)
        self.assertEqual(result['companies']['admiral']['marketCapsUsd']['current'], 1300)
        self.assertEqual(companies[0]['globalMarketCapRank'], 10002)
        self.assertFalse(result['rankHistoryComparable'])

    def test_missing_issuer_stops_publication(self):
        companies = [dict(slug='admiral', companyName='Admiral Group PLC', ticker='ADM.L')]
        records = [dict(Name=f'Issuer {i}', AssetType='Company', MarketCap=100+i,
                        LastSeen=NOW.isoformat()) for i in range(10001)]
        with self.assertRaises(ValueError):
            build(companies, {}, {'metadata':{'asOf':NOW.isoformat()}, 'data':records}, NOW, lambda _: {})

    def test_historical_rank_and_signed_movement_survive_refresh(self):
        company = dict(slug='beazley', companyName='Beazley PLC', ticker='BEZ.L')
        day = '2025-10-03'
        records = [dict(Name=f'Issuer {i}', AssetType='Company', MarketCap=100+i,
                        LastSeen=day) for i in range(10001)]
        records.append(dict(Name='Beazley', AssetType='Company', Ticker='BEZ.L', MarketCap=200, LastSeen=day))
        result = dict(companies={'beazley':dict(ranks={'current':9900},marketCapsUsd={'current':250},marketCapDates={})},
                      dates={},coverage={},unmatched={})
        add_year_comparison([company], result, {'metadata':{'asOf':day},'data':records}, day)
        self.assertEqual(result['coverage']['1y'], 1)
        self.assertEqual(result['companies']['beazley']['ranks']['1y'], 9901)
        self.assertEqual(company['globalMarketCapRankChange'], 1)
        self.assertTrue(result['rankHistoryComparable'])

    def test_historical_aberdeen_does_not_match_canadian_namesake(self):
        company = dict(slug='aberdeen-group',companyName='Aberdeen Group PLC',ticker='ABDN.L')
        correct = dict(Name='ABRDN',Ticker='SLFPF',MarketCap=4800000000,LastSeen=NOW.isoformat())
        wrong = dict(Name='Aberdeen',Ticker='AABVF',MarketCap=3200000,LastSeen=NOW.isoformat())
        self.assertEqual(historical_record(company,[wrong,correct],NOW),correct)


if __name__ == '__main__':
    unittest.main()
