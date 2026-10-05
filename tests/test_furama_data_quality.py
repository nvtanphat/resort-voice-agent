from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tools.validate_furama_semantics import validate_dataset
from concierge_kiosk.core.dataset_layout import FACTS, dataset_path


ROOT = Path(__file__).resolve().parents[1]


class FuramaDataQualityTests(unittest.TestCase):
    def test_semantic_quality_gate(self):
        result = validate_dataset(dataset_path(""))
        self.assertEqual(result["semantic_errors"], 0)
        self.assertGreaterEqual(result["facts_checked"], 300)

    def test_no_corrupted_clock_tokens(self):
        text = dataset_path(FACTS).read_text(encoding="utf-8")
        self.assertNotIn("30:00", text)
        self.assertNotIn("34:30", text)

    def test_all_normalized_opening_hours_are_valid_24h_clocks(self):
        clock = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
        for line in dataset_path(FACTS).read_text(encoding="utf-8").splitlines():
            fact = json.loads(line)
            if fact.get("fact_type") != "opening_hours":
                continue
            value = fact["normalized_value"]
            self.assertRegex(value["start"], clock, fact["canonical_fact_id"])
            self.assertRegex(value["end"], clock, fact["canonical_fact_id"])

    def test_regression_known_hours(self):
        rows = [json.loads(x) for x in dataset_path(FACTS).read_text(encoding="utf-8").splitlines() if x.strip()]
        facts = {(f["entity_id"], f.get("context")): f for f in rows}
        expected = {
            ("restaurant.cafe_indochine", "breakfast_hours"): ("06:30", "10:30"),
            ("service.in_room_dining", "operating_hours"): ("06:30", "00:00"),
            ("service.in_room_dining", "late_night_dishes"): ("22:30", "06:00"),
            ("spa.v_senses_wellness", "daily_hours"): ("09:00", "22:00"),
            ("restaurant.don_cipriani", "daily_dinner"): ("18:00", "22:00"),
        }
        for key, (start, end) in expected.items():
            self.assertIn(key, facts)
            self.assertEqual(facts[key]["normalized_value"], {"start": start, "end": end})

    def test_butler_localization_does_not_claim_24_7(self):
        for line in dataset_path(FACTS).read_text(encoding="utf-8").splitlines():
            fact = json.loads(line)
            if fact.get("entity_id") == "contact.butler_service" and fact.get("context") == "butler_availability":
                self.assertNotIn("24/7", json.dumps(fact["locale_support"], ensure_ascii=False))
                self.assertEqual(fact["normalized_value"], {"start":"06:00","end":"00:00"})
                return
        self.fail("Butler availability fact missing")


if __name__ == "__main__":
    unittest.main()
