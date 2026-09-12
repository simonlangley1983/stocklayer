import unittest
from datetime import date

from automation.news_recovery import audit, plan_windows
from automation.news_sentiment import missing_day_windows


class NewsRecoveryQueueTests(unittest.TestCase):
    def setUp(self):
        self.companies = [{"slug": "a", "companyName": "A"}, {"slug": "b", "companyName": "B"}]

    def test_no_news_is_complete_but_errors_and_partial_fetches_are_not(self):
        observations = [
            {"date": "2026-09-08", "coverageStatus": "no_coverage"},
            {"date": "2026-09-09", "coverageStatus": "provider_error"},
            {"date": "2026-09-10", "coverageStatus": "ok", "collectionCompleteness": .75},
            {"date": "2026-09-11", "coverageStatus": "ok"},
        ]
        result = audit(self.companies[:1], {"a": observations}, date(2026, 9, 11), 4)
        self.assertEqual(result["completedCompanyDays"], 2)
        self.assertEqual(result["pendingCompanyDays"], 2)
        self.assertEqual(result["noNewsCompanyDays"], 1)
        self.assertEqual(result["scoredCompanyDays"], 1)
        missing, _ = missing_day_windows([date(2026, 9, n) for n in range(8, 12)], observations, 5)
        self.assertEqual(missing, [date(2026, 9, 9), date(2026, 9, 10)])

    def test_rotation_does_not_keep_retrying_first_failed_company(self):
        coverage = audit(self.companies, {}, date(2026, 9, 11), 10)
        first = plan_windows(coverage, {}, limit=1)
        self.assertEqual(first[0]["slug"], "a")
        self.assertEqual(first[0]["days"], 5)
        second = plan_windows(coverage, {"lastAttemptAt": {"a": "2026-09-12T00:00:00Z"}}, limit=1)
        self.assertEqual(second[0]["slug"], "b")

    def test_window_stops_before_already_collected_date(self):
        coverage = audit(self.companies[:1], {"a": [{"date": "2026-09-09", "coverageStatus": "ok"}]}, date(2026, 9, 11), 5)
        window = plan_windows(coverage, {})[0]
        self.assertEqual((window["start"], window["end"], window["days"]), ("2026-09-07", "2026-09-08", 2))

    def test_completed_company_needs_no_recovery(self):
        coverage = audit(self.companies[:1], {"a": [{"date": "2026-09-11", "coverageStatus": "no_coverage"}]}, date(2026, 9, 11), 1)
        self.assertEqual(plan_windows(coverage, {}), [])
