from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.agent.tools.navigation import map_guidance
from concierge_kiosk.agent.tools.scheduling import approved_schedule
from concierge_kiosk.core.dataset_layout import MAP, MAP_PATHS, dataset_path
from concierge_kiosk.persistence.sqlite_store import Store
from shipped_db import shipped_store


class FuramaReleaseTests(unittest.TestCase):
    def test_map_release_excludes_legacy_unverified_place_and_has_evidence_paths(self):
        raw = json.loads(dataset_path(MAP).read_text(encoding="utf-8"))
        release = json.loads((ROOT / "releases/map-release.json").read_text(encoding="utf-8"))
        canonical_count = sum(len(zone.get("locations", [])) for zone in raw["zones"])
        self.assertGreater(canonical_count, 0)
        self.assertGreaterEqual(len(release["places"]), canonical_count)
        ids = {place["id"] for place in release["places"]}
        self.assertTrue(ids)
        self.assertEqual(len(release["paths"]), len(json.loads(dataset_path(MAP_PATHS).read_text(encoding="utf-8"))["paths"]))
        for edge in release["paths"]:
            self.assertRegex(edge["evidence"]["evidence_sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(edge["precision"], {"floorplan", "zone"})
            self.assertIn(edge["evidence"]["source_kind"], {"official_floorplan", "secondary_facility_map"})

    def test_icp_verified_route_works_when_start_is_known(self):
        path = ROOT / "releases/map-release.json"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        store = shipped_store()
        result = map_guidance(
            store,
            path=str(path),
            expected_sha256=digest,
            property_id="FURAMA_DANANG",
            language="en",
            query="How do I get to Danang Ballroom 1?",
            start_id="meeting_international_convention_palace",
            as_of="2026-10-01",
        )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["destination_id"], "meeting_ballroom_1")
        self.assertGreaterEqual(len(result["steps"]), 2)

    def test_planning_release_uses_runtime_window_contract(self):
        release = json.loads((ROOT / "releases/planning-release.json").read_text(encoding="utf-8"))
        for activity in release["activities"]:
            for window in activity["windows"]:
                self.assertEqual(set(window), {"start", "end"})
                self.assertNotIn("start_time", window)
                self.assertNotIn("end_time", window)

    def test_planning_release_is_accepted_for_all_languages(self):
        path = ROOT / "releases/planning-release.json"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        store = shipped_store()
        for language in ("vi", "en", "zh", "ko"):
            schedule = approved_schedule(
                store,
                path=str(path),
                expected_sha256=digest,
                property_id="FURAMA_DANANG",
                language=language,
                as_of="2026-10-01",
            )
            self.assertEqual(set(schedule), {"don_cipriani_dinner", "v_senses_spa"})
            self.assertEqual({item["topic"] for item in schedule.values()}, {"dining", "facilities"})
            self.assertTrue(all(item["hours_source_verified"] for item in schedule.values()))


if __name__ == "__main__":
    unittest.main()
