import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import subprocess

import backfill_annual_reports as backfill


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.companies = [{"slug": "a", "ticker": "A.L"}, {"slug": "z", "ticker": "Z.L"}]
        self.index = {"companies": {c["slug"]: {**c, "reports": [
            {"year": 2025, "title": "Annual report 2025", "url": f"https://example.com/{c['slug']}/2025.pdf"}
        ]} for c in self.companies}}

    def queue(self, existing=None, state=None):
        return backfill.queue_groups(self.index, self.companies, existing or {}, state or {}, 2020, 2026, self.now)

    def test_successful_reports_and_removed_companies_are_skipped(self):
        self.index['companies']['removed'] = self.index['companies']['a']
        self.assertEqual([g[3] for g in self.queue({'a:2025': {}})], ['z:2025'])

    def test_failed_first_company_does_not_starve_later_companies(self):
        first = self.queue()[0]
        state = {'a:2025': {'candidateHash': first[4], 'lastAttempt': self.now.isoformat()}}
        self.assertEqual([g[3] for g in self.queue(state=state)], ['z:2025', 'a:2025'])

    def test_cooldown_and_new_source_reset(self):
        first = self.queue()[0]
        state = {'a:2025': {'candidateHash': first[4], 'retryAfter': (self.now + timedelta(days=1)).isoformat()}}
        self.assertEqual([g[3] for g in self.queue(state=state)], ['z:2025'])
        self.index['companies']['a']['reports'][0]['url'] += '?new=1'
        self.assertEqual(len(self.queue(state=state)), 2)

    def test_wrong_year_is_not_counted_as_recovered(self):
        result = subprocess.CompletedProcess([], 0, '{"report_data_status":"extracted","report_year":2024}', '')
        with patch.object(backfill.subprocess, 'run', return_value=result):
            self.assertEqual(backfill.run_group(self.queue()[0], 1)['report_data_status'], 'extraction_failed')

    def test_slow_report_cannot_block_batch_forever(self):
        with patch.object(backfill.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 1)):
            self.assertEqual(backfill.run_group(self.queue()[0], 1)['report_data_status'], 'extraction_failed')


if __name__ == '__main__':
    unittest.main()
