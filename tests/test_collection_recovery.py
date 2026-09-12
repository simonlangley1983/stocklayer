import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from automation import news_sentiment as news
from scripts.monitor_annual_reports import candidate_source_urls


class CollectionRecoveryTests(unittest.TestCase):
    def test_expired_budget_does_not_contact_provider(self):
        provider = news.GdeltProvider(deadline=0)
        company = {"slug": "example", "aliases": ["Example Group"]}
        start, end = news.day_bounds(news.date(2026, 9, 10))
        with patch.object(provider.session, "get") as request:
            with self.assertRaises(news.CollectionPaused):
                provider.fetch(company, start, end)
            request.assert_not_called()

    def test_pause_saves_successes_without_marking_unfetched_days_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            companies = [dict(slug=s, companyName=s, ticker=s, aliases=[s]) for s in ("first", "second", "third")]
            universe = root / "universe.json"
            universe.write_text(json.dumps({"companies": companies}))
            args = Namespace(universe=universe, slugs=None, date="2026-09-10",
                             backfill_days=1, request_delay=0, max_runtime_seconds=10,
                             rebuild_only=False, test_scorer=True, dry_run=False,
                             minimum_completeness=.9)
            with patch.object(news, "HISTORY_DIR", root / "history"), \
                 patch.object(news, "LATEST_PATH", root / "latest.json"), \
                 patch.object(news, "RUN_STATUS_PATH", root / "status.json"), \
                 patch.object(news.GdeltProvider, "fetch", side_effect=[[], news.CollectionPaused("budget")]) as fetch:
                self.assertEqual(news.run(args), 2)
            self.assertEqual(fetch.call_count, 2)
            status = json.loads((root / "status.json").read_text())
            self.assertEqual(status["completedObservationCount"], 1)
            self.assertEqual(status["pausedReason"], "budget")
            self.assertTrue((root / "history/first.json").exists())
            self.assertFalse((root / "history/second.json").exists())
            latest = json.loads((root / "latest.json").read_text())
            self.assertEqual(latest["companies"]["second"]["coverageStatus"], "provider_error")
            self.assertIsNone(latest["companies"]["second"]["dailyScore"])

    def test_current_news_universe_matches_company_membership(self):
        current = json.loads((news.ROOT / "uk-companies.json").read_text())
        universe = json.loads(news.UNIVERSE_PATH.read_text())
        self.assertEqual({c["slug"] for c in current}, {c["slug"] for c in universe["companies"]})

    def test_verified_report_hub_precedes_guessed_paths(self):
        urls = candidate_source_urls({"ticker": "CCC.L", "domain": "computacenter.com"})
        self.assertTrue(urls[0].startswith("https://investors.computacenter.com/"))
