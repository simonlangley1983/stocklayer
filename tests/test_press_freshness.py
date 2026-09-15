import unittest
from automation.build_company_reports import build_overall_confidence

class PressFreshnessTests(unittest.TestCase):
    def score(self, rows):
        return build_overall_confidence({"series": rows}, {}, "2026-09-15")["score"]

    def test_recent_news_moves_score_in_both_directions(self):
        baseline = [{"date": "2026-09-10", "dailyScore": 50}]
        negative = self.score(baseline + [{"date": "2026-09-14", "dailyScore": 10}])
        positive = self.score(baseline + [{"date": "2026-09-14", "dailyScore": 90}])
        self.assertLess(negative, 50)
        self.assertGreater(positive, 50)

    def test_recent_evidence_outweighs_old_evidence(self):
        self.assertGreater(self.score([{"date":"2026-08-20", "dailyScore":10}, {"date":"2026-09-14", "dailyScore":90}]), 50)

    def test_stale_missing_invalid_and_future_evidence_are_excluded(self):
        for row in [{"date":"2026-08-01", "dailyScore":90}, {"date":"2026-09-16", "dailyScore":90}, {"date":"2026-09-14", "dailyScore":None}, {"dailyScore":90}, {"date":"2026-09-14", "dailyScore":float('nan')}]:
            with self.subTest(row=row):
                self.assertIsNone(self.score([row]))
