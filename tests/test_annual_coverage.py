import unittest
from scripts.check_annual_report_coverage import assess

class CoverageTests(unittest.TestCase):
    def test_stall_alert_after_three_unchanged_runs(self):
        companies = [{"slug": "a"}]
        status = assess(companies, {}, {})
        for _ in range(2):
            status = assess(companies, {}, status)
            self.assertFalse(status["alerts"])
        self.assertTrue(assess(companies, {}, status)["alerts"])

    def test_improvement_resets_stall(self):
        old = {"companies": [{"slug": "a", "years": [], "unchangedRuns": 4}]}
        status = assess([{"slug": "a"}], {"a": {2025}}, old)
        self.assertEqual(status["companies"][0]["unchangedRuns"], 0)
        self.assertFalse(status["alerts"])

    def test_regression_and_current_membership(self):
        old = {"companies": [{"slug": "a", "years": [2024, 2025]}, {"slug": "retired", "years": [2025]}]}
        status = assess([{"slug": "a"}], {"a": {2025}}, old)
        self.assertEqual(status["companyCount"], 1)
        self.assertEqual(len(status["alerts"]), 1)
