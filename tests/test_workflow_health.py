import unittest
from scripts.check_collection_run import outcome
from scripts.check_annual_report_coverage import enforce

class HealthTests(unittest.TestCase):
    def test_stalls_warn_but_lost_reports_fail(self):
        self.assertEqual(enforce({'alerts': ['a: fewer than two report years, unchanged for 4 runs; review source availability']}), 0)
        self.assertEqual(enforce({'alerts': ['a: previously extracted report years lost']}), 1)

    def test_saved_partial_batch_warns(self):
        status = {'pausedReason': 'Collection time budget reached; remaining dates will resume later', 'completedObservationCount': 16, 'requestedObservationCount': 100}
        self.assertEqual(outcome(2, status)[:2], (0, 'warning'))
        self.assertEqual(outcome(1, status)[0], 1)
        self.assertEqual(outcome(2, {**status, 'completedObservationCount': 0})[0], 2)
        self.assertEqual(outcome(2, {**status, 'pausedReason': 'Provider failed'})[0], 2)

if __name__ == '__main__':
    unittest.main()
