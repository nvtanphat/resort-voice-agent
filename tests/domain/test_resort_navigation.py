from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.agent.tools.navigation import map_guidance, read_approved_map
from concierge_kiosk.persistence.sqlite_store import Store


class ResortNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "releases/map-release.json"
        cls.digest = hashlib.sha256(cls.path.read_bytes()).hexdigest()
        cls.store = Store(str(ROOT / "data/concierge.sqlite3"))

    def route(self, query: str, *, start_id: str | None = None, language: str = "en"):
        return map_guidance(
            self.store,
            path=str(self.path), expected_sha256=self.digest,
            property_id="FURAMA_DANANG", language=language,
            query=query, start_id=start_id, as_of="2026-10-01",
        )

    def test_release_has_resort_zone_hubs_and_mixed_precision_evidence(self):
        release = read_approved_map(str(self.path), self.digest, "FURAMA_DANANG", as_of="2026-10-01")
        hubs = [p for p in release["places"] if p["place_type"] == "zone_hub"]
        self.assertEqual(len(hubs), 7)
        self.assertGreaterEqual(len(release["paths"]), 30)
        self.assertIn("zone", {e["precision"] for e in release["paths"]})
        self.assertIn("floorplan", {e["precision"] for e in release["paths"]})
        self.assertIn("secondary_facility_map", {e["evidence"]["source_kind"] for e in release["paths"]})
        self.assertIn("official_floorplan", {e["evidence"]["source_kind"] for e in release["paths"]})

    def test_lobby_to_tennis_is_verified_zone_route(self):
        result = self.route("How do I get to the tennis courts?")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["destination_id"], "recreation_tennis_club")
        self.assertEqual(result["precision"], "zone")
        self.assertEqual(result["destination_precision"], "exact_published_node")
        self.assertTrue(result["evidence"])
        self.assertTrue(any(e["source_kind"] == "secondary_facility_map" for e in result["evidence"]))

    def test_specific_dining_venue_uses_explicit_zone_only_fallback(self):
        result = self.route("Where is Café Indochine?")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["destination_id"], "restaurant_cafe_indochine")
        self.assertEqual(result["resolved_destination_id"], "zone_dining_hub")
        self.assertEqual(result["precision"], "zone")
        self.assertEqual(result["destination_precision"], "zone_only")
        self.assertIn("exact", " ".join(result["limitations"]).lower())

    def test_breakfast_buffet_description_resolves_to_cafe_indochine(self):
        result = self.route("nh\u00e0 h\u00e0ng buffet s\u00e1ng \u1edf \u0111\u00e2u", language="vi")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["destination_id"], "restaurant_cafe_indochine")

    def test_lobby_to_son_tra_combines_macro_and_official_floorplan(self):
        result = self.route("How do I get to Son Tra Room?")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["destination_id"], "meeting_son_tra_1")
        # Whole route is conservatively zone precision because the first leg from
        # Reception to ICP comes from the coarse resort map; the ICP leg is exact.
        self.assertEqual(result["precision"], "zone")
        kinds = {e["source_kind"] for e in result["evidence"]}
        self.assertEqual(kinds, {"secondary_facility_map", "official_floorplan"})
        self.assertGreaterEqual(len(result["steps"]), 2)

    def test_icp_to_son_tra_is_floorplan_precision(self):
        result = self.route("Son Tra Room", start_id="meeting_international_convention_palace")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["precision"], "floorplan")
        self.assertEqual({e["source_kind"] for e in result["evidence"]}, {"official_floorplan"})


if __name__ == "__main__":
    unittest.main()
