import sys
import unittest
import json
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from update_uk_price_history import calculate_returns, anniversary, refresh


class ReturnTests(unittest.TestCase):
    def test_calendar_anniversary_and_weekend(self):
        prices = [{'date': '2025-09-05', 'close': 100}, {'date': '2025-09-08', 'close': 200}]
        values, evidence = calculate_returns(prices, 125, date(2026, 9, 7))
        self.assertEqual(values['1y'], 25)
        self.assertEqual(evidence['1y']['startDate'], '2025-09-05')

    def test_short_history_is_missing_not_zero(self):
        values, evidence = calculate_returns([{'date': '2026-01-01', 'close': 100}], 120, date(2026, 9, 9))
        self.assertTrue(all(value is None for value in values.values()))
        self.assertEqual(evidence, {})

    def test_stale_baseline_is_not_substituted(self):
        values, _ = calculate_returns([{'date': '2025-08-01', 'close': 100}], 120, date(2026, 9, 9))
        self.assertIsNone(values['1y'])

    def test_leap_day(self):
        self.assertEqual(anniversary(date(2024, 2, 29), 1), date(2023, 2, 28))

    def test_negative_returns_are_preserved(self):
        values, _ = calculate_returns([{'date': '2025-09-09', 'close': 100}], 80, date(2026, 9, 9))
        self.assertEqual(values['1y'], -20)


class RefreshTests(unittest.TestCase):
    def test_failed_issuer_does_not_freeze_healthy_quotes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = [dict(slug=str(i), ticker=str(i), currentPrice=10,
                             priceDate='2026-10-02', priceRefreshError='old error') for i in range(100)]
            (root / 'uk-companies.json').write_text(json.dumps(original))
            def fetch(company):
                if company['slug'] in ('1', '2'):
                    raise ValueError('Stale provider quote')
                return dict(company, currentPrice=20, priceDate='2026-10-21'), {'prices': []}
            status = refresh(root, fetch)
            saved = json.loads((root / 'uk-companies.json').read_text())
            self.assertEqual(status['updated'], 98)
            self.assertEqual(saved[0]['currentPrice'], 20)
            self.assertNotIn('priceRefreshError', saved[0])
            self.assertEqual(saved[1]['currentPrice'], 10)
            self.assertEqual(saved[1]['priceDate'], '2026-10-02')
            self.assertEqual(saved[1]['priceStatus'], 'stale')
            self.assertFalse((root / 'history/1-history.json').exists())
            self.assertTrue((root / 'history/0-history.json').exists())

    def test_provider_outage_preserves_published_dataset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = json.dumps([dict(slug=str(i)) for i in range(10)])
            (root / 'uk-companies.json').write_text(source)
            def fail(company):
                raise ValueError('Provider outage')
            with self.assertRaises(RuntimeError):
                refresh(root, fail)
            self.assertEqual((root / 'uk-companies.json').read_text(), source)
            self.assertFalse((root / 'history').exists())


if __name__ == '__main__':
    unittest.main()

