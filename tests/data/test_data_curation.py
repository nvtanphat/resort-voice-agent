import json
import unittest
from pathlib import Path

from concierge_kiosk.core.dataset_layout import (
    ALIASES,
    CONTACTS,
    ENTITIES,
    FACTS,
    PLANNING,
    PROPERTY,
    QUARANTINE_FACTS,
    RELATIONS,
    SERVICE_CATALOG,
    dataset_path,
)

ROOT = Path(__file__).resolve().parents[2]


def load_jsonl(name):
    return [json.loads(x) for x in dataset_path(name).read_text(encoding="utf-8").splitlines() if x.strip()]


class DataCurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = load_jsonl(FACTS)
        cls.approved = [f for f in cls.facts if f.get("publication_status", "approved") == "approved"]
        cls.by_id = {f["canonical_fact_id"]: f for f in cls.facts}
        cls.quarantine = load_jsonl(QUARANTINE_FACTS)
        cls.quarantine_by_id = {f["canonical_fact_id"]: f for f in cls.quarantine}
        cls.entities = {e["entity_id"]: e for e in load_jsonl(ENTITIES)}

    def _find(self, entity, fact_type=None, context=None):
        return [f for f in self.approved if f["entity_id"] == entity
                and (fact_type is None or f["fact_type"] == fact_type)
                and (context is None or f.get("context") == context)]

    def test_current_dynamic_prices_are_expiry_bounded_and_official(self):
        prices = [f for f in self.approved if f["fact_type"] == "price_vnd"]
        self.assertEqual(len(prices), 7)
        for f in prices:
            self.assertEqual(f.get("verified_at"), "2026-10-01")
            self.assertEqual(f.get("price_basis"), "per_guest")
            self.assertIn(f.get("tax_basis"), {"++", "net", "inclusive"})
            self.assertTrue(any("furamavietnam.com" in s.get("source_url", "") for s in f.get("provenance_sources", [])))
            if f.get("price_temporality") == "current_listed_rate":
                self.assertIsNone(f.get("valid_until"))
            else:
                self.assertEqual(f.get("valid_until"), "2026-12-31")

    def test_price_semantics_are_not_flattened(self):
        prices = [f for f in self.approved if f["fact_type"] == "price_vnd"]
        for fact in prices:
            evidence = " ".join(s.get("evidence", "") for s in fact.get("provenance_sources", []))
            if fact.get("tax_basis") == "++":
                self.assertIn("++", evidence)
            elif fact.get("tax_basis") == "net":
                self.assertIn("net", evidence.lower())
            self.assertEqual(fact.get("verification_status"), "official_verified")
            self.assertEqual(fact.get("source_tier"), "official_primary")

    def test_promo_discount_never_becomes_capacity(self):
        for f in self.approved:
            if f["fact_type"] == "capacity":
                evidence = " ".join(s.get("evidence", "") for s in f.get("provenance_sources", []))
                self.assertNotIn("35% Off", evidence)

    def test_decimal_comma_fragments_are_quarantined(self):
        bad = {"cfact_023395c783d1644bb5e23f94", "cfact_9d8f3ccb7fd81117e4e7e96e", "cfact_d1d405dbbd62bb97ce1a54e4", "cfact_4e3733d2d8f75d421f9fe658"}
        for fid in bad:
            self.assertEqual(self.quarantine_by_id[fid]["publication_status"], "rejected_extraction")

    def test_current_address_and_checkout(self):
        profile = json.loads(dataset_path(PROPERTY).read_text(encoding="utf-8"))
        self.assertEqual(profile["address"]["ward"], "Ngu Hanh Son Ward")
        self.assertNotIn("district", profile["address"])
        self.assertEqual(profile["check_out_time"], "11:00")
        planning = json.loads(dataset_path(PLANNING).read_text(encoding="utf-8"))
        checkout = next(x for x in planning["operating_schedule"] if x["item"] == "check_out_time")
        self.assertEqual(checkout["hours"], "11:00")

    def test_current_room_area_balcony_beds_and_occupancy_contract(self):
        expected = {
            "room.lagoon_superior": (40.1, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children"),
            "room.garden_superior": (40.1, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children"),
            "room.garden_deluxe": (43.7, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children"),
            "room.ocean_deluxe": (43.7, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children"),
            "room.ocean_studio_suite": (41.1, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children"),
            "room.ocean_suite": (85.8, 24.0, "King", "3 Adults or 2 Adults & 2 Children"),
            "room.presidential_suite": (90.0, 23.0, "King", "3 Adults or 2 Adults & 2 Children"),
            "room.family_room_by_balcony": (85.8, 23.0, "1 King + 2 Single", "8 guests or 4 Adults & 4 Children"),
        }
        for eid, (area, balcony, bed, occupancy) in expected.items():
            self.assertEqual(self._find(eid, "area_sqm", "room_area")[0]["normalized_value"], area)
            self.assertEqual(self._find(eid, "balcony_area_sqm", "balcony_area")[0]["normalized_value"], balcony)
            self.assertEqual(self._find(eid, "bed_type", "bed_configuration")[0]["normalized_value"], bed)
            self.assertEqual(self._find(eid, "policy_rule", "occupancy_policy")[0]["normalized_value"], occupancy)

    def test_cafe_has_exact_service_windows_not_fake_continuous_day(self):
        windows = {(f["context"], f["normalized_value"]["start"], f["normalized_value"]["end"])
                   for f in self._find("restaurant.cafe_indochine", "opening_hours")}
        self.assertEqual(windows, {
            ("breakfast_hours", "06:30", "10:30"),
            ("a_la_carte_lunch", "11:30", "14:00"),
            ("a_la_carte_dinner", "18:00", "22:00"),
            ("seafood_steak_buffet_dinner", "18:30", "22:00"),
        })
        self.assertFalse(self._find("restaurant.cafe_indochine", "opening_hours", "daily_operating_hours"))

    def test_room_service_and_medical_are_current(self):
        self.assertEqual(self._find("service.in_room_dining", "opening_hours", "operating_hours")[0]["normalized_value"], {"start":"06:30","end":"00:00"})
        self.assertEqual(self._find("service.in_room_dining", "opening_hours", "late_night_dishes")[0]["normalized_value"], {"start":"22:30","end":"06:00"})
        self.assertEqual(self._find("service.medical_centre", "opening_hours", "staffed_hours")[0]["normalized_value"], {"start":"08:00","end":"17:00"})
        self.assertIn("Saturday afternoon and Sunday", self._find("service.medical_centre", "policy_rule", "hours_exception")[0]["normalized_value"])

    def test_pet_policy_and_room_cleaning_are_current_official_facts(self):
        pet = self._find("policy.pet_policy", "policy_rule", "pet_prohibition")
        self.assertEqual(len(pet), 1)
        self.assertEqual(pet[0]["normalized_value"], "prohibited")
        cleaning = self._find("service.room_cleaning", "opening_hours", "operating_hours")
        self.assertEqual(len(cleaning), 1)
        self.assertEqual(cleaning[0]["normalized_value"], {"start":"08:00","end":"17:00"})
        review_queue = json.loads(dataset_path("knowledge/canonical/domain_review_queue.json").read_text(encoding="utf-8"))
        self.assertEqual(review_queue["pending_count"], len(review_queue["items"]))

    def test_pool_lifeguard_schedule_is_not_mislabelled_as_opening_hours(self):
        self.assertFalse(self._find("recreation.swimming_pools", "opening_hours"))
        rows = self._find("recreation.swimming_pools", "activity_schedule", "lifeguard_hours")
        self.assertEqual(rows[0]["normalized_value"]["start"], "06:00")
        self.assertEqual(rows[0]["normalized_value"]["end"], "18:30")

    def test_babysitting_conflict_is_resolved_before_runtime_publication(self):
        rules = self._find("service.babysitting", "policy_rule", "advance_booking_notice")
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertEqual(rule.get("curation_resolution"), "dedicated_service_page_preferred")
        self.assertIn("one day", rule["normalized_value"].lower())
        self.assertNotIn("2-4 hours", rule["normalized_value"].lower())
        archived = [f for f in self.quarantine if f.get("entity_id") == "service.babysitting"
                    and f.get("conflict_status") == "official_source_conflict"]
        self.assertTrue(archived)
        planning = json.loads(dataset_path(PLANNING).read_text(encoding="utf-8"))
        lead = next(x for x in planning["lead_times_and_slas"] if x.get("service") == "babysitting_notice")
        self.assertEqual((lead["min_value"], lead["max_value"], lead["unit"]), (24, 24, "hours"))

    def test_operational_gaps_are_explicit_not_invented_facts(self):
        review_queue = json.loads(dataset_path("knowledge/canonical/domain_review_queue.json").read_text(encoding="utf-8"))
        self.assertGreater(review_queue["pending_count"], 0)
        self.assertTrue(all(item.get("status") == "pending" for item in review_queue["items"]))
        runtime_blob = "\n".join((ROOT / "knowledge/compiled/furama/vi").joinpath(name).read_text(encoding="utf-8")
                                  for name in [p.name for p in (ROOT / "knowledge/compiled/furama/vi").glob("*.md")])
        self.assertNotIn("Ghi chú bản phát hành", runtime_blob)

    def test_meeting_corruptions_are_repaired(self):
        self.assertEqual(self._find("meeting.international_convention_palace", "capacity", "max_occupancy")[0]["normalized_value"], 1000)
        self.assertEqual(self._find("meeting.boardroom", "area_sqm", "floor_area")[0]["normalized_value"], 31.0)
        self.assertEqual(self._find("meeting.boardroom", "capacity", "boardroom")[0]["normalized_value"], 20)
        self.assertEqual(self._find("meeting.gallery", "capacity", "cocktail")[0]["normalized_value"], 120)
        banquets = self._find("meeting.banquets", "capacity")
        # Release 1.0.3 adds one official, sourced maximum-guests fact.
        self.assertEqual([(row["context"], row["normalized_value"]) for row in banquets], [("maximum_guests", 2500)])

    def test_spa_wrong_source_prices_are_never_runtime_approved(self):
        self.assertFalse(self._find("spa.treatments", "price_vnd"))

    def test_relation_graph_has_no_edge_to_nonapproved_fact(self):
        status = {f["canonical_fact_id"]: f.get("publication_status", "approved") for f in self.facts}
        for r in load_jsonl(RELATIONS):
            for node in (r.get("source_id"), r.get("target_id")):
                if node in status:
                    self.assertEqual(status[node], "approved", r)

    def test_primary_fact_ledger_contains_only_web_reviewed_approved_facts(self):
        self.assertTrue(self.facts)
        for f in self.facts:
            self.assertEqual(f.get("publication_status"), "approved")
            self.assertEqual(f.get("curation_method"), "official_web_manual_review")
            self.assertGreaterEqual(str(f.get("verified_at") or ""), "2026-10-01")
            for src in f.get("provenance_sources", []):
                self.assertEqual(src.get("verified_at"), f.get("verified_at"))
            self.assertTrue(all("furamavietnam.com" in s.get("source_url", "") for s in f.get("provenance_sources", [])))

    def test_library_movie_time_is_activity_schedule_not_library_opening_hours(self):
        self.assertFalse(self._find("recreation.library", "opening_hours"))
        movie = self._find("recreation.library", "activity_schedule", "daily_movie_projection")[0]
        self.assertEqual(movie["normalized_value"]["start"], "17:00")
        self.assertEqual(movie["normalized_value"]["end"], "19:00")

    def test_day_pass_current_rate_and_inclusions(self):
        self.assertEqual(self._find("recreation.day_pass", "opening_hours", "daily_access_window")[0]["normalized_value"], {"start":"06:30","end":"18:30"})
        self.assertEqual(self._find("recreation.day_pass", "price_vnd", "adult_price")[0]["normalized_value"], 300000)
        self.assertEqual(self._find("recreation.day_pass", "price_vnd", "child_price_6_12")[0]["normalized_value"], 200000)
        self.assertIn("games room", self._find("recreation.day_pass", "service_feature", "included_access")[0]["normalized_value"].lower())

    def test_contact_phone_normalization(self):
        phones = [f["normalized_value"] for f in self.approved if f["fact_type"] == "phone"]
        self.assertIn("+84 28 3821 1888", phones)
        self.assertIn("+84 24 3942 8858", phones)
        self.assertTrue(all("84-28)" not in str(v) and "84-24)" not in str(v) for v in phones))

    def test_butler_and_medical_contacts_do_not_claim_24_7(self):
        contacts = json.loads(dataset_path(CONTACTS).read_text(encoding="utf-8"))
        selected = [c for c in contacts if c["contact_id"] in {"contact.butler_service", "contact.medical_centre"}]
        blob = json.dumps(selected, ensure_ascii=False).casefold()
        self.assertNotIn("24/7", blob)
        self.assertNotIn("24시간", blob)
        self.assertNotIn("24小时", blob)

    def test_no_active_fact_references_missing_entity_or_duplicate_semantic_key(self):
        entity_ids = set(self.entities)
        self.assertFalse({f["entity_id"] for f in self.approved} - entity_ids)
        keys = [(f["entity_id"], f["fact_type"], f.get("context")) for f in self.approved]
        self.assertEqual(len(keys), len(set(keys)))

    def test_taya_restaurant_hours_are_not_confused_with_drink_service_window(self):
        opening = self._find("restaurant.taya_house", "opening_hours", "dinner_hours")
        self.assertEqual(opening[0]["normalized_value"], {"start":"18:00","end":"22:00"})
        drinks = self._find("restaurant.taya_house", "service_window", "healthy_drinks_snack")
        self.assertEqual(drinks[0]["normalized_value"], {"start":"10:00","end":"22:00"})
        self.assertFalse(self._find("restaurant.taya_house", "opening_hours", "healthy_drinks_snack"))

    def test_property_profile_is_current_and_does_not_keep_ambiguous_inventory(self):
        profile = json.loads(dataset_path(PROPERTY).read_text(encoding="utf-8"))
        self.assertNotIn("postal_code", profile["address"])
        self.assertNotIn("total_accommodations", profile)
        self.assertEqual(profile["inventory"]["rooms_and_suites"], 198)
        self.assertEqual(profile["inventory"]["private_pool_villas"], 68)
        self.assertEqual(profile["electricity"]["voltage"], "220V")
        self.assertEqual(profile["electricity"]["frequency"], "50Hz")
        self.assertNotIn("hotline_operator", profile["contact"])
        self.assertEqual(profile["contact"]["fnb_phone"], "+84 236 651 9999")

    def test_service_catalog_does_not_invent_hours_fees_or_mix_extensions(self):
        services = {s["service_id"]: s for s in json.loads(dataset_path(SERVICE_CATALOG).read_text(encoding="utf-8"))}
        self.assertEqual(services["service.in_room_dining"]["entity_id"], "service.in_room_dining")
        self.assertEqual(services["service.in_room_dining"]["contact_extension"], "10")
        self.assertEqual(services["dining.restaurant_reservation"]["contact_extension"], "12")
        self.assertEqual(services["transportation.taxi"]["operating_hours"], "24/7")
        for sid in ["transportation.car_rental","service.courier","service.flight_reconfirmation","service.currency_exchange","service.extra_bed","service.first_aid"]:
            self.assertIsNone(services[sid]["operating_hours"], sid)
        self.assertFalse(services["service.extra_bed"]["is_complimentary"])
        self.assertFalse(services["service.babysitting"]["is_complimentary"])
        self.assertFalse(services["service.late_checkout"]["is_complimentary"])
        for sid, item in services.items():
            self.assertEqual(item.get("verification_status"), "official_web_reviewed", sid)
            self.assertTrue(item.get("source_urls"), sid)
            self.assertTrue(all("furamavietnam.com" in u for u in item["source_urls"]), sid)

    def test_verified_airport_transfer_and_medical_actions_are_present(self):
        services = {s["service_id"]: s for s in json.loads(dataset_path(SERVICE_CATALOG).read_text(encoding="utf-8"))}
        self.assertIn("transportation.airport_transfer", services)
        self.assertIn("service.medical_centre", services)
        self.assertIn("before 21:00", services["transportation.airport_transfer"]["description"])
        self.assertEqual(services["service.medical_centre"]["contact_extension"], "3420")

    def test_contacts_are_normalized_and_fax_is_not_a_second_sales_phone(self):
        contacts = {c["contact_id"]: c for c in json.loads(dataset_path(CONTACTS).read_text(encoding="utf-8"))}
        self.assertEqual(contacts["contact.danang_office"]["phones"], ["+84 236 3847 333", "+84 236 3847 888"])
        self.assertNotIn("internal_extensions", contacts["contact.danang_office"])
        self.assertEqual(contacts["contact.sales_hcm"]["fax"], "+84 28 3821 3246")
        self.assertEqual(contacts["contact.sales_hanoi"]["fax"], "+84 24 3942 8532")
        self.assertEqual(self._find("contact.sales_hcm", "phone", "fax")[0]["normalized_value"], "+84 28 3821 3246")
        self.assertEqual(self._find("contact.sales_hanoi", "phone", "fax")[0]["normalized_value"], "+84 24 3942 8532")
        self.assertFalse(self._find("contact.sales_hcm", "phone", "sales_phone_2"))
        self.assertFalse(self._find("contact.sales_hanoi", "phone", "sales_phone_2"))

    def test_guest_facing_text_facts_have_reviewed_localization(self):
        exempt = {"address"}
        for f in self.approved:
            if f["fact_type"] not in {"policy_rule","service_feature","bed_type"}:
                continue
            raw = str(f.get("raw_value", ""))
            if raw in {"14:00", "11:00"}:
                continue
            loc = f.get("locale_support") or {}
            self.assertTrue(loc.get("vi") and loc.get("ko") and loc.get("zh"), f["canonical_fact_id"])
            self.assertNotEqual(loc.get("vi"), raw, f["canonical_fact_id"])
            self.assertNotEqual(loc.get("ko"), raw, f["canonical_fact_id"])
            self.assertNotEqual(loc.get("zh"), raw, f["canonical_fact_id"])
            self.assertEqual(f.get("localization_method"), "llm_translation_reviewed", f["canonical_fact_id"])


if __name__ == "__main__":
    unittest.main()
