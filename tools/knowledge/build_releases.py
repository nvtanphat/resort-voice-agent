"""Build and pin Furama releases: property profile, map release, planning release."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "src")
from concierge_kiosk.core.dataset_layout import (
    ALIASES,
    ENTITIES,
    MAP,
    MAP_PATHS,
    PROPERTY,
    SERVICE_CATALOG,
    dataset_path,
)
from concierge_kiosk.core.property_profile import parse_property_profile


def request_kind_for_catalog_service(service: dict) -> str:
    """Map a canonical Furama service entry to the runtime request kind."""
    kind_map = {
        "dining": "dining",
        "housekeeping": "housekeeping",
        "front_desk": "human",
        "transportation": "transport",
        "guest_services": "human",
        "spa": "facilities",
    }
    sid = service["service_id"]
    kind = kind_map.get(service.get("category", ""), "facilities")
    if sid == "service.bath_towels":
        return "facilities"
    if sid == "service.room_cleaning":
        return "housekeeping"
    if sid in {"service.in_room_dining", "dining.restaurant_reservation"}:
        return "dining"
    if sid == "spa.booking":
        return "facilities"
    if sid == "service.tour_reservation":
        return "tour"
    if sid == "service.luggage":
        return "human"
    if sid == "service.late_checkout":
        return "front_office"
    return kind


def build_releases():
    releases_dir = Path("releases")
    releases_dir.mkdir(exist_ok=True)

    # 1. Build Property Profile from canonical property data.
    canonical_profile = json.loads(dataset_path(PROPERTY).read_text(encoding="utf-8"))
    property_id = canonical_profile["property_id"]
    property_name = canonical_profile["name"]
    property_timezone = canonical_profile["timezone"]
    with dataset_path(SERVICE_CATALOG).open(encoding="utf-8") as f:
        services = json.load(f)

    catalog = []
    for s in services:
        sid = s["service_id"]
        kind = request_kind_for_catalog_service(s)

        names = s.get("names_by_locale", {})
        en_n = names.get("en", s["name"])
        vi_n = names.get("vi", s["name"])
        ko_n = names.get("ko", s["name"])
        zh_n = names.get("zh", s["name"])

        title = {"en": en_n, "vi": vi_n, "ko": ko_n, "zh": zh_n}
        question = {
            "en": f"Would you like assistance with {en_n}?",
            "vi": f"Bạn có muốn được hỗ trợ về {vi_n} không?",
            "ko": f"{ko_n} 서비스를 요청하시겠습니까?",
            "zh": f"您需要 {zh_n} 的协助吗？",
        }
        catalog.append({
            "id": sid[:48],
            "request_kind": kind,
            "title": title,
            "question": question,
        })

    profile_payload = {
        "property_id": property_id,
        "property_name": property_name,
        "property_timezone": property_timezone,
        "default_language": "vi",
        "enabled_languages": ["vi", "en", "zh", "ko"],
        "low_risk_requires_verified_room": True,
        "emergency_policy": {
            "escalation_after_seconds": 60,
            "default_kiosk_location": "property_furama_resort_danang",
        },
        "session_policy": {
            "idle_timeout_seconds": 1200,
            "warning_seconds": 30,
        },
        "voice_policy": {
            "protocol": 2,
            "sample_rate": 16000,
            "max_frame_bytes": 131072,
            "max_windowed_audio_bytes": 6000000,
            "queue_bytes": 1000000,
            "credit_bytes": 262144,
            "vad_start_ms": 180,
            "vad_end_silence_ms": 650,
            "barge_preview_ms": 160,
            "barge_confirm_ms": 480,
            "false_interruption_recovery_ms": 500,
            "backpressure_timeout_ms": 3000,
            "vad": {
                "engine": "energy",
                "noise_floor_initial": 0.002,
                "positive_threshold": 0.006,
                "negative_threshold": 0.004,
                "redemption_ms": 96,
                "pre_speech_pad_ms": 420,
                "min_speech_ms": 320,
                "playback_positive_threshold": 0.065,
                "continuation_extra_ms": 620,
            },
        },
        "service_catalog": catalog,
    }

    # Verify schema
    parse_property_profile(profile_payload, max_session_ttl=1200)

    profile_path = releases_dir / "property-profile.json"
    profile_bytes = json.dumps(profile_payload, indent=2, ensure_ascii=False).encode("utf-8")
    profile_path.write_bytes(profile_bytes)
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    print(f"Property Profile: {profile_path} (SHA-256: {profile_sha256})")

    # 2. Resolve active knowledge revisions used as release provenance.
    con = sqlite3.connect("data/concierge.sqlite3")
    con.row_factory = sqlite3.Row

    def revisions(source_id: str) -> dict[str, str]:
        result = {}
        for lang in ["vi", "en", "zh", "ko"]:
            row = con.execute(
                "SELECT revision FROM knowledge WHERE property_id=? AND source=? AND language=? AND active=1 LIMIT 1",
                (property_id, source_id, lang),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Missing active knowledge revision for {source_id}/{lang}")
            result[lang] = row["revision"]
        return result

    def validate_release(payload: dict, schema_path: str) -> None:
        from jsonschema import Draft202012Validator, FormatChecker
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.absolute_path))
        if errors:
            details = "; ".join(f"{list(err.absolute_path)}: {err.message}" for err in errors[:8])
            raise RuntimeError(f"Release schema validation failed: {details}")

    property_source = "kb_property_furama_resort_danang"
    property_revs = revisions(property_source)
    cafe_source = "kb_restaurant_cafe_indochine"
    cafe_revs = revisions(cafe_source)
    don_source = "kb_restaurant_don_cipriani"
    don_revs = revisions(don_source)
    spa_source = "kb_spa_v_senses_wellness"
    spa_revs = revisions(spa_source)

    # 3. Build map release from canonical places plus separately reviewed,
    # evidence-backed paths. Legacy/unverified places remain in the migration
    # dataset but are not exposed by the public release.
    map_raw = json.loads(dataset_path(MAP).read_text(encoding="utf-8"))
    map_paths = json.loads(dataset_path(MAP_PATHS).read_text(encoding="utf-8"))
    entities = {}
    for line in dataset_path(ENTITIES).read_text(encoding="utf-8").splitlines():
        if line.strip():
            entity = json.loads(line)
            entities[entity["entity_id"]] = entity

    def release_id(entity_id: str) -> str:
        base = re.sub(r"[^a-z0-9_]+", "_", entity_id.casefold().replace(".", "_")).strip("_")
        if not base or not base[0].isalpha():
            base = "p_" + base
        if len(base) <= 40:
            return base
        digest = hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:7]
        return f"{base[:32]}_{digest}"[:40]

    # Publish both concrete entities and coarse zone hubs.  Zone hubs are used
    # only as an evidence-bounded fallback when the reviewed resort map supports
    # a facility area but not a corridor-level route to a specific sub-venue.
    zone_i18n = {
        "zone.main_lobby": {"en": "Main Building & Lobby", "vi": "Tòa nhà chính & Sảnh", "ko": "메인 빌딩 & 로비", "zh": "主楼与大堂"},
        "zone.ocean_wing": {"en": "Ocean Wing Guest Rooms", "vi": "Khu phòng Ocean Wing", "ko": "오션 윙 객실 구역", "zh": "海景翼客房区"},
        "zone.garden_wing": {"en": "Garden & Lagoon Wing Guest Rooms", "vi": "Khu phòng Garden & Lagoon Wing", "ko": "가든 & 라군 윙 객실 구역", "zh": "花园与泻湖翼客房区"},
        "zone.villas": {"en": "Furama Villas", "vi": "Khu biệt thự Furama", "ko": "푸라마 빌라 구역", "zh": "富丽华别墅区"},
        "zone.dining_hub": {"en": "Dining & Culinary Area", "vi": "Khu ẩm thực & nhà hàng", "ko": "다이닝 & 레스토랑 구역", "zh": "餐饮与餐厅区域"},
        "zone.convention_centre": {"en": "International Convention Palace", "vi": "Cung Hội nghị Quốc tế", "ko": "국제컨벤션팰리스", "zh": "国际会议宫"},
        "zone.wellness_recreation": {"en": "Wellness & Recreation Area", "vi": "Khu chăm sóc sức khỏe & giải trí", "ko": "웰니스 & 레크리에이션 구역", "zh": "康体与休闲区域"},
    }
    curated_aliases = json.loads(
        dataset_path(ALIASES).read_text(encoding="utf-8")).get("aliases_by_entity", {})
    places = []
    seen_place_ids = set()
    for zone in map_raw.get("zones", []):
        zone_ref = zone["zone_id"]
        hub_id = release_id(zone_ref)
        if hub_id in seen_place_ids:
            raise RuntimeError(f"Duplicate map zone id {hub_id}")
        seen_place_ids.add(hub_id)
        labels = zone_i18n.get(zone_ref) or {lang: str(zone.get("name") or zone_ref) for lang in ("vi", "en", "zh", "ko")}
        zone_aliases = [zone.get("name", ""), zone_ref]
        zone_aliases.extend(labels.values())
        aliases = []
        for alias in zone_aliases:
            alias = str(alias).strip()
            if 2 <= len(alias) <= 60 and alias.casefold() not in {a.casefold() for a in aliases}:
                aliases.append(alias)
        places.append({
            "id": hub_id, "place_type": "zone_hub", "zone_hub_id": hub_id,
            "aliases": aliases or [hub_id],
            "labels": {lang: str(labels[lang])[:80] for lang in ("vi", "en", "zh", "ko")},
        })

        for location in zone.get("locations", []):
            if location.get("verification_status") == "legacy_unverified":
                continue
            entity_id = location["entity_id"]
            entity = entities.get(entity_id, {})
            names = entity.get("names_by_locale") or {}
            fallback = location.get("name") or entity.get("name") or entity_id
            pid = release_id(entity_id)
            if pid in seen_place_ids:
                raise RuntimeError(f"Duplicate map release id {pid}")
            seen_place_ids.add(pid)
            alias_candidates = [fallback, entity.get("name", ""), entity_id]
            alias_candidates.extend(v for v in names.values() if isinstance(v, str))
            # Curated guest wording ("gym", "pool", "don cipriani"), round-robin
            # across languages so each keeps its strongest aliases within the cap.
            curated = curated_aliases.get(entity_id, {})
            per_language = [list(curated.get(lang, [])) for lang in ("en", "vi", "ko", "zh")]
            while any(per_language):
                for queue in per_language:
                    if queue:
                        alias_candidates.append(queue.pop(0))
            aliases = []
            for alias in alias_candidates:
                alias = str(alias).strip()
                if 2 <= len(alias) <= 60 and alias.casefold() not in {a.casefold() for a in aliases}:
                    aliases.append(alias)
                if len(aliases) == 20:
                    break
            places.append({
                "id": pid, "place_type": "entity", "zone_hub_id": hub_id,
                "aliases": aliases or [pid],
                "labels": {
                    "vi": str(names.get("vi") or fallback)[:80],
                    "en": str(names.get("en") or fallback)[:80],
                    "zh": str(names.get("zh") or fallback)[:80],
                    "ko": str(names.get("ko") or fallback)[:80],
                },
            })

    origin = release_id("property.furama_resort_danang")
    paths = []
    for edge in map_paths.get("paths", []):
        start = release_id(edge["from_entity_id"])
        end = release_id(edge["to_entity_id"])
        if start not in seen_place_ids or end not in seen_place_ids:
            raise RuntimeError(f"Map path references unpublished place: {edge['from_entity_id']} -> {edge['to_entity_id']}")
        paths.append({
            "from": start, "to": end, "precision": edge["precision"],
            "instructions": edge["instructions"], "evidence": edge["evidence"],
        })
    map_payload = {
        "approved": True,
        "property_id": property_id,
        "effective_from": "2026-01-01",
        "effective_to": "2030-01-01",
        "source_id": property_source,
        "source_revisions": property_revs,
        "origin": origin,
        "places": places,
        "paths": paths,
    }
    validate_release(map_payload, "datasets/schemas/releases/map-release.schema.json")
    map_path = releases_dir / "map-release.json"
    map_bytes = json.dumps(map_payload, indent=2, ensure_ascii=False).encode("utf-8")
    map_path.write_bytes(map_bytes)
    map_sha256 = hashlib.sha256(map_bytes).hexdigest()
    print(f"Map Release: {map_path} ({len(places)} verified places, {len(paths)} evidence-backed paths; SHA-256: {map_sha256})")

    # 4. Build planning release using schema-native start/end fields. Pin the
    # exact approved runtime quote instead of duplicating compiler wording here;
    # this keeps the release valid when guest-facing localization is improved.
    def source_quotes(source_id: str, source_revisions: dict[str, str], needle: str) -> dict[str, str]:
        quotes: dict[str, str] = {}
        for lang in ["vi", "en", "zh", "ko"]:
            rows = con.execute(
                "SELECT body FROM knowledge WHERE property_id=? AND language=? AND source=? AND revision=? "
                "AND classification='public' AND active=1 ORDER BY section_ordinal,id",
                (property_id, lang, source_id, source_revisions[lang]),
            ).fetchall()
            matches = [str(row["body"]).strip() for row in rows if needle in str(row["body"])]
            if not matches:
                raise RuntimeError(f"No approved planning quote for {source_id}/{lang}: {needle}")
            # Child chunks are intentionally concise; pin the shortest exact claim.
            quotes[lang] = min(matches, key=len)
        return quotes

    don_quote = source_quotes(don_source, don_revs, "18:00–22:00")
    spa_quote = source_quotes(spa_source, spa_revs, "09:00–22:00")

    def source_titles(source_id: str, source_revisions: dict[str, str]) -> list[str]:
        values: list[str] = []
        for lang in ["vi", "en", "zh", "ko"]:
            row = con.execute(
                "SELECT title FROM knowledge WHERE property_id=? AND language=? AND source=? AND revision=? "
                "AND classification='public' AND active=1 AND title<>'' ORDER BY section_ordinal,id LIMIT 1",
                (property_id, lang, source_id, source_revisions[lang]),
            ).fetchone()
            if row is None or not str(row["title"]).strip():
                raise RuntimeError(f"No approved planning title for {source_id}/{lang}")
            title = str(row["title"]).strip()
            if title not in values:
                values.append(title)
        return values

    don_aliases = source_titles(don_source, don_revs)
    spa_aliases = source_titles(spa_source, spa_revs)

    def source_window(quotes: dict[str, str]) -> dict[str, str]:
        # Planning hours must be evidenced by every locale quote. Do not keep a
        # second hard-coded schedule beside the approved knowledge source.
        import re
        windows = set()
        for quote in quotes.values():
            match = re.search(r"(?<!\d)([01]\d|2[0-3]):[0-5]\d\s*[–—-]\s*([01]\d|2[0-3]):[0-5]\d(?!\d)", quote)
            if not match:
                raise RuntimeError("Approved planning quote does not contain a time window")
            raw = match.group(0)
            start, end = re.split(r"\s*[–—-]\s*", raw)
            windows.add((start, end))
        if len(windows) != 1:
            raise RuntimeError("Planning time window differs across approved locales")
        start, end = next(iter(windows))
        return {"start": start, "end": end}

    don_window = source_window(don_quote)
    spa_window = source_window(spa_quote)
    planning_payload = {
        "approved": True,
        "property_id": property_id,
        "effective_from": "2026-01-01",
        "effective_to": "2030-01-01",
        "activities": [
            {
                "activity_id": "don_cipriani_dinner",
                "topic": "dining",
                "aliases": don_aliases,
                "source_id": don_source,
                "source_revisions": don_revs,
                "source_quote": don_quote,
                "duration_minutes": 60,
                "travel_buffer_minutes": 10,
                "weekdays": [0, 1, 2, 3, 4, 5, 6],
                "windows": [don_window],
            },
            {
                "activity_id": "v_senses_spa",
                "topic": "facilities",
                "aliases": spa_aliases,
                "source_id": spa_source,
                "source_revisions": spa_revs,
                "source_quote": spa_quote,
                "duration_minutes": 90,
                "travel_buffer_minutes": 15,
                "weekdays": [0, 1, 2, 3, 4, 5, 6],
                "windows": [spa_window],
            },
        ],
    }
    validate_release(planning_payload, "datasets/schemas/releases/planning-release.schema.json")
    planning_path = releases_dir / "planning-release.json"
    planning_bytes = json.dumps(planning_payload, indent=2, ensure_ascii=False).encode("utf-8")
    planning_path.write_bytes(planning_bytes)
    planning_sha256 = hashlib.sha256(planning_bytes).hexdigest()
    print(f"Planning Release: {planning_path} (schema validated; SHA-256: {planning_sha256})")
    con.close()

    # 5. Pin release hashes into local development/E2E environments.
    env_content = f"""CONCIERGE_ENV=development
CONCIERGE_DB_PATH=./data/concierge.sqlite3
CONCIERGE_PUBLIC_ORIGIN=http://localhost:8000
LANGGRAPH_STRICT_MSGPACK=true

CONCIERGE_PROPERTY_PROFILE_PATH={profile_path.as_posix()}
CONCIERGE_PROPERTY_PROFILE_SHA256={profile_sha256}
CONCIERGE_MAP_RELEASE_PATH={map_path.as_posix()}
CONCIERGE_MAP_RELEASE_SHA256={map_sha256}
CONCIERGE_PLANNING_RELEASE_PATH={planning_path.as_posix()}
CONCIERGE_PLANNING_RELEASE_SHA256={planning_sha256}
"""
    env_template = Path("config/local-runtime.env.example")
    env_template.write_text(env_content, encoding="utf-8", newline="\n")
    print(f"Wrote safe local runtime template: {env_template}")
    print("Copy it to .env locally when needed; release tooling never writes secret-bearing .env files.")


if __name__ == "__main__":
    build_releases()
