import unittest

from scripts.refresh_signalstrata import (
    extract_initiatives,
    latest_evidence_date,
    merge_exact_duplicates,
    normalise_initiative,
    payloads_for_batch,
)


class SignalStrataNormalisationTests(unittest.TestCase):
    def test_normalises_and_merges_exact_duplicate_ids(self):
        payload = {
            "initiatives": [
                {
                    "initiative_id": "one",
                    "initiative_name": "Example programme",
                    "category": "Digital & Tech",
                    "evidence": [{"id": "e1", "title": "First source", "publication_date": "2026-01-02"}],
                },
                {
                    "initiative_id": "one",
                    "initiative_name": "Example programme",
                    "evidence": [{"id": "e2", "title": "Second source", "publication_date": "2026-03-02"}],
                },
            ]
        }
        initiatives = merge_exact_duplicates(
            normalise_initiative(item, index)
            for index, item in enumerate(extract_initiatives(payload, "TEST"))
        )
        self.assertEqual(len(initiatives), 1)
        self.assertEqual(len(initiatives[0]["evidence"]), 2)
        self.assertEqual(latest_evidence_date(initiatives), "2026-03-02")

    def test_unknown_response_shape_is_empty_not_fabricated(self):
        self.assertEqual(extract_initiatives({"unexpected": True}, "TEST"), [])

    def test_batch_response_is_mapped_by_ticker(self):
        response = {"data": [{"ticker": "AZN", "initiatives": []}, {"ticker": "BARC", "initiatives": []}]}
        mapped = payloads_for_batch(response, ["AZN", "BARC"])
        self.assertEqual(mapped["AZN"]["ticker"], "AZN")
        self.assertEqual(mapped["BARC"]["ticker"], "BARC")


if __name__ == "__main__":
    unittest.main()
