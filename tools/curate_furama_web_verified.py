"""the current curation review: web-verified Furama canonical-data curation.

This deterministic curator repairs extractor artefacts with current official Furama
sources, adds missing guest-facing entities/facts, moves rejected/superseded rows into a separate audit quarantine ledger, and removes graph edges to non-publishable facts.

It intentionally does *not* fetch the web at runtime: evidence was reviewed on
2026-10-01 and is pinned here so repository builds remain reproducible.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("CONCIERGE_CURATION_ROOT", Path(__file__).resolve().parents[1])).resolve()
import sys
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import (
    ALIASES as ALIASES_RELATIVE,
    ENTITIES as ENTITIES_RELATIVE,
    FACTS as FACTS_RELATIVE,
    PLANNING as PLANNING_RELATIVE,
    CONTACTS as CONTACTS_RELATIVE,
    SERVICE_CATALOG as SERVICE_CATALOG_RELATIVE,
    WORKFLOWS as WORKFLOWS_RELATIVE,
    RELATIONS as RELATIONS_RELATIVE,
    QUARANTINE_FACTS,
    dataset_path,
)

DATA = dataset_path(FACTS_RELATIVE, ROOT / "datasets").parent
ENTITIES = dataset_path(ENTITIES_RELATIVE, ROOT / "datasets")
FACTS = dataset_path(FACTS_RELATIVE, ROOT / "datasets")
AUDIT_DIR = dataset_path("quarantine", ROOT / "datasets")
QUARANTINE = dataset_path(QUARANTINE_FACTS, ROOT / "datasets")
ALIASES = dataset_path(ALIASES_RELATIVE, ROOT / "datasets")
RELATIONS = dataset_path(RELATIONS_RELATIVE, ROOT / "datasets")
KNOWLEDGE = ROOT / "knowledge/approved/furama"
VERIFIED_AT = "2026-10-01"
LATEST_REVIEWED_AT = "2026-10-03"
PROPERTY = "FURAMA_DANANG"

CULINARY = "https://furamavietnam.com/culinary/"
ROOM_SERVICE = "https://furamavietnam.com/room-directory/room-facilities-services/room-service/"
MEDICAL = "https://furamavietnam.com/room-directory/security-safety/medical-service/"
FITNESS = "https://furamavietnam.com/room-directory/recreation/fitness-centre/"
KIDS = "https://furamavietnam.com/room-directory/recreation/kids-club/"
POOLS = "https://furamavietnam.com/room-directory/recreation/swimming-pools/"
BEACH = "https://furamavietnam.com/room-directory/recreation/beach/"
GOLF = "https://furamavietnam.com/room-directory/recreation/golf/"
SPA = "https://furamavietnam.com/room-directory/v-senses-wellness-spa-services/the-v-senses-wellness-and-spa/"
SPA_ARTICLE = "https://furamavietnam.com/page/14/?id=307&l=en&p=newsdetail"
WATER_SPORTS = "https://furamavietnam.com/recreations/water-sports/"
DAY_PASS = "https://furamavietnam.com/recreations/non-residential-guests/"
CONTACT = "https://furamavietnam.com/the-resort/contact-us/"
BUTLER = "https://furamavietnam.com/room-directory/room-facilities-services/butler-service-hotline-0911301020/"
CHECKOUT = "https://furamavietnam.com/room-directory/resorts-policies/check-out-time/"
CHECKIN = "https://furamavietnam.com/page/15/?l=en&p=gbook"
HOME = "https://furamavietnam.com/"
ELECTRICITY = "https://furamavietnam.com/room-directory/room-facilities-services/electricity/"
ADAPTERS = "https://furamavietnam.com/room-directory/room-facilities-services/adapters-ext-13/"
BATH_TOWELS = "https://furamavietnam.com/room-directory/room-facilities-services/bath-towels/"
HOUSEKEEPING = "https://furamavietnam.com/room-directory/room-facilities-services/housekeeping/"
ROOM_CLEANING = "https://furamavietnam.com/room-directory/room-facilities-services/room-cleaning-service/"
PET_POLICY = "https://furamavietnam.com/room-directory/resorts-policies/pet-policy/"
SHUTTLE = "https://furamavietnam.com/room-directory/baggage-service/shuttle-bus/"
OCEAN_DELUXE = "https://furamavietnam.com/room-suites/ocean-deluxe/"
EXTRA_BED = "https://furamavietnam.com/room-directory/room-facilities-services/extra-bed/"
CONCIERGE = "https://furamavietnam.com/room-directory/baggage-service/concierge/"
LUGGAGE = "https://furamavietnam.com/room-directory/baggage-service/luggage-service/"
LOST_LUGGAGE = "https://furamavietnam.com/room-directory/baggage-service/lost-luggage/"
LOST_FOUND = "https://furamavietnam.com/room-directory/baggage-service/lost-found/"
TAXI = "https://furamavietnam.com/room-directory/baggage-service/taxi/"
CAR_RENTAL = "https://furamavietnam.com/room-directory/baggage-service/car-rental/"
AIRPORT_TRANSFER = "https://furamavietnam.com/room-directory/baggage-service/airport-transfer/"
FIRST_AID = "https://furamavietnam.com/room-directory/security-safety/first-aid/"
AIRLINE_RES = "https://furamavietnam.com/room-directory/other-services/airline-reservations/"
FLIGHT_INFO = "https://furamavietnam.com/room-directory/other-services/flight-information-reconfirmation-change/"
CURRENCY_EXCHANGE = "https://furamavietnam.com/room-directory/other-services/currency-exchange/"
WAKE_UP = "https://furamavietnam.com/room-directory/other-services/wake-up-calls/"
COURIER = "https://furamavietnam.com/room-directory/other-services/courier-service/"
BABYSITTING_PAGE = "https://furamavietnam.com/room-directory/other-services/babysitting-services/"
RESTAURANTS_BARS = "https://furamavietnam.com/room-directory/fb-services/restaurants-bars/"
TAYA = "https://furamavietnam.com/furama-room-directory/room-directory/fb-services/taya-house/"
WELLNESS = "https://furamavietnam.com/spa-wellness/daily-wellness-activities/"
MEETING_PDF = "https://furamavietnam.com/wp-content/uploads/2025/02/Furama-ICP-Capacity-Charts-Floor-PLan.pdf"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False, sort_keys=False) for x in rows) + "\n", encoding="utf-8")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_id(url: str) -> str:
    return "web_" + sha(url)[:24]


def provenance(url: str, evidence: str, *, dedicated: bool = True) -> list[dict[str, Any]]:
    return [{
        "document_id": document_id(url),
        "source_url": url,
        "evidence": evidence,
        "evidence_sha256": sha(evidence),
        "is_dedicated_page": dedicated,
        "verified_at": VERIFIED_AT,
    }]


def fact_id(entity_id: str, fact_type: str, context: str) -> str:
    return "cfact_" + sha(f"curation|{entity_id}|{fact_type}|{context}")[:24]


def locale_value(en: str, vi: str | None = None, ko: str | None = None, zh: str | None = None) -> dict[str, str]:
    return {"en": en, "vi": vi or en, "ko": ko or en, "zh": zh or en}


entities = load_jsonl(ENTITIES)
facts = load_jsonl(FACTS)
entity_by_id = {e["entity_id"]: e for e in entities}
fact_by_id = {f["canonical_fact_id"]: f for f in facts}


def ensure_entity(entity_id: str, entity_type: str, name: str, domain: str, source_url: str,
                  *, vi: str, ko: str, zh: str, description: str = "") -> dict[str, Any]:
    if entity_id in entity_by_id:
        e = entity_by_id[entity_id]
        e["publication_status"] = "approved"
        e["name"] = name
        e["entity_type"] = entity_type
        e["domain"] = domain
        e["names_by_locale"] = {"en": name, "vi": vi, "ko": ko, "zh": zh}
        if description:
            e["description"] = description
        e["verified_at"] = VERIFIED_AT
        e.setdefault("source_urls", [])
        if source_url not in e["source_urls"]:
            e["source_urls"].append(source_url)
        return e
    e = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "name": name,
        "names_by_locale": {"en": name, "vi": vi, "ko": ko, "zh": zh},
        "domain": domain,
        "source_urls": [source_url],
        "description": description,
        "publication_status": "approved",
        "verified_at": VERIFIED_AT,
    }
    entities.append(e)
    entity_by_id[entity_id] = e
    return e


def suppress(entity_id: str, reason: str) -> None:
    e = entity_by_id.get(entity_id)
    if e:
        e["publication_status"] = "legacy_unverified"
        e["curation_reason"] = reason
        e["curated_at"] = VERIFIED_AT


def quarantine_where(predicate, status: str, reason: str) -> int:
    n = 0
    for f in facts:
        if predicate(f) and f.get("publication_status", "approved") == "approved":
            f["publication_status"] = status
            f["curation_reason"] = reason
            f["curated_at"] = VERIFIED_AT
            n += 1
    return n


def upsert_fact(entity_id: str, fact_type: str, context: str, raw: str, normalized: Any,
                url: str, evidence: str, *, unit: str | None = None,
                locales: dict[str, str] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    fid = fact_id(entity_id, fact_type, context)
    row = {
        "canonical_fact_id": fid,
        "property_id": PROPERTY,
        "entity_id": entity_id,
        "fact_type": fact_type,
        "context": context,
        "raw_value": raw,
        "normalized_value": normalized,
        "unit": unit,
        "confidence": 1.0,  # compatibility field; objective verification metadata is authoritative
        "verification_status": "official_verified",
        "source_tier": "official_primary",
        "provenance_sources": provenance(url, evidence),
        "locale_support": locales or locale_value(raw),
        "publication_status": "approved",
        "verified_at": VERIFIED_AT,
        "curation_method": "official_web_manual_review",
    }
    if locales is not None:
        row["localization_method"] = "llm_translation_reviewed"
        row["localization_reviewed_at"] = VERIFIED_AT
    if extra:
        row.update(extra)
    if fid in fact_by_id:
        target = fact_by_id[fid]
        target.clear(); target.update(row)
        return target
    # Curator upgrades must be idempotent across earlier extractor/curator IDs.
    # Reuse an existing semantic slot instead of publishing two approved facts
    # with the same entity/type/context merely because the historical ID differs.
    semantic = next((candidate for candidate in facts
                     if candidate.get("entity_id") == entity_id
                     and candidate.get("fact_type") == fact_type
                     and candidate.get("context") == context
                     and candidate.get("publication_status", "approved") == "approved"), None)
    if semantic is not None:
        old_id = semantic.get("canonical_fact_id")
        row["canonical_fact_id"] = old_id
        semantic.clear(); semantic.update(row)
        fact_by_id[old_id] = semantic
        return semantic
    facts.append(row)
    fact_by_id[fid] = row
    return row


# ---------------------------------------------------------------------------
# 1. Remove known misattributions from the guest-facing layer.
# ---------------------------------------------------------------------------
quarantine_where(
    lambda f: f["entity_id"] == "property.furama_resort_danang" and f.get("context") in {"room_service_hours", "late_night_room_service_hours"},
    "needs_reclassification", "Room-service schedule belongs to the In-Room Dining service, not the property entity."
)
quarantine_where(
    lambda f: f["entity_id"] == "restaurant.cafe_indochine" and f.get("context") == "daily_operating_hours",
    "rejected_extraction", "Current official page publishes distinct breakfast, a-la-carte and buffet windows; a single continuous 06:30-22:00 window is misleading."
)
quarantine_where(
    lambda f: f["entity_id"] == "bar.hai_van_lounge" and f.get("context") == "afternoon_tea_hours",
    "needs_reverification", "Current official culinary page confirms lounge opening hours but not this historic afternoon-tea time window."
)
quarantine_where(
    lambda f: f["entity_id"] == "recreation.swimming_pools" and f.get("fact_type") == "opening_hours",
    "needs_reverification", "Current official swimming-pools page describes facilities/lifeguards but does not publish pool opening hours."
)
quarantine_where(
    lambda f: f["entity_id"] == "recreation.library" and f.get("fact_type") == "opening_hours" and f.get("context") == "daily_movie_projection",
    "needs_reclassification", "17:00-19:00 is the daily movie projection schedule, not Library opening hours."
)
quarantine_where(
    lambda f: f["entity_id"] == "service.babysitting" and f.get("context") == "advance_booking_notice",
    "needs_reverification", "Superseded by the current curation review conflict-aware review: official Babysitting and Kids Club pages publish different advance-notice guidance."
)
quarantine_where(
    lambda f: f["entity_id"] == "service.first_aid" and f.get("context") == "medical_availability",
    "rejected_extraction", "Current official Medical Centre page publishes bounded staffed hours, not 24/7."
)
quarantine_where(
    lambda f: f["entity_id"] == "meeting.banquets" and f.get("fact_type") == "capacity",
    "rejected_extraction", "Legacy extractor truncated a larger banquet statement into capacity=2; no valid venue capacity is supported."
)
quarantine_where(
    lambda f: f["entity_id"] == "meeting.international_convention_palace" and f.get("fact_type") in {"area_sqm", "capacity"},
    "needs_reclassification", "Legacy rows mixed individual-room dimensions/capacities into the ICP parent entity."
)
quarantine_where(
    lambda f: f["entity_id"] == "spa.treatments" and f.get("fact_type") == "price_vnd",
    "rejected_extraction", "Price was extracted from a non-treatment menu/source; current official spa page verifies treatments/durations but not these prices."
)
# Old room 'floor_area' duplicates and room facts are replaced by dedicated current pages.
room_entities_existing = {e["entity_id"] for e in entities if e["entity_id"].startswith("room.") and e["entity_id"] not in {"room.beach_front_villas", "room.pool_villas"}}
quarantine_where(
    lambda f: f["entity_id"] in room_entities_existing and (f.get("fact_type") in {"area_sqm", "bed_type", "policy_rule"} and f.get("context") in {"floor_area", "room_area", "bed_configuration", "occupancy_policy"}),
    "needs_reverification", "Superseded by the current curation review dedicated-room-page verification."
)

# ---------------------------------------------------------------------------
# 2. Rooms: dedicated current pages win over aggregate extractor output.
# ---------------------------------------------------------------------------
room_specs = [
    ("room.lagoon_superior", "Lagoon Superior", 40.1, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children", "https://furamavietnam.com/room-suites/lagoon-superior/", "AREA: 40.1 SQM. PLUS 11.3 SQM. BALCONY. One King / Twin Bed. Max occupancy 3 Adults or 2 Adults & 2 Children"),
    ("room.garden_superior", "Garden Superior", 40.1, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children", "https://furamavietnam.com/room-suites/garden-superior/", "AREA: 40.1 SQM. PLUS 11.3 SQM. BALCONY. One King / Twin Bed. Max occupancy 3 Adults or 2 Adults & 2 Children"),
    ("room.garden_deluxe", "Garden Deluxe", 43.7, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children", "https://furamavietnam.com/room-suites/garden-deluxe/", "AREA: 43.7 SQM. PLUS 11.3 SQM. BALCONY. King/Twin. Max occupancy 3 Adults or 2 Adults & 2 Children"),
    ("room.ocean_deluxe", "Ocean Deluxe", 43.7, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children", "https://furamavietnam.com/room-suites/ocean-deluxe/", "AREA: 43.7 SQM. PLUS 11.3 SQM. BALCONY. King/Twin. Max occupancy 3 Adults or 2 Adults & 2 Children"),
    ("room.ocean_studio_suite", "Ocean Studio Suite", 41.1, 11.3, "King or Twin", "3 Adults or 2 Adults & 2 Children", "https://furamavietnam.com/room-suites/ocean-studio-suite/", "AREA: 41.1 SQM. PLUS 11.3 SQM. BALCONY. King/Twin. Max occupancy 3 Adults or 2 Adults & 2 Children"),
    ("room.ocean_suite", "Ocean Suite", 85.8, 24.0, "King", "3 Adults or 2 Adults & 2 Children", "https://furamavietnam.com/room-suites/ocean-suite/", "AREA: 85.8 SQM. PLUS 24 SQM. BALCONY. King Bed. Max occupancy 3 Adults or 2 Adults & 2 Children"),
    ("room.presidential_suite", "Presidential Suite", 90.0, 23.0, "King", "3 Adults or 2 Adults & 2 Children", "https://furamavietnam.com/room-suites/presidential-suite/", "AREA: 90 SQM. PLUS 23 SQM. BALCONY. King Bed. Max occupancy 3 Adults or 2 Adults & 2 Children"),
    ("room.family_room_by_balcony", "Family Room by Balcony", 85.8, 23.0, "1 King + 2 Single", "8 guests or 4 Adults & 4 Children", "https://furamavietnam.com/room-suites/family-room-by-balcony/", "AREA: 85.8 SQM. PLUS 23 SQM. BALCONY. 1 King + 2 Single beds. Max occupancy 8 or 4 Adults & 4 Children"),
]
ensure_entity("room.family_room_by_balcony", "room", "Family Room by Balcony", "rooms", room_specs[-1][6], vi="Phòng Gia đình có Ban công", ko="발코니 패밀리 룸", zh="阳台家庭房")
for eid, _name, area, balcony, bed, occ, url, evidence in room_specs:
    upsert_fact(eid, "area_sqm", "room_area", f"{area:g} sqm", area, url, evidence, unit="sqm")
    upsert_fact(eid, "balcony_area_sqm", "balcony_area", f"{balcony:g} sqm balcony", balcony, url, evidence, unit="sqm")
    upsert_fact(eid, "bed_type", "bed_configuration", bed, bed, url, evidence, locales=locale_value(bed))
    upsert_fact(eid, "policy_rule", "occupancy_policy", occ, occ, url, evidence, locales=locale_value(occ))

# ---------------------------------------------------------------------------
# 3. Current food & beverage venue schedules and time-bounded offers.
# ---------------------------------------------------------------------------
new_venues = [
    ("restaurant.v_senses_cafe", "restaurant", "V-Senses Cafe", "V-Senses Café", "V-센스 카페", "V-Senses 咖啡厅"),
    ("bar.ocean_terrace", "bar", "The Ocean Terrace", "The Ocean Terrace", "더 오션 테라스", "海洋露台"),
    ("bar.lamuse_gourmet_cafe", "bar", "L’Amuse Gourmet Cafe", "L’Amuse Gourmet Cafe", "라뮤즈 고메 카페", "L’Amuse 精品咖啡厅"),
]
for eid, typ, en, vi, ko, zh in new_venues:
    ensure_entity(eid, typ, en, "dining", CULINARY, vi=vi, ko=ko, zh=zh)
suppress("bar.ocean_bar", "Superseded by the current official venue name The Ocean Terrace; historic Ocean Bar is not published in the 2026 culinary directory.")

# Quarantine all existing opening rows for these venues, then republish exact current windows.
for eid in {"restaurant.cafe_indochine", "restaurant.don_cipriani", "restaurant.danaksara", "restaurant.the_fan_steakhouse", "restaurant.taya_house", "bar.hai_van_lounge", "bar.lagoon_bar"}:
    quarantine_where(lambda f, eid=eid: f["entity_id"] == eid and f.get("fact_type") == "opening_hours", "needs_reverification", "Superseded by the current curation review current culinary-page verification.")

culinary_hours = [
    ("restaurant.cafe_indochine", "breakfast_hours", "06:30", "10:30", "Buffet Breakfast 06:30 to 10:30"),
    ("restaurant.cafe_indochine", "a_la_carte_lunch", "11:30", "14:00", "A la carte 11:30 TO 14:00 AND 18:00 TO 22:00"),
    ("restaurant.cafe_indochine", "a_la_carte_dinner", "18:00", "22:00", "A la carte 11:30 TO 14:00 AND 18:00 TO 22:00"),
    ("restaurant.cafe_indochine", "seafood_steak_buffet_dinner", "18:30", "22:00", "Seafood & Steak Buffet Dinner 18:30 to 22:00"),
    ("restaurant.don_cipriani", "daily_lunch", "11:30", "14:00", "DAILY LUNCH 11:30 TO 14:00"),
    ("restaurant.don_cipriani", "daily_dinner", "18:00", "22:00", "Daily Dinner 18:00 to 22:00"),
    ("restaurant.taya_house", "dinner_hours", "18:00", "22:00", "Opening hours: 18:00 – 22:00"),
    ("restaurant.danaksara", "breakfast_hours", "06:30", "10:30", "Buffet Breakfast 06:30 to 10:30"),
    ("restaurant.danaksara", "a_la_carte_dinner", "18:00", "22:00", "A la carte 18:00 to 22:00"),
    ("restaurant.the_fan_steakhouse", "dinner_hours", "18:00", "22:00", "DINNER 18:00 to 22:00"),
    ("bar.hai_van_lounge", "daily_hours", "08:00", "00:00", "OPEN Daily from 08:00 to 00:00"),
    ("restaurant.v_senses_cafe", "daily_hours", "06:30", "21:00", "OPEN Daily from 06:30 to 21:00"),
    ("bar.ocean_terrace", "daily_hours", "10:00", "21:00", "OPEN DAILY FROM 10:00 TO 21:00"),
    ("bar.lamuse_gourmet_cafe", "daily_hours", "09:00", "21:00", "OPEN DAILY FROM 9:00 TO 21:00"),
    ("bar.lagoon_bar", "daily_hours", "10:00", "18:00", "OPEN DAILY FROM 10:00 TO 18:00"),
]
for eid, context, start, end, evidence in culinary_hours:
    source_url = TAYA if eid == "restaurant.taya_house" and context == "dinner_hours" else CULINARY
    upsert_fact(eid, "opening_hours", context, f"{start} - {end}", {"start": start, "end": end}, source_url, evidence)
# Semantic attributes are canonical facts, not free-form keyword stuffing. They
# are pinned only when the official source explicitly states the attribute.
upsert_fact(
    "restaurant.don_cipriani", "cuisine_type", "primary_cuisine", "Italian", "Italian", CULINARY,
    "Cuisine Italian",
    locales=locale_value("Italian cuisine", "Ẩm thực Ý", "이탈리아 요리", "意大利菜"),
)
# The current culinary overview separately advertises healthy drinks/snacks from
# 10:00–22:00. This is a service window, not the restaurant's opening hours.
upsert_fact(
    "restaurant.taya_house", "service_window", "healthy_drinks_snack",
    "10:00 - 22:00", {"start": "10:00", "end": "22:00"}, CULINARY,
    "HEALTHY DRINKS & SNACK 10:00 TO 22:00",
)
# Taya cooking class occurs at two start times, represented as schedule facts rather than fake continuous hours.
upsert_fact("restaurant.taya_house", "activity_schedule", "cooking_class_1100", "11:00 daily", {"days": ["MON","TUE","WED","THU","FRI","SAT","SUN"], "start": "11:00"}, CULINARY, "Vietnamese Cooking class 11:00 or 14:00 daily")
upsert_fact("restaurant.taya_house", "activity_schedule", "cooking_class_1400", "14:00 daily", {"days": ["MON","TUE","WED","THU","FRI","SAT","SUN"], "start": "14:00"}, CULINARY, "Vietnamese Cooking class 11:00 or 14:00 daily")

# Current offers are dynamic, time-bounded, and therefore carry expiry/basis metadata.
offers = [
    ("restaurant.cafe_indochine", "seafood_steak_buffet_offer", 888000, "++", "Seafood & Steak Buffet Dinner", "VND 888,000++ /guest; From now until 31/12/2026"),
    ("restaurant.cafe_indochine", "vietnamese_copper_tray_offer", 390000, "net", "Vietnamese Copper Tray Meal", "VND 390,000 net/guest; From now until 31/12/2026"),
    ("restaurant.taya_house", "cooking_class_offer", 799000, "++", "Cooking Class At Tàya House", "VND 799,000VND++ /guest; From now until 31/12/2026"),
    ("bar.hai_van_lounge", "afternoon_tea_dessert_buffet_offer", 450000, "net", "Afternoon Tea & Dessert Buffet", "VND 450,000 net/guest; From now until 31/12/2026"),
    ("restaurant.cafe_indochine", "international_breakfast_buffet_offer", 498000, "++", "International Breakfast Buffet", "VND 498,000++ /guest; From now until 31/12/2026"),
]
for eid, context, price, tax_basis, label, evidence in offers:
    upsert_fact(eid, "price_vnd", context, f"{price:,} VND", price, CULINARY, evidence, unit="VND",
                locales=locale_value(f"{label}: {price:,} VND"),
                extra={"valid_until": "2026-12-31", "price_basis": "per_guest", "tax_basis": tax_basis})

# Ensure service/action entities exist before adding canonical facts.
ensure_entity(
    "dining.restaurant_reservation", "service", "Restaurant & Bar Reservation", "dining", RESTAURANTS_BARS,
    vi="Đặt bàn Nhà hàng & Bar", ko="레스토랑 및 바 예약", zh="餐厅及酒吧预订",
)
ensure_entity(
    "transportation.airport_transfer", "service", "Airport Transfer", "transportation", AIRPORT_TRANSFER,
    vi="Đưa đón sân bay", ko="공항 이동 서비스", zh="机场接送",
)
ensure_entity(
    "service.medical_centre", "service", "Medical Centre", "guest_services", MEDICAL,
    vi="Trung tâm Y tế", ko="의료 센터", zh="医疗中心",
)

# ---------------------------------------------------------------------------
# 4. Guest services & recreation from current official directory pages.
# ---------------------------------------------------------------------------
ensure_entity("service.in_room_dining", "service", "In-Room Dining (Room Service)", "guest_services", ROOM_SERVICE,
              vi="Dịch vụ ăn uống tại phòng", ko="인룸 다이닝", zh="客房送餐")
upsert_fact("service.in_room_dining", "opening_hours", "operating_hours", "06:30 - 00:00", {"start":"06:30","end":"00:00"}, ROOM_SERVICE, "Operating hours: From 6:30am to mid night .")
upsert_fact("service.in_room_dining", "opening_hours", "late_night_dishes", "22:30 - 06:00", {"start":"22:30","end":"06:00"}, ROOM_SERVICE, "Late night dishes are available from 22:30pm to 06:00 am next morning .")
upsert_fact("service.in_room_dining", "extension", "front_desk_contact", "Ext. 10", "10", ROOM_SERVICE, "Kindly contact the Front Desk at ext. 10.")
upsert_fact("service.in_room_dining", "policy_rule", "outside_food_policy", "Outside food/drinks and cooking in-room are not allowed for safety and hygiene reasons", "Outside food/drinks and cooking in-room are not allowed for safety and hygiene reasons", ROOM_SERVICE, "For safety and hygenic reasons, we do not allow to bring food and drinks from outside into the resort as well as cooking in the room")
upsert_fact(
    "service.in_room_dining", "service_feature", "room_delivery",
    "International and local cuisines are delivered to the guest room",
    "International and local cuisines are delivered to the guest room",
    ROOM_SERVICE,
    "A creative menu of international and local cuisines is available and will be promptly delivered to your room.",
    locales=locale_value(
        "International and local cuisines delivered to the guest room",
        "Món ăn quốc tế và địa phương được giao tận phòng",
        "세계 각국 및 현지 요리를 객실로 배달",
        "国际及本地菜肴可送至客房",
    ),
)

ensure_entity("service.medical_centre", "service", "Medical Centre", "guest_services", MEDICAL,
              vi="Trung tâm Y tế", ko="의료 센터", zh="医疗中心")
upsert_fact("service.medical_centre", "opening_hours", "staffed_hours", "08:00 - 17:00", {"start":"08:00","end":"17:00"}, MEDICAL, "Medical Center hours: daily from 08:00 am to 05:00 pm except Saturday afternoon and Sunday.")
upsert_fact("service.medical_centre", "policy_rule", "hours_exception", "Except Saturday afternoon and Sunday", "Except Saturday afternoon and Sunday", MEDICAL, "Medical Center hours: daily from 08:00 am to 05:00 pm except Saturday afternoon and Sunday.")
upsert_fact("service.medical_centre", "extension", "medical_or_operator", "3420/0", "3420/0", MEDICAL, "Please call the Medical Centre or Operator via ext. 3420/0")

# Refresh exact verified existing recreation schedules.
ensure_entity("recreation.fitness_center", "recreation", "Fitness Center", "recreation", FITNESS,
              vi="Phòng tập Thể dục Fitness Center", ko="피트니스 센터", zh="健身中心")
ensure_entity("recreation.kids_club", "recreation", "Kids Club", "recreation", KIDS,
              vi="Câu lạc bộ Trẻ em", ko="키즈 클럽", zh="儿童俱乐部")
quarantine_where(lambda f: f["entity_id"] in {"recreation.fitness_center","recreation.tennis_club","recreation.kids_club"} and f.get("fact_type") == "opening_hours", "needs_reverification", "Superseded by current official recreation-page verification.")
upsert_fact("recreation.fitness_center", "opening_hours", "daily_hours", "06:00 - 21:00", {"start":"06:00","end":"21:00"}, FITNESS, "At Furama Resort and Furama Villas: Open daily from 06:00 am to 09:00 pm.")
upsert_fact(
    "recreation.fitness_center", "service_feature", "exercise_equipment",
    "Nautilus machines, free weights and cardiovascular equipment",
    "Nautilus machines, free weights and cardiovascular equipment",
    FITNESS,
    "you will find a wide range of Nautilus and free weights as well as many different cardiovascular equipments for exercising.",
    locales=locale_value(
        "Nautilus machines, free weights and cardiovascular equipment",
        "Máy Nautilus, tạ tự do và thiết bị tập tim mạch",
        "노틸러스 기구, 프리웨이트 및 유산소 운동 기구",
        "Nautilus器械、自由重量及多种有氧健身设备",
    ),
)
# Tennis current value is already 06-20 in the canonical set; provenance is pinned to the official recreation directory.
upsert_fact("recreation.tennis_club", "opening_hours", "daily_hours", "06:00 - 20:00", {"start":"06:00","end":"20:00"}, "https://furamavietnam.com/room-directory/recreation/tennis-court/", "Tennis Court open daily from 06:00 am to 08:00 pm.")
upsert_fact("recreation.kids_club", "opening_hours", "regular_hours", "08:30 - 18:00", {"start":"08:30","end":"18:00"}, KIDS, "Open daily from 8:30 am to 6:00 pm, excepted Friday and Saturday from 8:30am to 9:00pm.")
upsert_fact("recreation.kids_club", "opening_hours", "friday_saturday_hours", "08:30 - 21:00", {"start":"08:30","end":"21:00"}, KIDS, "Open daily from 8:30 am to 6:00 pm, excepted Friday and Saturday from 8:30am to 9:00pm.")
upsert_fact(
    "recreation.kids_club", "service_feature", "children_activities",
    "Children activities include dragon dancing, swimming lessons, kite flying, PlayStation, baking/cooking and statue painting",
    "Children activities include dragon dancing, swimming lessons, kite flying, PlayStation, baking/cooking and statue painting",
    KIDS,
    "various activities such as dragon dancing, swimming lessons, kite flying, play station 3, baking and cooking class, statue painting",
    locales=locale_value(
        "Children activities include dragon dancing, swimming lessons, kite flying, PlayStation, baking/cooking and statue painting",
        "Hoạt động cho trẻ gồm múa lân, học bơi, thả diều, PlayStation, làm bánh/nấu ăn và tô tượng",
        "어린이 활동에는 용춤, 수영 강습, 연날리기, PlayStation, 베이킹·요리 및 조각상 색칠이 포함됩니다",
        "儿童活动包括舞龙、游泳课程、放风筝、PlayStation、烘焙/烹饪和雕像彩绘",
    ),
)
ensure_entity("service.babysitting", "service", "Babysitting Services", "guest_services", BABYSITTING_PAGE,
              vi="Dịch vụ Trông trẻ", ko="베이비시팅 서비스", zh="儿童看护服务")
upsert_fact(
    "service.babysitting", "policy_rule", "advance_booking_notice",
    "Book one day in advance; additional charges apply.",
    "Book one day in advance; additional charges apply.",
    BABYSITTING_PAGE,
    "Please contact the Tour Desk or Kid Club one day in advance to make reservation. Additional charges will apply.",
    locales=locale_value(
        "Book one day in advance; additional charges apply.",
        "Vui lòng đặt trước một ngày; dịch vụ có tính phí bổ sung.",
        "하루 전에 예약해 주세요. 추가 요금이 적용됩니다.",
        "请提前一天预订；此服务需额外收费。",
    ),
    extra={"curation_resolution":"dedicated_service_page_preferred"},
)
upsert_fact("service.babysitting", "extension", "tour_desk_contact", "3777", "3777", KIDS, "For babysitting services, please contact the Tour Desk via number 3777 or Kid Club")

ensure_entity("recreation.beach", "recreation", "Furama Beach", "recreation", BEACH,
              vi="Bãi biển Furama", ko="푸라마 비치", zh="富丽华海滩")
upsert_fact("recreation.beach", "distance_km", "beach_length", "1 km of My Khe Beach", 1.0, BEACH, "Occupying 1km of luxurious My Khe Beach", unit="km")
upsert_fact("recreation.beach", "activity_schedule", "lifeguard_hours", "Daily 06:00 - 18:00", {"days":["MON","TUE","WED","THU","FRI","SAT","SUN"],"start":"06:00","end":"18:00"}, BEACH, "Lifeguards are on duty from 06:00am. to 06:00pm.")
upsert_fact("recreation.beach", "policy_rule", "night_swimming", "Night swimming is not permitted", "Night swimming is not permitted", BEACH, "Night swimming is not permitted.")

# Water-sports page publishes lifeguard coverage, not pool opening hours.
upsert_fact("recreation.swimming_pools", "activity_schedule", "lifeguard_hours", "Daily 06:00 - 18:30", {"days":["MON","TUE","WED","THU","FRI","SAT","SUN"],"start":"06:00","end":"18:30"}, WATER_SPORTS, "Lifeguard Hours: Pools: 06:00 AM – 06:30 PM; Beach: 06:00 AM – 06:00 PM")

# High-frequency stay operations verified from current official Furama pages.
# Replace legacy combined late-checkout prose with atomic, independently citable rules.
quarantine_where(
    lambda f: f["entity_id"] == "property.furama_resort_danang" and f.get("context") == "late_checkout_policy",
    "needs_reclassification",
    "Superseded by separate official late-checkout rules for departures through 18:00 and after 18:00.",
)
ensure_entity("service.housekeeping", "service", "Housekeeping Services", "guest_services", HOUSEKEEPING,
              vi="Dịch vụ Buồng phòng", ko="하우스키핑 서비스", zh="客房服务")
upsert_fact(
    "service.housekeeping", "service_feature", "extra_room_items",
    "Housekeeping can provide extra blankets, pillows, coat hangers and related items",
    "Housekeeping can provide extra blankets, pillows, coat hangers and related items",
    HOUSEKEEPING,
    "Our Housekeeping department will be pleased to supply you with extra blankets, pillows, coat hangers and other related items.",
    locales=locale_value(
        "Housekeeping can provide extra blankets, pillows, coat hangers and related items",
        "Bộ phận Buồng phòng có thể cung cấp thêm chăn, gối, móc áo và các vật dụng liên quan",
        "하우스키핑에서 추가 담요, 베개, 옷걸이 및 관련 물품을 제공할 수 있습니다",
        "客房部可提供额外毛毯、枕头、衣架及相关用品",
    ),
)
ensure_entity("transportation.airport_transfer", "transportation", "Airport Transfer", "transportation", AIRPORT_TRANSFER,
              vi="Đưa đón sân bay", ko="공항 이동 서비스", zh="机场接送")
upsert_fact(
    "transportation.airport_transfer", "policy_rule", "early_departure_booking_deadline",
    "For early-morning departures, book airport transfer before 21:00 on the previous day",
    "For early-morning departures, book airport transfer before 21:00 on the previous day",
    AIRPORT_TRANSFER,
    "For early morning departure, please book the transfer before 9:00p.m. on the day before departure.",
    locales=locale_value(
        "For early-morning departures, book airport transfer before 21:00 on the previous day",
        "Nếu khởi hành sáng sớm, vui lòng đặt xe sân bay trước 21:00 của ngày hôm trước",
        "이른 아침 출발은 전날 21:00 이전에 공항 이동을 예약해 주세요",
        "清晨出发请在前一天21:00前预订机场接送",
    ),
)
ensure_entity("transportation.taxi", "transportation", "Taxi Service", "transportation", TAXI,
              vi="Dịch vụ Taxi", ko="택시 서비스", zh="出租车服务")
upsert_fact(
    "transportation.taxi", "service_window", "taxi_availability",
    "24 hours", {"start":"00:00","end":"24:00"}, TAXI,
    "Taxi is always available 24 hours at the front entrance of the Resort.",
    locales=locale_value("Taxi is available 24 hours", "Taxi có sẵn 24 giờ", "택시는 24시간 이용 가능합니다", "出租车24小时可用"),
)
upsert_fact(
    "property.furama_resort_danang", "amenity_feature", "wifi_availability",
    "WiFi is available in guest rooms", "WiFi is available in guest rooms", OCEAN_DELUXE,
    "WiFi, Cable/Satellite TV",
    locales=locale_value("WiFi is available in guest rooms", "Phòng khách có Wi-Fi", "객실에서 Wi-Fi를 이용할 수 있습니다", "客房提供Wi-Fi"),
)
upsert_fact(
    "property.furama_resort_danang", "amenity_feature", "minibar_availability",
    "Guest rooms include an in-room minibar with tea/coffee",
    "Guest rooms include an in-room minibar with tea/coffee", OCEAN_DELUXE,
    "In- room mini bar with tea/coffee",
    locales=locale_value(
        "Guest rooms include an in-room minibar with tea/coffee",
        "Phòng khách có minibar trong phòng kèm trà/cà phê",
        "객실에는 차/커피가 포함된 미니바가 있습니다",
        "客房设有迷你吧并提供茶/咖啡",
    ),
)
upsert_fact(
    "property.furama_resort_danang", "policy_rule", "late_checkout_until_18",
    "Late check-out until 18:00 is charged at 50% of the daily room rate and is subject to availability",
    "Late check-out until 18:00 is charged at 50% of the daily room rate and is subject to availability", CHECKOUT,
    "An additional 50% of the daily room rate will be charged for extended check-out until 6:00 pm. Late check-out is subject to availability.",
    locales=locale_value(
        "Late check-out until 18:00 is charged at 50% of the daily room rate and is subject to availability",
        "Trả phòng muộn đến 18:00 tính 50% giá phòng theo ngày và tùy tình trạng phòng",
        "18:00까지의 레이트 체크아웃은 일일 객실 요금의 50%가 부과되며 객실 상황에 따라 가능합니다",
        "延迟退房至18:00收取每日房价的50%，并视房态而定",
    ),
)
upsert_fact(
    "property.furama_resort_danang", "policy_rule", "late_checkout_after_18",
    "Departures after 18:00 are charged one night and are subject to availability",
    "Departures after 18:00 are charged one night and are subject to availability", CHECKOUT,
    "For departures after 6:00pm, one night rate will be charged. Late check-out is subject to availability.",
    locales=locale_value(
        "Departures after 18:00 are charged one night and are subject to availability",
        "Trả phòng sau 18:00 tính một đêm và tùy tình trạng phòng",
        "18:00 이후 출발은 1박 요금이 부과되며 객실 상황에 따라 가능합니다",
        "18:00后退房收取一晚房费，并视房态而定",
    ),
)

# Day Pass is a current guest-facing product with explicit hours, prices and inclusions.
ensure_entity("recreation.day_pass", "recreation_pass", "Non-Residential Guest Day Pass", "recreation", DAY_PASS,
              vi="Vé Day Pass cho khách không lưu trú", ko="비투숙객 데이 패스", zh="非住店客人日票")
upsert_fact("recreation.day_pass", "opening_hours", "daily_access_window", "06:30 - 18:30", {"start":"06:30","end":"18:30"}, DAY_PASS, "Day Pass Package: Time Daily from 6:30 to 18:30")
upsert_fact("recreation.day_pass", "price_vnd", "adult_price", "300,000 VND", 300000, DAY_PASS, "Adult VND300,000", unit="VND", locales=locale_value("Adult: 300,000 VND"), extra={"price_basis":"per_guest","tax_basis":"inclusive","verified_at":VERIFIED_AT,"price_temporality":"current_listed_rate"})
upsert_fact("recreation.day_pass", "price_vnd", "child_price_6_12", "200,000 VND", 200000, DAY_PASS, "Kid VND200,000 (6-12 years old)", unit="VND", locales=locale_value("Child age 6-12: 200,000 VND"), extra={"price_basis":"per_guest","tax_basis":"inclusive","verified_at":VERIFIED_AT,"price_temporality":"current_listed_rate"})
upsert_fact("recreation.day_pass", "service_feature", "included_access", "Pools & beach access, sun-chair, umbrella, towels, games room", "Pools & beach access, sun-chair, umbrella, towels, games room", DAY_PASS, "Including Pools & beach access, sun-chair, umbrella, towels, games room")
upsert_fact("recreation.day_pass", "policy_rule", "availability", "Subject to availability", "Subject to availability", DAY_PASS, "Subject to availability")
upsert_fact("recreation.day_pass", "email", "booking_contact", "recreation@furamavietnam.com", "recreation@furamavietnam.com", DAY_PASS, "BOOK NOW (recreation@furamavietnam.com)")
upsert_fact("contact.danang_office", "phone", "secondary_phone", "+84 236 3847 888", "+84 236 3847 888", CONTACT, "Tel.: 84-236-3847 333/888")
upsert_fact("contact.danang_office", "address", "office_address", "103 - 105 Vo Nguyen Giap Street, Ngu Hanh Son Ward, Danang City, Vietnam", "103 - 105 Vo Nguyen Giap Street, Ngu Hanh Son Ward, Danang City, Vietnam", CONTACT, "103 - 105 Vo Nguyen Giap Street, Ngu Hanh Son Ward, Danang City, Vietnam")
upsert_fact("contact.sales_hcm", "address", "office_address", "Suite 5D, Floor 10 Opera View Building, 161 Dong Khoi Street, District 1, HCMC", "Suite 5D, Floor 10 Opera View Building, 161 Dong Khoi Street, District 1, HCMC", CONTACT, "Suite 5D, Floor 10 Opera View Building, 161 Dong Khoi Street, District 1")
upsert_fact("contact.sales_hanoi", "address", "office_address", "Suite 1403, 14th Floor, 109 Tran Hung Dao St., Hoan Kiem, Hanoi", "Suite 1403, 14th Floor, 109 Tran Hung Dao St., Hoan Kiem, Hanoi", CONTACT, "Suite 1403, 14th Floor, 109 Tran Hung Dao St., Hoan Kiem")
# Reclassify official fax numbers under a clear fax context rather than "sales_phone_2".
quarantine_where(lambda f: f["entity_id"] in {"contact.sales_hcm", "contact.sales_hanoi"} and f.get("context") == "sales_phone_2", "needs_reclassification", "Official contact page identifies this number as fax, not a second sales phone.")
upsert_fact("contact.sales_hcm", "phone", "fax", "+84 28 3821 3246", "+84 28 3821 3246", CONTACT, "Fax: +84 28 3821 3246")
upsert_fact("contact.sales_hanoi", "phone", "fax", "+84 24 3942 8532", "+84 24 3942 8532", CONTACT, "Fax: +84 24 3942 8532")

# Game room and library were verified from current Furama Room Directory pages in this curation pass.
GAME = "https://furamavietnam.com/room-directory/recreation/game-room/"
LIBRARY = "https://furamavietnam.com/room-directory/recreation/library/"
ensure_entity("recreation.game_room", "recreation", "Game Room", "recreation", GAME, vi="Phòng Trò chơi", ko="게임룸", zh="游戏室")
upsert_fact("recreation.game_room", "opening_hours", "daily_hours", "09:00 - 18:00", {"start":"09:00","end":"18:00"}, GAME, "Game Room: open from 09:00 to 18:00; football table and PlayStation; located next to the Gift Shop beside the Lobby entrance.")
ensure_entity("recreation.library", "recreation", "Library", "recreation", LIBRARY, vi="Thư viện", ko="도서관", zh="图书馆")
upsert_fact("recreation.library", "activity_schedule", "daily_movie_projection", "Daily 17:00 - 19:00", {"days":["MON","TUE","WED","THU","FRI","SAT","SUN"],"start":"17:00","end":"19:00"}, LIBRARY, "A big projector shows movies from 5:00pm to 7:00pm daily; the library is located above resort lobby level.")
upsert_fact("recreation.library", "service_feature", "books", "Books in English, German, French, Dutch and Japanese are available free of charge", "Books in English, German, French, Dutch and Japanese are available free of charge", LIBRARY, "Books in English, German, French, Dutch and Japanese are available free of charge.")

# Spa and wellness.
quarantine_where(lambda f: f["entity_id"] == "spa.v_senses_wellness" and f.get("fact_type") == "opening_hours", "needs_reclassification", "Wellness class times belong to the daily wellness programme; spa opening hours are republished separately.")
upsert_fact("spa.v_senses_wellness", "opening_hours", "daily_hours", "09:00 - 22:00", {"start":"09:00","end":"22:00"}, SPA, "Daily opening hours: 09:00am to 10:00pm.")
upsert_fact("spa.v_senses_wellness", "extension", "spa_reception", "Ext. 16", "16", SPA, "For the booking, please dial extension 16 to make an appointment.")
upsert_fact("spa.v_senses_wellness", "policy_rule", "last_appointment", "Last appointment 20:30", "20:30", SPA_ARTICLE, "Operating Hours: Open from 09:00 - 22:00. Reservation: Accepting bookings until 20:30; please schedule treatments 24 hours in advance.")
upsert_fact("spa.v_senses_wellness", "policy_rule", "advance_booking_notice", "Schedule treatments 24 hours in advance", "24 hours in advance", SPA_ARTICLE, "Operating Hours: Open from 09:00 - 22:00. Reservation: Accepting bookings until 20:30; please schedule treatments 24 hours in advance.")
ensure_entity("spa.daily_wellness", "wellness_program", "Daily Wellness Activities", "spa", WELLNESS,
              vi="Hoạt động Wellness Hằng ngày", ko="데일리 웰니스 프로그램", zh="每日康体活动")
wellness_rows = [
    ("early_bird_yoga", ["MON","TUE","FRI","SAT","SUN"], "07:30", "08:15", "MON-TUE; FRI-SUN - 7:30 – 8:15"),
    ("free_basic_coaching", ["MON","TUE","WED","THU","FRI","SAT","SUN"], "08:00", "09:00", "DAILY 08:00 – 09:00"),
    ("sound_therapy_ritual", ["MON","TUE","THU","FRI","SAT","SUN"], "11:45", "12:00", "MON-TUE; THU-SUN -11:45 – 12:00"),
    ("relaxing_yoga", ["MON","THU","FRI","SAT","SUN"], "17:15", "18:00", "MON; THU-SUN - 17:15 – 18:00"),
]
for ctx, days, start, end, ev in wellness_rows:
    upsert_fact("spa.daily_wellness", "activity_schedule", ctx, ev, {"days":days,"start":start,"end":end}, WELLNESS, ev)

# ---------------------------------------------------------------------------
# 5. Property policy, contact and Butler facts re-verified on official pages.
# ---------------------------------------------------------------------------
# Current contact page: normalize all office phones into unambiguous international display form.
contact_facts = [
    ("contact.danang_office", "phone", "main_phone", "+84 236 3847 333", CONTACT, "Danang Office: (84-236) 3847 333"),
    ("contact.danang_office", "phone", "fax", "+84 236 3847 666", CONTACT, "Danang Office: (84-236) 3847 666"),
    ("contact.reservation", "email", "room_reservation", "reservation@furamavietnam.com", CONTACT, "Danang Office: reservation@furamavietnam.com"),
    ("contact.sales_hcm", "phone", "sales_phone_1", "+84 28 3821 1888", CONTACT, "Ho Chi Minh City Office: (84-28) 382 11 888"),
    ("contact.sales_hcm", "phone", "fax", "+84 28 3821 3246", CONTACT, "Ho Chi Minh City Office Fax: (84-28) 382 13 246"),
    ("contact.sales_hcm", "email", "sales_office", "salesoffice@furamavietnam.com", CONTACT, "Ho Chi Minh City Office: salesoffice@furamavietnam.com"),
    ("contact.sales_hanoi", "phone", "sales_phone_1", "+84 24 3942 8858", CONTACT, "Hanoi City Office: (84-24) 3942 8858"),
    ("contact.sales_hanoi", "phone", "fax", "+84 24 3942 8532", CONTACT, "Hanoi City Office Fax: (84-24) 3942 8532"),
    ("contact.sales_hanoi", "email", "sales_office", "saleshanoi@furamavietnam.com", CONTACT, "Hanoi City Office: saleshanoi@furamavietnam.com"),
    ("contact.fb_reservation", "phone", "fnb_hotline", "+84 236 651 9999", CULINARY, "Telephone +84 236 651 9999"),
    ("contact.fb_reservation", "email", "fnb_reservation", "fb@furamavietnam.com", CULINARY, "Email fb@furamavietnam.com"),
]
for eid, ftype, ctx, value, url, evidence in contact_facts:
    upsert_fact(eid, ftype, ctx, value, value, url, evidence, locales=locale_value(value))
upsert_fact("property.furama_resort_danang", "address", "main_property", "103–105 Vo Nguyen Giap Street, Ngu Hanh Son Ward, Danang City, Vietnam", "103–105 Vo Nguyen Giap Street, Ngu Hanh Son Ward, Danang City, Vietnam", CONTACT, "Danang Office: 103 – 105 Vo Nguyen Giap Street, Ngu Hanh Son Ward, Danang City, Vietnam.")
upsert_fact("property.furama_resort_danang", "policy_rule", "check_out_time", "11:00", "11:00", CHECKOUT, "Check-out time is 11:00 noon.")
upsert_fact("property.furama_resort_danang", "policy_rule", "late_checkout_policy", "50% daily room rate until 18:00; one night after 18:00; subject to availability", "50% daily room rate until 18:00; one night after 18:00; subject to availability", CHECKOUT, "An additional 50% of the daily room rate will be charged for extended check-out until 6:00 pm. For departures after 6:00pm, one night rate will be charged. Late check-out is subject to availability.")
# Check-in is supported by an official Furama terms page; keep source temporality explicit.
upsert_fact("property.furama_resort_danang", "policy_rule", "check_in_time", "14:00", "14:00", CHECKIN, "Check-in time is from 2:00 PM, and check-out time is at 11:00 AM.", extra={"source_temporality":"official_terms_page"})

upsert_fact("contact.butler_service", "phone", "butler_hotline", "+84 911 301 020", "+84 911 301 020", BUTLER, "Butlers are available from 06:00 to Midnight; hotline 0911301020; Villa Front Desk extension 7303/7304.")
upsert_fact("contact.butler_service", "extension", "butler_desk", "7303/7304", "7303/7304", BUTLER, "Butlers are available from 06:00 to Midnight; hotline 0911301020; Villa Front Desk extension 7303/7304.")
upsert_fact("contact.butler_service", "opening_hours", "butler_availability", "06:00 - 00:00", {"start":"06:00","end":"00:00"}, BUTLER, "Our excellent butlers are available from 06:00 to Midnight for the guests staying at Villa.")

# Property inventory and electricity from current official pages.
upsert_fact("property.furama_resort_danang", "room_count", "rooms_and_suites", "198 rooms and suites", 198, HOME, "the resort features 198 elegantly appointed rooms and suites, along with 68 private pool villas", unit="rooms")
upsert_fact("property.furama_resort_danang", "villa_count", "private_pool_villas", "68 private pool villas", 68, HOME, "the resort features 198 elegantly appointed rooms and suites, along with 68 private pool villas", unit="villas")
upsert_fact("property.furama_resort_danang", "electricity_voltage", "standard_supply_voltage", "220V", 220, ELECTRICITY, "The standard supply of electricity at the resort is 220V/50 Hz.", unit="V")
upsert_fact("property.furama_resort_danang", "electricity_frequency", "standard_supply_frequency", "50 Hz", 50, ELECTRICITY, "The standard supply of electricity at the resort is 220V/50 Hz.", unit="Hz")
upsert_fact("service.adapters", "extension", "housekeeping_contact", "Ext. 13", "13", ADAPTERS, "Adapters – Ext 13")
upsert_fact("service.adapters", "service_feature", "adapter_transformer_availability", "Adapter available in room; transformers available from Housekeeping", "Adapter available in room; transformers available from Housekeeping", ADAPTERS, "An adapter is available in your room; it is placed in the drawer of your bedside table or at your desk. Transformers are available from our Housekeeping Department.")
upsert_fact("service.bath_towels", "service_feature", "additional_towels", "Additional towels available through Housekeeping", "Additional towels available through Housekeeping", BATH_TOWELS, "Should you require additional towels, please contact our Housekeeping Department.")
ensure_entity("service.room_cleaning", "service", "Room Cleaning Service", "guest_services", ROOM_CLEANING,
              vi="Dịch vụ dọn phòng", ko="객실 청소 서비스", zh="客房清洁服务")
ensure_entity("policy.pet_policy", "policy", "Pet Policy", "guest_services", PET_POLICY,
              vi="Chính sách vật nuôi", ko="반려동물 정책", zh="宠物政策")

upsert_fact("service.room_cleaning", "service_feature", "housekeeping_items", "Housekeeping can supply extra blankets, pillows, coat hangers and related items", "Housekeeping can supply extra blankets, pillows, coat hangers and related items", HOUSEKEEPING, "Our Housekeeping department will be pleased to supply you with extra blankets, pillows, coat hangers and other related items.")
_room_cleaning_hours = upsert_fact(
    "service.room_cleaning", "opening_hours", "operating_hours", "08:00 - 17:00",
    {"start":"08:00","end":"17:00"}, ROOM_CLEANING,
    "This service will be arranged daily between 8:00 am and 5:00 pm",
    locales=locale_value(
        "Room cleaning is arranged daily from 08:00 to 17:00.",
        "Dịch vụ dọn phòng được thực hiện hằng ngày từ 08:00 đến 17:00.",
        "객실 청소 서비스는 매일 08:00~17:00에 제공됩니다.",
        "客房清洁服务每日08:00至17:00提供。",
    ),
)
_room_cleaning_hours["verified_at"] = LATEST_REVIEWED_AT
_room_cleaning_hours["provenance_sources"][0]["verified_at"] = LATEST_REVIEWED_AT

_pet_policy = upsert_fact(
    "policy.pet_policy", "policy_rule", "pet_prohibition", "Pets are not permitted", "prohibited",
    PET_POLICY,
    "For the convenience of other guests and in an effort to help us provide the cleanest and most sanitary accommodations possible, pets are not permitted.",
    locales=locale_value(
        "Pets are not permitted.",
        "Không được phép mang vật nuôi vào khu nghỉ dưỡng.",
        "반려동물은 허용되지 않습니다.",
        "度假村不允许携带宠物。",
    ),
)
_pet_policy["verified_at"] = LATEST_REVIEWED_AT
_pet_policy["provenance_sources"][0]["verified_at"] = LATEST_REVIEWED_AT
entity_by_id["service.room_cleaning"]["verified_at"] = LATEST_REVIEWED_AT
entity_by_id["policy.pet_policy"]["verified_at"] = LATEST_REVIEWED_AT
upsert_fact("service.extra_bed", "service_feature", "extra_bed_request", "Extra bed in resort or folded mattress in Villa available on request; charges apply", "Extra bed in resort or folded mattress in Villa available on request; charges apply", EXTRA_BED, "Please contact the Front Desk if you require an extra bed in resort or a folded mattress in Villa. Additional charges will apply")
upsert_fact("service.luggage", "service_feature", "concierge_luggage_assistance", "Concierge assists with luggage and related services", "Concierge assists with luggage and related services", LUGGAGE, "For assistance with luggage and other related services, please contact the Concierge.")
upsert_fact("service.luggage", "service_feature", "lost_airline_luggage", "Concierge can assist with luggage lost from a flight", "Concierge can assist with luggage lost from a flight", LOST_LUGGAGE, "For any lost luggage from your flight, our team of Concierges are available for assistance.")
upsert_fact("transportation.taxi", "opening_hours", "taxi_availability", "24 hours", {"start":"00:00","end":"00:00","continuous":True}, TAXI, "Taxis are available 24 hours at the entrance of the Resort and Villa. The Concierge will be pleased to assist you with arranging for a taxi")
upsert_fact("transportation.car_rental", "service_feature", "concierge_arrangement", "Car rental can be arranged through Concierge in the resort lobby", "Car rental can be arranged through Concierge in the resort lobby", CAR_RENTAL, "Please contact with Concierge located at the resort lobby for this service")
upsert_fact("transportation.airport_transfer", "service_feature", "concierge_arrangement", "Concierge can arrange airport transport", "Concierge can arrange airport transport", AIRPORT_TRANSFER, "Our Concierge will be pleased to arrange your transport to the airport.")
upsert_fact("transportation.airport_transfer", "policy_rule", "early_departure_booking", "For early-morning departure, book before 21:00 the day before", "For early-morning departure, book before 21:00 the day before", AIRPORT_TRANSFER, "For early morning departure, please book the transfer before 9:00p.m. on the day before departure.")
upsert_fact("service.lost_and_found", "service_feature", "housekeeping_assistance", "Contact Housekeeping for Lost & Found assistance", "Contact Housekeeping for Lost & Found assistance", LOST_FOUND, "Please contact our Housekeeping Department for assistance.")
upsert_fact("service.first_aid", "service_feature", "operator_contact", "Contact the Operator for first aid", "Contact the Operator for first aid", FIRST_AID, "For first aid, please contact the Operator")
upsert_fact("service.wake_up_calls", "service_feature", "operator_request", "Contact the Telephone Operator for an early-morning wake-up call", "Contact the Telephone Operator for an early-morning wake-up call", WAKE_UP, "Please contact the Telephone Operator for your early morning call.")
upsert_fact("service.currency_exchange", "service_feature", "front_desk_exchange", "Foreign currency exchange at Front Desk at normal bank rates", "Foreign currency exchange at Front Desk at normal bank rates", CURRENCY_EXCHANGE, "Foreign currencies can be exchanged at Front Desk at the normal Bank rates")
upsert_fact("service.courier", "service_feature", "business_centre_arrangement", "Courier service can be arranged through the Business Centre in the Resort Lobby", "Courier service can be arranged through the Business Centre in the Resort Lobby", COURIER, "This service can be arranged through our Business Centre in the Resort Lobby.")
upsert_fact("service.flight_reconfirmation", "service_feature", "business_centre_assistance", "Flight information/reconfirmation/change assistance is through the Business Centre in the Resort Lobby", "Flight information/reconfirmation/change assistance is through the Business Centre in the Resort Lobby", FLIGHT_INFO, "Please contact the Business Centre at the resort lobby")
upsert_fact("dining.restaurant_reservation", "opening_hours", "room_service_reservation_desk", "06:00 - 00:00", {"start":"06:00","end":"00:00"}, RESTAURANTS_BARS, "For restaurant & bar reservation please call room service between 6:00am – 0:00am at Ext. 12")
upsert_fact("dining.restaurant_reservation", "extension", "room_service_contact", "Ext. 12", "12", RESTAURANTS_BARS, "For restaurant & bar reservation please call room service between 6:00am – 0:00am at Ext. 12")

# ---------------------------------------------------------------------------
# 6. Golf: split catch-all extractor rows into specific course entities.
# ---------------------------------------------------------------------------
golf_rows = [
    ("golf.montgomerie_links", "Montgomerie Links", "+84 235 394 1942", 10, "Sơn Trà, Dien Duong, Quang Nam province"),
    ("golf.danang_golf_club", "Danang Golf Club", "+84 236 395 8112", 10, "Son Tra, Dien Ngoc, Da Nang"),
    ("golf.laguna_golf_club", "Laguna Golf Club", "+84 234 369 5800", 80, "Phu Loc District"),
    ("golf.ba_na_hills_golf_club", "Ba Na Hills Golf Club", "+84 236 392 4888", 45, "An Son, Hoa Ninh, Hoa Vang, Danang"),
]
for eid, name, phone, mins, address in golf_rows:
    ensure_entity(eid, "golf_course", name, "recreation", GOLF, vi=name, ko=name, zh=name)
    ev = f"{name}: Address {address}; Telephone {phone}; Distance from hotel: Around {mins} minutes"
    upsert_fact(eid, "phone", "contact", phone, phone, GOLF, ev)
    upsert_fact(eid, "travel_time_min", "from_resort_approx", f"Around {mins} minutes", mins, GOLF, ev, unit="minutes")
    upsert_fact(eid, "address", "course_address", address, address, GOLF, ev)
# Any still-approved operational facts on catch-all recreation are unsafe.
quarantine_where(lambda f: f["entity_id"] == "recreation.resort_activities" and f.get("fact_type") in {"opening_hours","phone","distance_km"}, "needs_reclassification", "Split into specific recreation/golf entities during the current curation review.")

# ---------------------------------------------------------------------------
# 7. Meeting facts: repair exact rows from official Furama capacity PDF.
# ---------------------------------------------------------------------------
# Replace broken Boardroom/Gallery rows.
quarantine_where(lambda f: f["entity_id"] in {"meeting.boardroom","meeting.gallery"} and f.get("fact_type") in {"area_sqm","height_m","capacity"}, "needs_reverification", "Superseded by official Furama ICP capacity-chart verification.")
meeting_evidence = "Official Furama ICP Capacity Chart: Danang Grand Ballroom 774 sqm; Ocean Ballroom 363 sqm; Gallery Room 1&2 / 3&4 180 sqm, 18x10x3.3, banquet 90, theatre 120, classroom 80, U-shape 45, cocktail 120; Board Room (BC) 31 sqm, 8.2x4x3.3, Board Room 20."
for kind, ctx, raw, value, unit in [
    ("area_sqm","floor_area","31 sqm",31.0,"sqm"),
    ("height_m","ceiling_height","3.3 m",3.3,"m"),
    ("capacity","boardroom","20",20,"people"),
]:
    upsert_fact("meeting.boardroom", kind, ctx, raw, value, MEETING_PDF, meeting_evidence, unit=unit)
for kind, ctx, raw, value, unit in [
    ("area_sqm","floor_area","180 sqm",180.0,"sqm"),
    ("height_m","ceiling_height","3.3 m",3.3,"m"),
    ("capacity","banquet","90",90,"people"),
    ("capacity","theatre","120",120,"people"),
    ("capacity","classroom","80",80,"people"),
    ("capacity","u_shape","45",45,"people"),
    ("capacity","cocktail","120",120,"people"),
]:
    upsert_fact("meeting.gallery", kind, ctx, raw, value, MEETING_PDF, meeting_evidence, unit=unit)
# Parent ICP max public capacity is the maximum supported by Grand Ballroom.
upsert_fact("meeting.international_convention_palace", "capacity", "max_occupancy", "Up to 1,000 guests", 1000, MEETING_PDF,
            "Danang Grand Ballroom: Theatre 1000 and Cocktail 1000 in the official Furama ICP Capacity Chart.", unit="people")
# Refresh core room rows to guard against stale parser values.
meeting_core = {
    "meeting.danang_grand_ballroom": (774.0, {"banquet":500,"theatre":1000,"classroom":500,"u_shape":120,"cocktail":1000}),
    "meeting.ballroom_1": (258.0, {"banquet":130,"theatre":250,"classroom":120,"u_shape":70,"cocktail":250,"boardroom":60}),
    "meeting.ballroom_2": (258.0, {"banquet":130,"theatre":250,"classroom":120,"u_shape":70,"cocktail":250,"boardroom":60}),
    "meeting.ballroom_3": (258.0, {"banquet":130,"theatre":250,"classroom":120,"u_shape":70,"cocktail":250,"boardroom":60}),
    "meeting.han_river_1": (39.0, {"banquet":20,"theatre":30,"classroom":20,"u_shape":15,"cocktail":30,"boardroom":20}),
    "meeting.han_river_2": (41.0, {"banquet":20,"theatre":30,"classroom":20,"u_shape":15,"cocktail":30,"boardroom":20}),
    "meeting.son_tra_1": (62.0, {"banquet":40,"theatre":60,"classroom":36,"u_shape":24,"cocktail":60,"boardroom":30}),
    "meeting.ocean_ballroom": (363.0, {"banquet":200,"theatre":300,"classroom":160,"u_shape":100,"cocktail":250,"boardroom":70}),
}
for eid, (area, caps) in meeting_core.items():
    quarantine_where(lambda f, eid=eid: f["entity_id"] == eid and f.get("fact_type") in {"area_sqm","capacity"}, "needs_reverification", "Superseded by the current curation review official Furama ICP capacity-chart verification.")
    upsert_fact(eid, "area_sqm", "floor_area", f"{area:g} sqm", area, MEETING_PDF, meeting_evidence, unit="sqm")
    for ctx, cap in caps.items():
        upsert_fact(eid, "capacity", ctx, str(cap), cap, MEETING_PDF, meeting_evidence, unit="people")

# Publish exact ceiling heights for rooms whose height is present in the official chart.
for eid, height in {
    "meeting.danang_grand_ballroom":5.5, "meeting.ballroom_1":5.5, "meeting.ballroom_2":5.5,
    "meeting.ballroom_3":5.5, "meeting.han_river_1":2.5, "meeting.han_river_2":2.5,
    "meeting.son_tra_1":2.5, "meeting.ocean_ballroom":3.0, "meeting.gallery":3.3, "meeting.boardroom":3.3,
}.items():
    upsert_fact(eid, "height_m", "ceiling_height", f"{height:g} m", height, MEETING_PDF, meeting_evidence, unit="m")

# Add official chart venues that were missing from the canonical dataset.
meeting_additions = {
    "meeting.ballroom_1_2": ("Danang Ballroom 1&2", 516.0, {"banquet":350,"theatre":700,"classroom":300,"u_shape_single":100,"u_shape_double_triple":170,"cocktail":700,"hollow_square_single":120,"hollow_square_double_triple":200}, 5.5),
    "meeting.ballroom_2_3": ("Danang Ballroom 2&3", 516.0, {"banquet":350,"theatre":700,"classroom":300,"u_shape_single":100,"u_shape_double_triple":170,"cocktail":700,"hollow_square_single":120,"hollow_square_double_triple":200}, 5.5),
    "meeting.danang_ballroom_foyer": ("Danang Ballroom Foyer", 294.0, {"banquet":100,"cocktail":200}, 5.7),
    "meeting.non_nuoc_suite": ("Non Nuoc Suite (I&II)", 106.0, {"banquet":60,"theatre":80,"classroom":60,"u_shape_single":36,"cocktail":80,"hollow_square_single":42,"boardroom":34}, 2.6),
    "meeting.non_nuoc_1": ("Non Nuoc Room 1", 53.0, {"banquet":25,"theatre":40,"classroom":25,"u_shape_single":18,"cocktail":40,"hollow_square_single":20,"boardroom":20}, 2.6),
    "meeting.non_nuoc_2": ("Non Nuoc Room 2", 53.0, {"banquet":25,"theatre":40,"classroom":25,"u_shape_single":18,"cocktail":40,"hollow_square_single":20,"boardroom":20}, 2.6),
    "meeting.icp_garden": ("ICP Garden", 516.0, {"banquet":120,"cocktail":200}, None),
    "meeting.ocean_ballroom_terrace": ("Ocean Ballroom Terrace", 199.0, {"banquet":120,"cocktail":200}, None),
    "meeting.gallery_individual": ("Gallery Room 1, 2, 3 or 4", 90.0, {"banquet":40,"theatre":60,"classroom":40,"u_shape_single":20,"cocktail":60,"hollow_square_single":25,"boardroom":20}, 3.3),
    "meeting.beach_event_space": ("Beach Event Space", 4266.0, {"banquet":3000,"cocktail":4000}, None),
}
for eid, (name, area, caps, height) in meeting_additions.items():
    ensure_entity(eid, "meeting_room", name, "meetings", MEETING_PDF, vi=name, ko=name, zh=name)
    upsert_fact(eid, "area_sqm", "floor_area", f"{area:g} sqm", area, MEETING_PDF, meeting_evidence, unit="sqm")
    if height is not None:
        upsert_fact(eid, "height_m", "ceiling_height", f"{height:g} m", height, MEETING_PDF, meeting_evidence, unit="m")
    for ctx, cap in caps.items():
        upsert_fact(eid, "capacity", ctx, str(cap), cap, MEETING_PDF, meeting_evidence, unit="people")

# Complete missing capacity columns for existing rooms when the chart provides them.
meeting_extra_caps = {
    "meeting.danang_grand_ballroom":{"u_shape_double_triple":250,"hollow_square_single":150,"hollow_square_double_triple":320},
    "meeting.ballroom_1":{"u_shape_double_triple":100,"hollow_square_single":80,"hollow_square_double_triple":120},
    "meeting.ballroom_2":{"u_shape_double_triple":100,"hollow_square_single":80,"hollow_square_double_triple":120},
    "meeting.ballroom_3":{"u_shape_double_triple":100,"hollow_square_single":80,"hollow_square_double_triple":120},
    "meeting.han_river_1":{"hollow_square_single":18},
    "meeting.han_river_2":{"hollow_square_single":18},
    "meeting.son_tra_1":{"hollow_square_single":30},
    "meeting.ocean_ballroom":{"u_shape_double_triple":120,"hollow_square_single":110,"hollow_square_double_triple":150},
    "meeting.gallery":{"u_shape_double_triple":72,"hollow_square_single":50,"hollow_square_double_triple":80,"boardroom":40},
}
for eid, caps in meeting_extra_caps.items():
    for ctx, cap in caps.items():
        upsert_fact(eid, "capacity", ctx, str(cap), cap, MEETING_PDF, meeting_evidence, unit="people")

# ---------------------------------------------------------------------------
# 8. Alias coverage and minimal human-review docs for new runtime entities.
# ---------------------------------------------------------------------------
alias_payload = json.loads(ALIASES.read_text(encoding="utf-8"))
alias_map = alias_payload.setdefault("aliases_by_entity", {})
semantic_aliases = {
    "restaurant.don_cipriani": {
        "en": ["italian cuisine", "italian food", "pasta", "pizza"],
        "vi": ["ẩm thực ý", "món ý", "pasta", "pizza"],
        "ko": ["이탈리아 요리", "파스타", "피자"],
        "zh": ["意大利菜", "意大利餐厅", "意面", "披萨"],
    },
    "service.in_room_dining": {
        "en": ["room service", "food delivery", "in room dining", "late night food"],
        "vi": ["room service", "giao đồ ăn tận phòng", "đồ ăn khuya", "ăn uống tại phòng"],
        "ko": ["룸서비스", "객실 음식 배달", "야식", "인룸 다이닝"],
        "zh": ["客房服务", "送餐到房", "夜宵", "客房送餐"],
    },
    "recreation.fitness_center": {
        "en": ["gym", "fitness centre", "fitness center", "weights", "cardio"],
        "vi": ["phòng gym", "phòng tập", "tạ", "thiết bị tim mạch"],
        "ko": ["헬스장", "피트니스", "웨이트", "유산소"],
        "zh": ["健身房", "健身中心", "自由重量", "有氧设备"],
    },
    "property.furama_resort_danang": {
        "en": ["wifi", "wi-fi", "minibar", "late checkout", "check out late", "room amenities"],
        "vi": ["wifi", "wi-fi", "minibar", "trả phòng muộn", "tiện nghi phòng"],
        "ko": ["와이파이", "미니바", "레이트 체크아웃", "객실 편의시설"],
        "zh": ["无线网络", "迷你吧", "延迟退房", "客房设施"],
    },
    "service.housekeeping": {
        "en": ["housekeeping", "extra blanket", "extra pillow", "coat hanger"],
        "vi": ["buồng phòng", "chăn thêm", "gối thêm", "móc áo"],
        "ko": ["하우스키핑", "추가 담요", "추가 베개", "옷걸이"],
        "zh": ["客房服务", "额外毛毯", "额外枕头", "衣架"],
    },
    "transportation.airport_transfer": {
        "en": ["airport transfer", "airport car", "ride to airport"],
        "vi": ["đưa đón sân bay", "xe sân bay", "xe ra sân bay"],
        "ko": ["공항 이동", "공항 차량"],
        "zh": ["机场接送", "机场用车"],
    },
    "recreation.kids_club": {
        "en": ["kids activities", "children activities", "play area", "kids club"],
        "vi": ["hoạt động trẻ em", "khu vui chơi trẻ em", "câu lạc bộ trẻ em"],
        "ko": ["어린이 활동", "키즈 놀이", "키즈 클럽"],
        "zh": ["儿童活动", "儿童游乐", "儿童俱乐部"],
    },
}
for entity_id, by_language in semantic_aliases.items():
    target = alias_map.setdefault(entity_id, {})
    for language, values in by_language.items():
        current = target.setdefault(language, [])
        seen = {str(item).casefold() for item in current}
        for value in values:
            if value.casefold() not in seen:
                current.append(value)
                seen.add(value.casefold())
for e in entities:
    eid = e["entity_id"]
    if eid not in alias_map:
        names = e.get("names_by_locale") or {}
        alias_map[eid] = {lang: [str(names.get(lang) or e.get("name") or eid).casefold()] for lang in ("en","vi","ko","zh")}
ALIASES.write_text(json.dumps(alias_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

existing_doc_entities: set[str] = set()
for p in KNOWLEDGE.rglob("*.md"):
    text = p.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("entity_id:"):
            existing_doc_entities.add(line.split(":",1)[1].strip().strip('"\'')); break
for e in entities:
    eid = e["entity_id"]
    if eid in existing_doc_entities:
        continue
    filename = "kb_" + eid.replace(".", "_") + ".md"
    front = [
        "---", f"property_id: {PROPERTY}", f"document_id: kb_{eid.replace('.', '_')}", f"entity_id: {eid}",
        f"domain: {e.get('domain','general')}", "languages: [en, vi, ko, zh]", "source_document_ids: []", "source_fact_ids: []", "---",
        f"# {e.get('name',eid)}", "", "## Key Specifications & Facts", "", "## Localizations", "",
        f"- EN: {e.get('names_by_locale',{}).get('en',e.get('name',eid))}",
        f"- VI: {e.get('names_by_locale',{}).get('vi',e.get('name',eid))}",
        f"- KO: {e.get('names_by_locale',{}).get('ko',e.get('name',eid))}",
        f"- ZH: {e.get('names_by_locale',{}).get('zh',e.get('name',eid))}", "",
    ]
    (KNOWLEDGE / filename).write_text("\n".join(front), encoding="utf-8")

# ---------------------------------------------------------------------------
# 8b. Locale quality gate. Facts remain sourced from official Furama evidence;
# these guest-facing renderings are LLM-assisted translations reviewed in this
# curation pass, not quotations from the source website.
# ---------------------------------------------------------------------------
LOCALIZED_TEXT = {
    "50% daily room rate until 18:00; one night after 18:00; subject to availability": {
        "vi":"Tính 50% giá phòng ngày đến 18:00; sau 18:00 tính một đêm; tùy tình trạng phòng.",
        "ko":"18:00까지는 1일 객실 요금의 50%, 18:00 이후에는 1박 요금이 적용되며 객실 상황에 따라 이용 가능합니다.",
        "zh":"延迟至18:00前收取当日房价的50%；18:00后收取一晚房费；视房态而定。"},
    "Night swimming is not permitted": {"vi":"Không được phép bơi biển vào ban đêm.","ko":"야간 해수욕은 허용되지 않습니다.","zh":"禁止夜间海泳。"},
    "Subject to availability": {"vi":"Tùy tình trạng sẵn có.","ko":"이용 가능 여부에 따라 제공됩니다.","zh":"视供应情况而定。"},
    "Pools & beach access, sun-chair, umbrella, towels, games room": {"vi":"Sử dụng hồ bơi và bãi biển, ghế nằm, ô che, khăn và phòng trò chơi.","ko":"수영장·해변 이용, 선베드, 파라솔, 타월, 게임룸 포함.","zh":"包含泳池和海滩使用、躺椅、遮阳伞、毛巾及游戏室。"},
    "Books in English, German, French, Dutch and Japanese are available free of charge": {"vi":"Có sách tiếng Anh, Đức, Pháp, Hà Lan và Nhật để đọc miễn phí.","ko":"영어, 독일어, 프랑스어, 네덜란드어, 일본어 도서를 무료로 이용할 수 있습니다.","zh":"可免费阅读英语、德语、法语、荷兰语和日语书籍。"},
    "1 King + 2 Single": {"vi":"1 giường King + 2 giường đơn","ko":"킹 침대 1개 + 싱글 침대 2개","zh":"1张特大床 + 2张单人床"},
    "8 guests or 4 Adults & 4 Children": {"vi":"Tối đa 8 khách hoặc 4 người lớn và 4 trẻ em","ko":"최대 8명 또는 성인 4명 + 어린이 4명","zh":"最多8位客人，或4位成人+4位儿童"},
    "King or Twin": {"vi":"Giường King hoặc Twin","ko":"킹 또는 트윈 침대","zh":"特大床或双床"},
    "3 Adults or 2 Adults & 2 Children": {"vi":"Tối đa 3 người lớn hoặc 2 người lớn và 2 trẻ em","ko":"성인 3명 또는 성인 2명 + 어린이 2명","zh":"最多3位成人，或2位成人+2位儿童"},
    "King": {"vi":"Giường King","ko":"킹 침대","zh":"特大床"},
    "Adapter available in room; transformers available from Housekeeping": {"vi":"Có bộ chuyển đổi ổ cắm trong phòng; có thể yêu cầu biến áp từ Housekeeping.","ko":"객실에 어댑터가 비치되어 있으며 변압기는 하우스키핑에 요청할 수 있습니다.","zh":"客房内提供转换插头；变压器可向客房服务部申请。"},
    "Book one day in advance; Kids Club page separately says at least 2-4 hours. Confirm with Tour Desk/Kids Club.": {"vi":"Nên đặt trước một ngày; trang Kids Club lại ghi tối thiểu 2–4 giờ. Hãy xác nhận với Tour Desk/Kids Club.","ko":"전용 베이비시팅 페이지는 하루 전 예약을 안내하지만 키즈클럽 페이지는 최소 2–4시간 전이라고 안내합니다. 투어 데스크/키즈클럽에 확인하세요.","zh":"专门的保姆服务页面要求提前一天预订，而儿童俱乐部页面另称至少提前2–4小时。请向旅游服务台/儿童俱乐部确认。"},
    "Additional towels available through Housekeeping": {"vi":"Có thể yêu cầu thêm khăn qua Housekeeping.","ko":"추가 타월은 하우스키핑에 요청할 수 있습니다.","zh":"可向客房服务部申请额外毛巾。"},
    "Courier service can be arranged through the Business Centre in the Resort Lobby": {"vi":"Có thể sắp xếp dịch vụ chuyển phát qua Business Centre tại sảnh Resort.","ko":"리조트 로비의 비즈니스 센터를 통해 택배 서비스를 요청할 수 있습니다.","zh":"可通过度假村大堂的商务中心安排快递服务。"},
    "Foreign currency exchange at Front Desk at normal bank rates": {"vi":"Có thể đổi ngoại tệ tại Front Desk theo tỷ giá ngân hàng thông thường.","ko":"프런트 데스크에서 일반 은행 환율로 외화를 환전할 수 있습니다.","zh":"可在前台按一般银行汇率兑换外币。"},
    "Extra bed in resort or folded mattress in Villa available on request; charges apply": {"vi":"Có thể yêu cầu giường phụ tại Resort hoặc nệm gấp tại Villa; có tính phí.","ko":"리조트에서는 엑스트라 베드, 빌라에서는 접이식 매트리스를 요청할 수 있으며 추가 요금이 부과됩니다.","zh":"度假村可申请加床，别墅可申请折叠床垫；需额外收费。"},
    "Contact the Operator for first aid": {"vi":"Liên hệ Tổng đài để được hỗ trợ sơ cấp cứu.","ko":"응급 처치가 필요하면 교환원에게 연락하세요.","zh":"如需急救，请联系总机。"},
    "Flight information/reconfirmation/change assistance is through the Business Centre in the Resort Lobby": {"vi":"Hỗ trợ thông tin, xác nhận lại hoặc thay đổi chuyến bay qua Business Centre tại sảnh Resort.","ko":"항공편 정보, 재확인 및 변경 지원은 리조트 로비의 비즈니스 센터에서 제공합니다.","zh":"航班信息、再确认及变更协助由度假村大堂商务中心提供。"},
    "Outside food/drinks and cooking in-room are not allowed for safety and hygiene reasons": {"vi":"Không được mang đồ ăn, thức uống từ bên ngoài vào Resort hoặc nấu ăn trong phòng vì lý do an toàn và vệ sinh.","ko":"안전과 위생을 위해 외부 음식·음료 반입 및 객실 내 조리는 허용되지 않습니다.","zh":"出于安全和卫生原因，不允许携带外部食品饮料进入度假村，也不允许在客房内烹饪。"},
    "Contact Housekeeping for Lost & Found assistance": {"vi":"Liên hệ Housekeeping để được hỗ trợ đồ thất lạc.","ko":"분실물 지원은 하우스키핑에 문의하세요.","zh":"失物招领请联系客房服务部。"},
    "Concierge assists with luggage and related services": {"vi":"Concierge hỗ trợ hành lý và các dịch vụ liên quan.","ko":"컨시어지가 수하물 및 관련 서비스를 지원합니다.","zh":"礼宾部可协助处理行李及相关服务。"},
    "Concierge can assist with luggage lost from a flight": {"vi":"Concierge có thể hỗ trợ trường hợp hành lý thất lạc từ chuyến bay.","ko":"항공편에서 분실된 수하물 관련 지원을 컨시어지에 요청할 수 있습니다.","zh":"礼宾部可协助处理航班托运行李遗失事宜。"},
    "Except Saturday afternoon and Sunday": {"vi":"Trừ chiều thứ Bảy và Chủ Nhật.","ko":"토요일 오후와 일요일 제외.","zh":"周六下午及周日除外。"},
    "Housekeeping can supply extra blankets, pillows, coat hangers and related items": {"vi":"Housekeeping có thể cung cấp thêm chăn, gối, móc áo và các vật dụng liên quan.","ko":"하우스키핑에서 추가 담요, 베개, 옷걸이 및 관련 물품을 제공할 수 있습니다.","zh":"客房服务部可提供额外毛毯、枕头、衣架及相关用品。"},
    "Contact the Telephone Operator for an early-morning wake-up call": {"vi":"Liên hệ Tổng đài điện thoại để yêu cầu báo thức sáng sớm.","ko":"이른 아침 모닝콜은 전화 교환원에게 요청하세요.","zh":"如需清晨叫醒服务，请联系电话总机。"},
    "Schedule treatments 24 hours in advance": {"vi":"Nên đặt liệu trình trước 24 giờ.","ko":"트리트먼트는 24시간 전에 예약하는 것이 권장됩니다.","zh":"建议提前24小时预约护理。"},
    "Last appointment 20:30": {"vi":"Nhận lịch cuối lúc 20:30.","ko":"마지막 예약 접수는 20:30입니다.","zh":"最晚预约时间为20:30。"},
    "For early-morning departure, book before 21:00 the day before": {"vi":"Nếu khởi hành sáng sớm, hãy đặt trước 21:00 của ngày hôm trước.","ko":"이른 아침 출발은 전날 21:00 이전에 예약하세요.","zh":"如清晨出发，请在前一天21:00前预订。"},
    "Concierge can arrange airport transport": {"vi":"Concierge có thể sắp xếp phương tiện đưa đón sân bay.","ko":"컨시어지가 공항 교통편을 준비할 수 있습니다.","zh":"礼宾部可安排机场交通。"},
    "Car rental can be arranged through Concierge in the resort lobby": {"vi":"Có thể sắp xếp thuê xe qua Concierge tại sảnh Resort.","ko":"리조트 로비의 컨시어지를 통해 차량 대여를 요청할 수 있습니다.","zh":"可通过度假村大堂礼宾部安排租车。"},
}
for fact in facts:
    translated = LOCALIZED_TEXT.get(str(fact.get("raw_value", "")))
    if translated and fact.get("publication_status", "approved") == "approved":
        fact["locale_support"] = {"en":str(fact.get("raw_value", "")), **translated}
        fact["localization_method"] = "llm_translation_reviewed"
        fact["localization_reviewed_at"] = VERIFIED_AT

# ---------------------------------------------------------------------------
# 9. Final trust boundary: anything still active but not manually web-reviewed
# in this curation pass leaves the runtime ledger. It remains in quarantine so
# provenance/history is preserved without contaminating guest answers.
# ---------------------------------------------------------------------------
for f in facts:
    if f.get("publication_status", "approved") == "approved" and f.get("curation_method") != "official_web_manual_review":
        f["publication_status"] = "needs_reverification"
        f["curation_reason"] = "Not re-verified against a specific official Furama source in the current curation review; archived from runtime until reviewed."
        f["curated_at"] = VERIFIED_AT

# ---------------------------------------------------------------------------
# 10. Relations: purge edges to non-approved fact ledger rows; regenerate fact edges.
# ---------------------------------------------------------------------------
approved_facts = {f["canonical_fact_id"]: f for f in facts if f.get("publication_status", "approved") == "approved"}
all_fact_ids = set(fact_by_id) | {f["canonical_fact_id"] for f in facts}
relations = load_jsonl(RELATIONS)
filtered: list[dict[str, Any]] = []
for r in relations:
    src, dst = r.get("source_id"), r.get("target_id")
    if (src in all_fact_ids and src not in approved_facts) or (dst in all_fact_ids and dst not in approved_facts):
        continue
    # Fact-derived edges are regenerated below to avoid stale values/evidence.
    if src in entity_by_id and dst in all_fact_ids and r.get("relation_type") in {"has_opening_hours","has_floor_area","has_capacity_limit","offers_menu_price"}:
        continue
    filtered.append(r)
relation_type = {"opening_hours":"has_opening_hours", "area_sqm":"has_floor_area", "balcony_area_sqm":"has_floor_area", "capacity":"has_capacity_limit", "price_vnd":"offers_menu_price"}
for f in approved_facts.values():
    rt = relation_type.get(f.get("fact_type"))
    if not rt:
        continue
    ev = (f.get("provenance_sources") or [{}])[0].get("evidence", "")
    row = {"source_id": f["entity_id"], "relation_type": rt, "target_id": f["canonical_fact_id"], "value": f.get("normalized_value"), "source_type": "factual", "evidence": ev}
    if f.get("unit"):
        row["unit"] = f["unit"]
    if f.get("context"):
        row["context"] = f["context"]
    filtered.append(row)
# stable de-dup
seen, dedup = set(), []
for r in filtered:
    key = (r.get("source_id"), r.get("relation_type"), r.get("target_id"), json.dumps(r.get("value"), sort_keys=True, ensure_ascii=False))
    if key in seen:
        continue
    seen.add(key); dedup.append(r)

# ---------------------------------------------------------------------------
# 11. Planning and contacts: only publish current factual windows.
# ---------------------------------------------------------------------------
    planning = json.loads(dataset_path(PLANNING_RELATIVE, ROOT / "datasets").read_text(encoding="utf-8"))
planning["version"] = "1.2.0"
planning["operating_schedule"] = [
    {"item":"check_in_time","hours":"14:00","source_type":"factual","provenance":"property.furama_resort_danang"},
    {"item":"check_out_time","hours":"11:00","source_type":"factual","provenance":"property.furama_resort_danang"},
    {"item":"cafe_indochine_breakfast","hours":"06:30 - 10:30","source_type":"factual","provenance":"restaurant.cafe_indochine"},
    {"item":"cafe_indochine_a_la_carte_lunch","hours":"11:30 - 14:00","source_type":"factual","provenance":"restaurant.cafe_indochine"},
    {"item":"cafe_indochine_a_la_carte_dinner","hours":"18:00 - 22:00","source_type":"factual","provenance":"restaurant.cafe_indochine"},
    {"item":"in_room_dining","hours":"06:30 - 00:00","source_type":"factual","provenance":"service.in_room_dining"},
    {"item":"don_cipriani","hours":"11:30 - 14:00 & 18:00 - 22:00","source_type":"factual","provenance":"restaurant.don_cipriani"},
    {"item":"danaksara","hours":"06:30 - 10:30 & 18:00 - 22:00","source_type":"factual","provenance":"restaurant.danaksara"},
    {"item":"the_fan_steakhouse","hours":"18:00 - 22:00","source_type":"factual","provenance":"restaurant.the_fan_steakhouse"},
    {"item":"taya_house","hours":"18:00 - 22:00","source_type":"factual","provenance":"restaurant.taya_house"},
    {"item":"hai_van_lounge","hours":"08:00 - 00:00","source_type":"factual","provenance":"bar.hai_van_lounge"},
    {"item":"v_senses_cafe","hours":"06:30 - 21:00","source_type":"factual","provenance":"restaurant.v_senses_cafe"},
    {"item":"ocean_terrace","hours":"10:00 - 21:00","source_type":"factual","provenance":"bar.ocean_terrace"},
    {"item":"lamuse_gourmet_cafe","hours":"09:00 - 21:00","source_type":"factual","provenance":"bar.lamuse_gourmet_cafe"},
    {"item":"lagoon_bar","hours":"10:00 - 18:00","source_type":"factual","provenance":"bar.lagoon_bar"},
    {"item":"v_senses_spa","hours":"09:00 - 22:00","source_type":"factual","provenance":"spa.v_senses_wellness"},
    {"item":"fitness_center","hours":"06:00 - 21:00","source_type":"factual","provenance":"recreation.fitness_center"},
    {"item":"kids_club_regular","hours":"08:30 - 18:00","source_type":"factual","provenance":"recreation.kids_club"},
    {"item":"kids_club_fri_sat","hours":"08:30 - 21:00","source_type":"factual","provenance":"recreation.kids_club"},
    {"item":"medical_centre","hours":"08:00 - 17:00 except Saturday afternoon and Sunday","source_type":"factual","provenance":"service.medical_centre"},
    {"item":"beach_lifeguards","hours":"06:00 - 18:00","source_type":"factual","provenance":"recreation.beach"},
]
# Preserve only operational-config SLAs; replace factual lead-times.
operational = [x for x in planning.get("lead_times_and_slas", []) if x.get("source_type") == "operational_config"]
for x in operational:
    if x.get("service") == "in_room_dining":
        x["dispatch_hours"] = "06:30 - 00:00"
planning["lead_times_and_slas"] = [
    {"service":"babysitting_notice","min_value":24,"max_value":24,"unit":"hours","source_type":"factual","provenance":"service.babysitting","note":"Dedicated babysitting service page requires booking one day in advance."},
    *operational,
]
dataset_path(PLANNING_RELATIVE, ROOT / "datasets").write_text(json.dumps(planning, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

contacts = json.loads(dataset_path(CONTACTS_RELATIVE, ROOT / "datasets").read_text(encoding="utf-8"))
for c in contacts:
    cid = c.get("contact_id")
    if cid == "contact.danang_office":
        c["phones"] = ["+84 236 3847 333", "+84 236 3847 888"]
        c["fax"] = "+84 236 3847 666"
        c["email"] = "reservation@furamavietnam.com"
        c.pop("internal_extensions", None)
        c["address"] = "103 - 105 Vo Nguyen Giap Street, Ngu Hanh Son Ward, Danang City, Vietnam"
    elif cid == "contact.fb_reservation":
        c["phones"] = ["+84 236 651 9999"]
        c["email"] = "fb@furamavietnam.com"
        c.pop("internal_extensions", None)  # current public culinary page does not publish Ext 12
    elif cid == "contact.medical_centre":
        c.pop("phones", None)  # avoid presenting the resort main switchboard as a direct medical phone
        c["internal_extensions"] = ["3420", "0"]
        c["languages"] = {
            "en":"Resort Medical Centre / in-room consultation; Ext 3420 or Operator 0; staffed 08:00–17:00 except Saturday afternoon and Sunday.",
            "vi":"Trung tâm Y tế / tư vấn tại phòng; máy lẻ 3420 hoặc Tổng đài 0; phục vụ 08:00–17:00, trừ chiều thứ Bảy và Chủ Nhật.",
            "ko":"리조트 의료센터/객실 상담; 내선 3420 또는 교환원 0; 토요일 오후와 일요일을 제외하고 08:00–17:00 운영.",
            "zh":"度假村医疗中心/客房问诊；分机3420或总机0；除周六下午和周日外，08:00–17:00值班。",
        }
    elif cid == "contact.butler_service":
        c["phones"] = ["+84 911 301 020"]
        c["internal_extensions"] = ["7303", "7304"]
        c["languages"] = {
            "en":"Villa butler service is available 06:00–midnight; hotline +84 911 301 020 or Villa Front Desk Ext 7303/7304.",
            "vi":"Dịch vụ quản gia biệt thự phục vụ 06:00–nửa đêm; hotline +84 911 301 020 hoặc Lễ tân Villa máy lẻ 7303/7304.",
            "ko":"빌라 버틀러 서비스는 06:00부터 자정까지 이용 가능하며, +84 911 301 020 또는 빌라 프런트 내선 7303/7304로 연락할 수 있습니다.",
            "zh":"别墅管家服务时间为06:00至午夜；可拨打+84 911 301 020或别墅前台分机7303/7304。",
        }
    elif cid == "contact.sales_hcm":
        c["phones"] = ["+84 28 3821 1888"]
        c["fax"] = "+84 28 3821 3246"
        c["address"] = "Suite 5D, Floor 10 Opera View Building, 161 Dong Khoi Street, District 1, HCMC"
        c["email"] = "salesoffice@furamavietnam.com"
    elif cid == "contact.sales_hanoi":
        c["phones"] = ["+84 24 3942 8858"]
        c["fax"] = "+84 24 3942 8532"
        c["address"] = "Suite 1403, 14th Floor, 109 Tran Hung Dao St., Hoan Kiem, Hanoi"
        c["email"] = "saleshanoi@furamavietnam.com"
    c["verified_at"] = VERIFIED_AT
dataset_path(CONTACTS_RELATIVE, ROOT / "datasets").write_text(json.dumps(contacts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Canonical property profile: remove ambiguous/unverified fields and retain only web-backed inventory.
profile_path = dataset_path("knowledge/canonical/property_profile.json", ROOT / "datasets")
profile = json.loads(profile_path.read_text(encoding="utf-8"))
profile["address"] = {"street":"103 - 105 Vo Nguyen Giap Street", "ward":"Ngu Hanh Son Ward", "city":"Danang City", "country":"Vietnam"}
profile["address"].pop("postal_code", None)
profile["check_in_time"] = "14:00"
profile["check_out_time"] = "11:00"
profile["contact"] = {
    "main_phone": "+84 236 3847 333",
    "secondary_phone": "+84 236 3847 888",
    "fax": "+84 236 3847 666",
    "butler_hotline": "+84 911 301 020",
    "general_email": "reservation@furamavietnam.com",
    "fnb_phone": "+84 236 651 9999",
    "fnb_email": "fb@furamavietnam.com",
}
profile["electricity"] = {
    "voltage": "220V", "frequency": "50Hz",
    "adapter_service": "Adapter is available in-room; transformers are available from Housekeeping (Adapters page title: Ext 13).",
    "source_url": ELECTRICITY,
}
profile.pop("total_accommodations", None)
profile["inventory"] = {"rooms_and_suites": 198, "private_pool_villas": 68, "source_url": HOME}
profile["source_urls"] = [HOME, CONTACT, CHECKOUT, CHECKIN, ELECTRICITY, ADAPTERS]
profile["verified_at"] = VERIFIED_AT
profile["data_quality"] = {
    "curated_at": VERIFIED_AT,
    "note": "Guest-facing values are limited to current or explicitly time-bounded official Furama evidence; conflicting official guidance is surfaced instead of silently guessed.",
}
profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Service catalog is an operational UX layer: keep real services, strip unsupported hours/extensions/fees.
service_path = dataset_path(SERVICE_CATALOG_RELATIVE, ROOT / "datasets")
services = json.loads(service_path.read_text(encoding="utf-8"))
patches = {
    "service.in_room_dining": {
        "entity_id":"service.in_room_dining","operating_hours":"06:30 - 00:00","contact_extension":"10","is_complimentary":None,
        "description":"In-room dining is available 06:30–midnight; the current Room Service page directs guests to Front Desk Ext 10. Late-night dishes are listed 22:30–06:00.",
        "source_urls":[ROOM_SERVICE],
    },
    "service.room_cleaning": {"operating_hours":"08:00 - 17:00","contact_extension":None,"is_complimentary":None,"description":"Room cleaning is arranged daily 08:00–17:00; guests may contact Housekeeping to request a special time. Housekeeping can also supply extra blankets, pillows, coat hangers and related items.","source_urls":[ROOM_CLEANING,HOUSEKEEPING]},
    "service.bath_towels": {"operating_hours":None,"contact_extension":None,"is_complimentary":None,"description":"Additional towels are available through Housekeeping; no public service hours or extension are stated on the current page.","source_urls":[BATH_TOWELS]},
    "service.adapters": {"operating_hours":None,"contact_extension":"13","is_complimentary":None,"description":"An adapter is available in-room; transformers are available from Housekeeping. The official page is titled Adapters – Ext 13.","source_urls":[ADAPTERS,ELECTRICITY]},
    "service.extra_bed": {"operating_hours":None,"contact_extension":None,"is_complimentary":False,"description":"Extra bed in the resort or folded mattress in a Villa can be requested from Front Desk; additional charges apply. No public hours are stated.","source_urls":[EXTRA_BED]},
    "service.luggage": {"operating_hours":None,"contact_extension":None,"is_complimentary":None,"description":"Concierge assists with luggage and related services, including assistance with luggage lost from a flight. No public hours or extension are stated.","source_urls":[LUGGAGE,LOST_LUGGAGE]},
    "transportation.taxi": {"operating_hours":"24/7","contact_extension":None,"is_complimentary":None,"description":"Taxis are available 24 hours at Resort and Villa entrances; Concierge can arrange a taxi.","source_urls":[TAXI]},
    "service.wake_up_calls": {"operating_hours":None,"contact_extension":"0","is_complimentary":None,"description":"Wake-up calls can be requested through the Telephone Operator. Operator extension 0 is independently published on the Medical Service page.","source_urls":[WAKE_UP,MEDICAL]},
    "service.currency_exchange": {"operating_hours":None,"contact_extension":None,"is_complimentary":None,"description":"Foreign currencies can be exchanged at Front Desk at normal bank rates; no public service hours are stated.","source_urls":[CURRENCY_EXCHANGE]},
    "service.first_aid": {"operating_hours":None,"contact_extension":"0","is_complimentary":None,"description":"For first aid, contact the Operator. The official First Aid page does not claim 24/7 nurse coverage.","source_urls":[FIRST_AID,MEDICAL]},
    "spa.booking": {"operating_hours":"09:00 - 22:00","contact_extension":"16","is_complimentary":None,"description":"V-Senses Spa is open 09:00–22:00; bookings are accepted until 20:30 and treatments should be scheduled 24 hours in advance. Dial Ext 16.","source_urls":[SPA,SPA_ARTICLE]},
    "dining.restaurant_reservation": {"entity_id":"dining.restaurant_reservation","operating_hours":"06:00 - 00:00","contact_extension":"12","is_complimentary":None,"description":"Restaurant & bar reservations can be requested through Room Service between 06:00 and midnight at Ext 12.","source_urls":[RESTAURANTS_BARS]},
    "service.babysitting": {"operating_hours":None,"contact_extension":"3777","is_complimentary":False,"description":"Babysitting should be booked one day in advance through Tour Desk/Kids Club; additional charges apply. Tour Desk extension 3777 is published on the Kids Club page.","source_urls":[BABYSITTING_PAGE,KIDS]},
    "transportation.car_rental": {"operating_hours":None,"contact_extension":None,"is_complimentary":None,"description":"Car rental can be arranged through Concierge in the resort lobby; no public operating hours or extension are stated.","source_urls":[CAR_RENTAL]},
    "service.late_checkout": {"operating_hours":None,"contact_extension":None,"is_complimentary":False,"description":"Standard check-out is 11:00. Late check-out is subject to availability; 50% of daily room rate applies until 18:00 and one night after 18:00.","source_urls":[CHECKOUT]},
    "service.lost_and_found": {"operating_hours":None,"contact_extension":None,"is_complimentary":None,"description":"For Lost & Found assistance, the official page directs guests to Housekeeping; no public hours or extension are stated.","source_urls":[LOST_FOUND]},
    "service.courier": {"operating_hours":None,"contact_extension":None,"is_complimentary":None,"description":"Courier service can be arranged through the Business Centre in the Resort Lobby; no public hours are stated.","source_urls":[COURIER]},
    "service.flight_reconfirmation": {"operating_hours":None,"contact_extension":None,"is_complimentary":None,"description":"Flight information, reconfirmation and change assistance is available through the Business Centre in the Resort Lobby; no public hours are stated.","source_urls":[FLIGHT_INFO]},
}
existing_service_ids = {s.get("service_id") for s in services}
if "transportation.airport_transfer" not in existing_service_ids:
    services.append({
        "service_id":"transportation.airport_transfer","entity_id":"transportation.airport_transfer",
        "name":"Airport Transfer Arrangement","service_type":"staff_request","category":"transportation","department_id":"FO_CONCIERGE",
        "contact_extension":None,"operating_hours":None,"is_complimentary":None,
        "description":"Concierge can arrange airport transport. For early-morning departures, the official page asks guests to book before 21:00 the day before.",
        "names_by_locale":{"en":"Airport Transfer Arrangement","vi":"Sắp xếp đưa đón sân bay","ko":"공항 이동 서비스 요청","zh":"机场接送安排"},
        "source_urls":[AIRPORT_TRANSFER],"verified_at":VERIFIED_AT,"verification_status":"official_web_reviewed",
    })
if "service.medical_centre" not in existing_service_ids:
    services.append({
        "service_id":"service.medical_centre","entity_id":"service.medical_centre",
        "name":"Medical Centre Assistance","service_type":"staff_request","category":"guest_services","department_id":"MEDICAL_CENTRE",
        "contact_extension":"3420","operating_hours":"08:00 - 17:00 except Saturday afternoon and Sunday","is_complimentary":None,
        "description":"Medical Centre staffed 08:00–17:00 except Saturday afternoon and Sunday; contact Ext 3420 or Operator 0.",
        "names_by_locale":{"en":"Medical Centre Assistance","vi":"Hỗ trợ Trung tâm Y tế","ko":"의료 센터 지원","zh":"医疗中心协助"},
        "source_urls":[MEDICAL],"verified_at":VERIFIED_AT,"verification_status":"official_web_reviewed",
    })

for service in services:
    patch = patches.get(service.get("service_id"))
    if patch:
        service.update(patch)
        service["verified_at"] = LATEST_REVIEWED_AT if service.get("service_id") == "service.room_cleaning" else VERIFIED_AT
        service["verification_status"] = "official_web_reviewed"
service_path.write_text(json.dumps(services, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Operational facts that cannot be safely learned from the public web stay out of RAG.
# This ledger tells operators exactly which internal sources are still needed.
def operational_gap(key: str, status: str, owner: str, reason: str, required_fields: list[str],
                    preferred_source: str, *, production_blocker: bool = False) -> dict[str, Any]:
    return {
        "key": key, "status": status, "owner": owner, "reason": reason,
        "required_fields": required_fields,
        "preferred_authoritative_source": preferred_source,
        "production_blocker_for_autonomous_commit": production_blocker,
        "acceptance_rule": "Do not mark resolved until source owner, effective date, and provenance are recorded.",
    }


operational_gaps = {
    "property_id": PROPERTY,
    "reviewed_at": LATEST_REVIEWED_AT,
    "items": [
        operational_gap("wifi_password", "internal_source_required", "front_office",
                        "Public pages confirm Wi-Fi availability but do not publish the active password.",
                        ["ssid", "active_password_or_auth_method", "guest_scope", "effective_from", "effective_until_or_rotation_policy"],
                        "property_internal_only"),
        operational_gap("fixed_shuttle_schedule", "not_publicly_verified", "concierge",
                        "The official Shuttle-Bus page confirms a daily paid Furama–Hoi An shuttle as per the resort schedule, but does not publish departure times or the current timetable.",
                        ["route", "pickup_points", "departure_times", "days_of_operation", "price", "booking_cutoff", "effective_dates"],
                        "official_property_or_transport_desk"),
        operational_gap("minibar_prices", "internal_source_required", "food_beverage",
                        "Official room pages confirm a minibar but do not publish the current item price list.",
                        ["item_id", "item_name", "unit", "price_vnd", "tax_service_charge_inclusion", "effective_from", "effective_until"],
                        "current_fnb_price_list"),
        operational_gap("ev_charging", "not_publicly_verified", "engineering",
                        "No reviewed official Furama source publishes EV charging availability.",
                        ["available", "location", "connector_type", "power_kw", "access_rule", "price", "hours"],
                        "engineering_or_front_office_verified"),
        operational_gap("grab_instructions", "local_guidance_required", "concierge",
                        "Official transport pages cover taxi/concierge arrangements, not app-specific Grab instructions.",
                        ["approved_pickup_point", "guest_wayfinding", "vehicle_access_restrictions", "after_hours_variation"],
                        "concierge_local_sop"),
        operational_gap("official_service_sla_matrix", "internal_source_required", "rooms_division",
                        "Prototype timing targets are industry-calibrated synthetic values; Furama has not provided official acknowledgement/attendance/resolution SLAs.",
                        ["service_code", "acknowledgement_target_min", "attendance_target_min", "resolution_target_or_range", "clock_pause_rules", "peak_load_rule", "effective_date"],
                        "signed_rooms_division_sla", production_blocker=True),
        operational_gap("engineering_department_contact_and_hours", "internal_source_required", "engineering",
                        "Engineering routing is operationally realistic but no current public Furama extension, shift coverage or internal escalation chain is published.",
                        ["department_id", "internal_extension_or_queue", "coverage_hours", "after_hours_owner", "supervisor_role"],
                        "engineering_sop_or_directory", production_blocker=True),
        operational_gap("room_access_and_dnd_sop", "internal_source_required", "housekeeping_security",
                        "Routine room-entry, DND override, contactless delivery and master-key procedures are internal SOPs and must not be inferred.",
                        ["dnd_contact_sequence", "entry_authority", "contactless_handoff", "master_key_authority", "welfare_check_boundary", "audit_requirement"],
                        "signed_rooms_security_sop", production_blocker=True),
        operational_gap("ops_escalation_chain", "internal_source_required", "rooms_division",
                        "Department supervisor/duty-manager escalation recipients and thresholds are not public; prototype performs only one idempotent escalation.",
                        ["service_code", "escalation_after_min", "level1_role", "level2_role", "priority_change", "repeat_policy", "notification_channel"],
                        "signed_department_escalation_matrix", production_blocker=True),
        operational_gap("live_tour_inventory_prices_suppliers", "staff_confirmation_required", "tour_desk",
                        "Public destination guidance does not establish live tour supplier inventory, pickup time, cutoff or prices.",
                        ["product_id", "supplier", "destination", "pickup_time", "duration", "price", "currency", "capacity", "cutoff", "cancellation_policy", "valid_from", "valid_to"],
                        "tour_desk_live_inventory", production_blocker=True),
    ],
}
(DATA / "operational-gaps.json").write_text(json.dumps(operational_gaps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Keep operational workflow contact channels aligned with the current service catalog.
workflow_path = dataset_path(WORKFLOWS_RELATIVE, ROOT / "datasets")
workflows = load_jsonl(workflow_path)
for workflow in workflows:
    if workflow.get("workflow_id") == "wf_in_room_dining_order":
        workflow["channel"] = "kiosk_interactive_or_front_desk_ext10"
        for step in workflow.get("steps", []):
            if step.get("step") == 3:
                step["action"] = (
                    "Creates a staff request ticket containing the order details for the In-Room Dining team; "
                    "guest fallback/contact is Front Desk Ext 10. Kitchen/POS transmission is not automated by the kiosk"
                )
write_jsonl(workflow_path, workflows)

# Write canonical ledgers last. The primary facts ledger is runtime-clean: only
# approved, web-reviewed facts live there. Rejected/superseded rows are moved to
# a separate audit ledger rather than silently deleted.
active_facts = sorted(
    [f for f in facts if f.get("publication_status", "approved") == "approved"],
    key=lambda f: (f.get("entity_id", ""), f.get("fact_type", ""), f.get("context", ""), f.get("canonical_fact_id", "")),
)
quarantined = [f for f in facts if f.get("publication_status", "approved") != "approved"]
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
existing_quarantine = load_jsonl(QUARANTINE) if QUARANTINE.exists() else []
archive_by_id = {f["canonical_fact_id"]: f for f in existing_quarantine}
for f in quarantined:
    archived = dict(f)
    archived.setdefault("archived_at", VERIFIED_AT)
    archive_by_id[archived["canonical_fact_id"]] = archived
# If a fact ID is active again, it must not also remain in quarantine.
for f in active_facts:
    archive_by_id.pop(f["canonical_fact_id"], None)
# Publishability is explicit: approved catalog entities without an approved fact
# remain discoverable in the catalog but are not represented as RAG knowledge.
active_fact_entities = {f["entity_id"] for f in active_facts}
for entity in entities:
    entity["knowledge_status"] = "runtime_publishable" if entity["entity_id"] in active_fact_entities else "catalog_only"

# Known decimal-comma tokenization artefacts are deterministic extractor failures, not facts awaiting review.
for _bad_id in {"cfact_023395c783d1644bb5e23f94", "cfact_9d8f3ccb7fd81117e4e7e96e", "cfact_d1d405dbbd62bb97ce1a54e4", "cfact_4e3733d2d8f75d421f9fe658"}:
    if _bad_id in archive_by_id:
        archive_by_id[_bad_id]["publication_status"] = "rejected_extraction"
        archive_by_id[_bad_id]["curation_reason"] = "Decimal-comma tokenization fragment from legacy extraction; official room pages publish the full decimal measurement."
archive_rows = sorted(archive_by_id.values(), key=lambda f: (f.get("entity_id", ""), f.get("canonical_fact_id", "")))

write_jsonl(ENTITIES, entities)
write_jsonl(FACTS, active_facts)
write_jsonl(QUARANTINE, archive_rows)
write_jsonl(RELATIONS, dedup)

status_counts: dict[str, int] = {}
for f in archive_rows:
    status = f.get("publication_status", "archived")
    status_counts[status] = status_counts.get(status, 0) + 1
print(json.dumps({
    "entities": len(entities),
    "active_facts": len(active_facts),
    "quarantined_facts": len(archive_rows),
    "quarantine_statuses": status_counts,
    "relations": len(dedup),
    "verified_at": VERIFIED_AT,
}, ensure_ascii=False, sort_keys=True))
