from __future__ import annotations

import unittest

from automation.build_company_reports import (
    build_annual_section,
    build_company_report,
    build_press_section,
)


class BuildCompanyReportsTests(unittest.TestCase):
    def annual(self, year: int, words: int, **counts):
        return {
            "company_slug": "example",
            "report_year": year,
            "report_title": f"Annual report {year}",
            "report_url": f"https://example.test/{year}.pdf",
            "report_data_status": "extracted",
            "extracted_word_count": words,
            **counts,
        }

    def test_keyword_trends_are_normalised_for_report_length(self) -> None:
        section = build_annual_section(
            [
                self.annual(2024, 100_000, ai_mentions=10),
                self.annual(2025, 200_000, ai_mentions=20),
            ]
        )
        ai = next(item for item in section["emergingKeywords"] if item["key"] == "ai")
        self.assertEqual(ai["changePer10kWords"], 0)

    def test_thematic_positivity_is_labelled_as_a_proxy(self) -> None:
        section = build_annual_section(
            [
                self.annual(2024, 100_000, ai_mentions=1, regulation_mentions=10),
                self.annual(2025, 100_000, ai_mentions=20, regulation_mentions=5),
            ]
        )
        self.assertGreater(section["latestPositivity"], 50)
        self.assertEqual(
            section["latestPositivityMethod"],
            "year-on-year thematic direction proxy",
        )

    def test_no_coverage_is_not_rendered_as_neutral_sentiment(self) -> None:
        section = build_press_section(
            {
                "observations": [
                    {
                        "date": "2026-08-30",
                        "dailyScore": None,
                        "rollingScore": 52,
                        "coverageStatus": "no_coverage",
                    }
                ]
            }
        )
        self.assertIsNone(section["latestScore"])
        self.assertEqual(section["scoredDayCount"], 0)

    def test_press_events_exclude_description_only_company_mentions(self) -> None:
        company = {
            "companyName": "Barclays",
            "ticker": "BARC.L",
            "aliases": ["Barclays", "Barclays PLC", "Barclays Bank"],
            "requireHeadlineAlias": True,
            "contextTerms": [],
        }
        payload = {
            "observations": [{
                "date": "2026-08-30",
                "dailyScore": 50,
                "topStories": [
                    {"title": "Unrelated shares fall after earnings", "polarity": -0.7},
                    {"title": "Barclays PLC announces a strategic update", "polarity": 0.4},
                ],
            }]
        }
        section = build_press_section(payload, company)
        self.assertEqual([item["title"] for item in section["stories"]], [
            "Barclays PLC announces a strategic update"
        ])

    def test_company_report_combines_all_four_source_sections(self) -> None:
        company = {
            "companyName": "Example PLC",
            "ticker": "EXM.L",
            "slug": "example",
            "sector": "Industrials",
            "ftseRank": 50,
        }
        sentiment = {
            "observations": [
                {
                    "date": "2026-08-30",
                    "dailyScore": 61.2,
                    "rollingScore": 57.1,
                    "storyCount": 1,
                    "sourceCount": 1,
                    "coverageStatus": "ok",
                    "topStories": [
                        {
                            "title": "Example wins a major contract",
                            "publishedAt": "2026-08-30T09:00:00Z",
                            "url": "https://publisher.test/story",
                            "source": "publisher.test",
                            "polarity": 0.6,
                        }
                    ],
                    "flags": [],
                }
            ]
        }
        report = build_company_report(
            company,
            sentiment,
            [self.annual(2025, 100_000, ai_mentions=5)],
            "2026-09-01T00:00:00Z",
        )
        self.assertIn("introduction", report["company"])
        self.assertEqual(report["pressCoverage"]["latestScore"], 61.2)
        self.assertEqual(report["annualReportAnalysis"]["reportCount"], 1)
        self.assertTrue(any(item["type"] == "press_story" for item in report["events"]))


if __name__ == "__main__":
    unittest.main()
