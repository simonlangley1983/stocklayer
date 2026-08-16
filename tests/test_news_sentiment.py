from __future__ import annotations

import json
import unittest
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from automation.news_sentiment import (
    KeywordTestScorer,
    canonical_url,
    cluster_articles,
    event_flags,
    fetch_window_adaptive,
    load_event_rules,
    narrative_flags,
    partition_candidates_by_day,
    processing_days,
    read_json,
    score_company_day,
)


ROOT = Path(__file__).resolve().parents[1]


class NewsSentimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.methodology = read_json(ROOT / "sentiment" / "methodology.json", None)
        self.company = {
            "companyName": "Example Group PLC",
            "ticker": "EXM.L",
            "slug": "example",
            "sector": "Industrials",
            "domain": "example.com",
            "aliases": ["Example Group PLC", "Example Group"],
            "requireHeadlineAlias": False,
        }

    @staticmethod
    def candidate(title: str, domain: str = "publisher.test", url_suffix: str = "1") -> dict:
        return {
            "title": title,
            "url": f"https://{domain}/story/{url_suffix}?utm_source=test",
            "domain": domain,
            "seendate": "20260815T120000Z",
            "language": "English",
        }

    def test_canonical_url_removes_tracking(self) -> None:
        self.assertEqual(
            canonical_url("HTTPS://Example.COM/path/?utm_source=x&a=1#fragment"),
            "https://example.com/path?a=1",
        )

    def test_near_identical_headlines_form_one_story(self) -> None:
        articles = [
            {"title": "Example Group reports record annual profit growth", "publishedAt": "1"},
            {"title": "Example Group reports record annual profit growth", "publishedAt": "2"},
            {"title": "Example Group appoints a new chief executive", "publishedAt": "3"},
        ]
        self.assertEqual(len(cluster_articles(articles, 0.82)), 2)

    def test_positive_story_is_shrunk_toward_neutral(self) -> None:
        observation = score_company_day(
            self.company,
            date(2026, 8, 15),
            [self.candidate("Example Group beats expectations with record growth")],
            KeywordTestScorer(),
            self.methodology,
            load_event_rules(),
            [],
        )
        self.assertGreater(observation["dailyScore"], 50)
        self.assertLess(observation["dailyScore"], 70)
        self.assertEqual(observation["storyCount"], 1)

    def test_no_coverage_is_null_not_neutral(self) -> None:
        observation = score_company_day(
            self.company,
            date(2026, 8, 15),
            [],
            KeywordTestScorer(),
            self.methodology,
            load_event_rules(),
            [],
        )
        self.assertIsNone(observation["dailyScore"])
        self.assertEqual(observation["coverageStatus"], "no_coverage")
        self.assertIn("no_coverage", {item["type"] for item in observation["flags"]})

    def test_first_party_story_flags_event_but_does_not_score(self) -> None:
        observation = score_company_day(
            self.company,
            date(2026, 8, 15),
            [
                self.candidate(
                    "Example Group announces annual results and raises guidance",
                    domain="example.com",
                )
            ],
            KeywordTestScorer(),
            self.methodology,
            load_event_rules(),
            [],
        )
        flag_types = {item["type"] for item in observation["flags"]}
        self.assertIsNone(observation["dailyScore"])
        self.assertIn("annual_results", flag_types)
        self.assertIn("guidance_raised", flag_types)

    def test_ambiguous_name_requires_specific_alias_or_context(self) -> None:
        company = {
            **self.company,
            "companyName": "Next PLC",
            "slug": "next",
            "aliases": ["Next PLC", "Next"],
            "requireHeadlineAlias": True,
            "contextTerms": ["retail", "retailer", "fashion"],
        }
        observation = score_company_day(
            company,
            date(2026, 8, 15),
            [self.candidate("What happens next after the latest policy decision")],
            KeywordTestScorer(),
            self.methodology,
            load_event_rules(),
            [],
        )
        self.assertIsNone(observation["dailyScore"])
        self.assertEqual(
            observation["rejectedCandidateCounts"].get("ambiguous_company_without_alias"),
            1,
        )

    def test_narrative_threshold_flags(self) -> None:
        observation = {
            "dailyScore": 70.0,
            "confidence": 80.0,
            "storyCount": 5,
            "sourceCount": 3,
            "positiveStoryCount": 4,
            "negativeStoryCount": 0,
            "dispersion": 0.2,
            "coverageStatus": "ok",
            "topStories": [],
        }
        history = [{"date": "2026-08-14", "dailyScore": 50.0, "storyCount": 1, "coverageStatus": "ok"}]
        flags = narrative_flags(observation, history, self.methodology["flagThresholds"])
        types = {item["type"] for item in flags}
        self.assertIn("sentiment_jump", types)
        self.assertIn("good_press", types)

    def test_truncated_collection_reduces_confidence_and_adds_flag(self) -> None:
        complete = score_company_day(
            self.company,
            date(2026, 8, 15),
            [self.candidate("Example Group beats expectations with record growth")],
            KeywordTestScorer(),
            self.methodology,
            load_event_rules(),
            [],
        )
        truncated = score_company_day(
            self.company,
            date(2026, 8, 15),
            [self.candidate("Example Group beats expectations with record growth")],
            KeywordTestScorer(),
            self.methodology,
            load_event_rules(),
            [],
            collection_completeness=0.75,
        )
        self.assertLess(truncated["confidence"], complete["confidence"])
        self.assertIn("collection_degraded", {item["type"] for item in truncated["flags"]})

    def test_candidates_are_partitioned_by_london_day(self) -> None:
        candidates = [
            {"seendate": "20260814T233000Z", "title": "Late", "url": "https://a.test/1"},
            {"seendate": "20260815T233000Z", "title": "Later", "url": "https://a.test/2"},
        ]
        partitioned = partition_candidates_by_day(
            candidates, date(2026, 8, 15), date(2026, 8, 17)
        )
        self.assertEqual(len(partitioned[date(2026, 8, 15)]), 1)
        self.assertEqual(len(partitioned[date(2026, 8, 16)]), 1)

    def test_adaptive_fetch_splits_capped_windows(self) -> None:
        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0

            def fetch(self, company, start_utc, end_utc):
                self.calls += 1
                start_day = start_utc.astimezone(ZoneInfo("Europe/London")).date()
                end_day = end_utc.astimezone(ZoneInfo("Europe/London")).date()
                span = (end_day - start_day).days
                if span > 1:
                    return [
                        {"seendate": f"{start_day:%Y%m%d}T120000Z"},
                        {"seendate": f"{start_day:%Y%m%d}T130000Z"},
                    ]
                return [{"seendate": f"{start_day:%Y%m%d}T120000Z"}]

        provider = FakeProvider()
        partitioned, truncated, request_count = fetch_window_adaptive(
            provider,
            self.company,
            date(2026, 8, 12),
            date(2026, 8, 16),
            max_records=2,
        )
        self.assertEqual(request_count, 7)
        self.assertEqual(provider.calls, 7)
        self.assertFalse(truncated)
        self.assertTrue(all(len(partitioned[item]) == 1 for item in partitioned))

    def test_backfill_day_range_is_chronological(self) -> None:
        days = processing_days(Namespace(date=None, backfill_days=30))
        self.assertEqual(len(days), 30)
        self.assertEqual(days[-1] - days[0], timedelta(days=29))

    def test_event_flag_is_possible_with_one_source(self) -> None:
        article = {
            "id": "a1",
            "title": "Example Group issues profit warning",
            "description": "",
            "domain": "publisher.test",
            "firstParty": False,
        }
        flags = event_flags([article], load_event_rules())
        profit_warning = next(item for item in flags if item["type"] == "profit_warning")
        self.assertEqual(profit_warning["status"], "possible")

    def test_universe_contains_all_100_unique_companies(self) -> None:
        universe = json.loads((ROOT / "sentiment" / "company-universe.json").read_text(encoding="utf-8"))
        slugs = [item["slug"] for item in universe["companies"]]
        self.assertEqual(universe["companyCount"], 100)
        self.assertEqual(len(slugs), 100)
        self.assertEqual(len(set(slugs)), 100)


if __name__ == "__main__":
    unittest.main()
