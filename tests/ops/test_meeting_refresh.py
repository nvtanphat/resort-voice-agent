from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class MeetingRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = [json.loads(line) for line in (ROOT / "datasets/knowledge/canonical/facts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    def _value(self, entity_id: str, fact_type: str, context: str):
        for fact in self.facts:
            if fact["entity_id"] == entity_id and fact["fact_type"] == fact_type and fact["context"] == context:
                return fact["normalized_value"], fact
        self.fail(f"missing fact {entity_id}/{fact_type}/{context}")

    def test_current_icp_dimensions(self):
        checks = {
            ("meeting.danang_grand_ballroom", "area_sqm", "floor_area"): 774.0,
            ("meeting.danang_grand_ballroom", "height_m", "ceiling_height"): 5.5,
            ("meeting.han_river_1", "area_sqm", "floor_area"): 39.0,
            ("meeting.han_river_2", "area_sqm", "floor_area"): 41.0,
            ("meeting.son_tra_1", "area_sqm", "floor_area"): 62.0,
            ("meeting.ocean_ballroom", "height_m", "ceiling_height"): 3.0,
        }
        for key, expected in checks.items():
            value, fact = self._value(*key)
            self.assertEqual(value, expected, key)
            self.assertEqual(fact.get("publication_status"), "approved", key)
            self.assertIn("Furama-ICP-Capacity-Charts-Floor-PLan.pdf", fact["provenance_sources"][0]["source_url"])


    def test_refreshed_numeric_locale_metadata_matches_canonical_value(self):
        refreshed = {
            "meeting.danang_grand_ballroom", "meeting.ballroom_1", "meeting.ballroom_2",
            "meeting.ballroom_3", "meeting.han_river_1", "meeting.han_river_2",
            "meeting.son_tra_1", "meeting.ocean_ballroom",
        }
        for fact in self.facts:
            if fact["entity_id"] not in refreshed or fact.get("publication_status") != "approved":
                continue
            if fact["fact_type"] not in {"area_sqm", "height_m", "capacity"}:
                continue
            needle = f"{fact['normalized_value']:g}" if isinstance(fact["normalized_value"], float) else str(fact["normalized_value"])
            for language, text in (fact.get("locale_support") or {}).items():
                self.assertIn(needle, str(text), (fact["canonical_fact_id"], language, text))

    def test_legacy_son_tra_2_is_absent_from_runtime_compiled_corpus(self):
        compiled = ROOT / "knowledge/compiled/furama"
        self.assertFalse(any(path.name == "son-tra-2.md" for path in compiled.rglob("*.md")))

    def test_legacy_son_tra_2_is_not_publishable(self):
        rows = [fact for fact in self.facts if fact["entity_id"] == "meeting.son_tra_2"]
        self.assertFalse(rows)
        quarantine = [json.loads(line) for line in (ROOT / "datasets/quarantine/facts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        archived = [fact for fact in quarantine if fact["entity_id"] == "meeting.son_tra_2"]
        self.assertTrue(archived)
        self.assertTrue(all(fact.get("publication_status") == "legacy_unverified" for fact in archived))


if __name__ == "__main__":
    unittest.main()
