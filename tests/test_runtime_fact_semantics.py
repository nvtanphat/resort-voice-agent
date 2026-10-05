from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from concierge_kiosk.rag.relevance import candidate_relevant, evidence_relevant

ROOT = Path(__file__).resolve().parents[1]


class RuntimeFactSemanticsTests(unittest.TestCase):
    def test_expiring_prices_compile_with_exact_basis_and_validity(self):
        path = ROOT / "knowledge/compiled/furama/en/kb_restaurant_cafe_indochine.md"
        raw = path.read_text(encoding="utf-8")
        self.assertIn('"effective_to":"2026-12-31"', raw)
        self.assertIn("498,000 VND++ / guest", raw)
        self.assertIn("888,000 VND++ / guest", raw)
        self.assertIn("390,000 VND net / guest", raw)

    def test_expired_offer_rows_are_filtered_without_expiring_permanent_entity_facts(self):
        con = sqlite3.connect(ROOT / "data/concierge.sqlite3")
        try:
            promo = con.execute(
                "SELECT COUNT(*) FROM knowledge WHERE source=? AND effective_to=?",
                ("kb_restaurant_cafe_indochine", "2026-12-31"),
            ).fetchone()[0]
            after_expiry = con.execute(
                "SELECT COUNT(*) FROM knowledge WHERE source=? AND effective_from<=? "
                "AND effective_to IS NOT NULL AND (effective_to IS NULL OR effective_to>=?)",
                ("kb_restaurant_cafe_indochine", "2027-01-01", "2027-01-01"),
            ).fetchone()[0]
            permanent = con.execute(
                "SELECT COUNT(*) FROM knowledge WHERE source=? AND effective_to IS NULL",
                ("kb_restaurant_cafe_indochine",),
            ).fetchone()[0]
        finally:
            con.close()
        self.assertGreater(promo, 0)
        self.assertEqual(after_expiry, 0)
        self.assertGreater(permanent, 0)

    def test_single_term_alias_cannot_authorize_unrelated_child_candidate(self):
        body = "Traditional Italian cuisine is served for lunch and dinner."
        search_text = body + " pasta pizza italian restaurant"
        self.assertFalse(evidence_relevant("pasta", "en", body, "Don Cipriani's", "Verified facts"))
        self.assertFalse(candidate_relevant("pasta", "en", search_text, body, "Don Cipriani's", "Verified facts"))

    def test_semantic_facts_are_backed_by_current_official_provenance(self):
        facts = [json.loads(line) for line in (ROOT / "datasets/knowledge/canonical/facts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        keys = {(f["entity_id"], f["fact_type"], f.get("context")): f for f in facts}
        expected = [
            ("restaurant.don_cipriani", "cuisine_type", "primary_cuisine"),
            ("service.in_room_dining", "service_feature", "room_delivery"),
            ("recreation.kids_club", "service_feature", "children_activities"),
            ("recreation.fitness_center", "service_feature", "exercise_equipment"),
        ]
        for key in expected:
            self.assertIn(key, keys)
            fact = keys[key]
            self.assertEqual(fact.get("verification_status"), "official_verified")
            self.assertEqual(fact.get("source_tier"), "official_primary")
            self.assertTrue(all(src.get("source_url", "").startswith("https://furamavietnam.com/") for src in fact["provenance_sources"]))

    def test_entity_knowledge_status_is_explicit(self):
        entities = [json.loads(line) for line in (ROOT / "datasets/knowledge/canonical/entities.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        facts = [json.loads(line) for line in (ROOT / "datasets/knowledge/canonical/facts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        active = {f["entity_id"] for f in facts if f.get("publication_status", "approved") == "approved"}
        for entity in entities:
            expected = "runtime_publishable" if entity["entity_id"] in active else "catalog_only"
            self.assertEqual(entity.get("knowledge_status"), expected)


if __name__ == "__main__":
    unittest.main()
