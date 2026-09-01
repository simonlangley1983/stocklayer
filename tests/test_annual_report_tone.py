from __future__ import annotations

import unittest

from scripts.extract_annual_report_keywords import lexical_tone


class AnnualReportToneTests(unittest.TestCase):
    def test_positive_financial_language_scores_above_neutral(self) -> None:
        tone = lexical_tone(
            "Strong growth and improved resilience created successful opportunities."
        )
        self.assertGreater(tone["tone_positivity_score"], 50)

    def test_negative_financial_language_scores_below_neutral(self) -> None:
        tone = lexical_tone(
            "Uncertainty, adverse pressure and losses created material headwinds and risk."
        )
        self.assertLess(tone["tone_positivity_score"], 50)

    def test_no_lexicon_matches_is_unavailable_not_neutral(self) -> None:
        tone = lexical_tone("The meeting took place on Tuesday.")
        self.assertIsNone(tone["tone_positivity_score"])


if __name__ == "__main__":
    unittest.main()
