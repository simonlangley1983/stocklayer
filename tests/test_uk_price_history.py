import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from update_uk_price_history import calculate_returns, anniversary


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


if __name__ == '__main__':
    unittest.main()
