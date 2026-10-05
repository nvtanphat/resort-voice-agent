"""Refresh Furama meeting facts/map against the current official ICP capacity floor plan.

Source of truth checked 2026-10-01:
https://furamavietnam.com/wp-content/uploads/2025/02/Furama-ICP-Capacity-Charts-Floor-PLan.pdf
and the current Meetings & Conferences page.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import ENTITIES, FACTS, MAP, dataset_path

FACTS_PATH = dataset_path(FACTS)
MAP_PATH = dataset_path(MAP)
ENTITIES_PATH = dataset_path(ENTITIES)
PDF_URL = "https://furamavietnam.com/wp-content/uploads/2025/02/Furama-ICP-Capacity-Charts-Floor-PLan.pdf"
PAGE_URL = "https://furamavietnam.com/meetings-events/meetings-conferences/"

CURRENT = {
    "meeting.danang_grand_ballroom": {"area_sqm": 774.0, "height_m": 5.5, "theatre": 1000, "classroom": 500, "banquet": 500, "cocktail": 1000, "u_shape": 120},
    "meeting.ballroom_1": {"area_sqm": 258.0, "height_m": 5.5, "theatre": 250, "classroom": 120, "banquet": 130, "cocktail": 250, "u_shape": 70},
    "meeting.ballroom_2": {"area_sqm": 258.0, "height_m": 5.5, "theatre": 250, "classroom": 120, "banquet": 130, "cocktail": 250, "u_shape": 70},
    "meeting.ballroom_3": {"area_sqm": 258.0, "height_m": 5.5, "theatre": 250, "classroom": 120, "banquet": 130, "cocktail": 250, "u_shape": 70},
    "meeting.han_river_1": {"area_sqm": 39.0, "height_m": 2.5, "theatre": 30, "classroom": 20, "banquet": 20, "cocktail": 30, "u_shape": 15},
    "meeting.han_river_2": {"area_sqm": 41.0, "height_m": 2.5, "theatre": 30, "classroom": 20, "banquet": 20, "cocktail": 30, "u_shape": 15},
    "meeting.son_tra_1": {"area_sqm": 62.0, "height_m": 2.5, "theatre": 60, "classroom": 36, "banquet": 40, "cocktail": 60, "u_shape": 24},
    "meeting.ocean_ballroom": {"area_sqm": 363.0, "height_m": 3.0, "theatre": 300, "classroom": 160, "banquet": 200, "cocktail": 250, "u_shape": 100},
}

ROW_EVIDENCE = {
    "meeting.danang_grand_ballroom": "Danang Grand Ballroom | Area 774 sqm | 36x21.5x5.5 m | Banquet 500 | Theatre 1000 | Classroom 500 | U-Shape 120 | Cocktail 1000",
    "meeting.ballroom_1": "Danang Ballroom 1 | Area 258 sqm | 12x21.5x5.5 m | Banquet 130 | Theatre 250 | Classroom 120 | U-Shape 70 | Cocktail 250",
    "meeting.ballroom_2": "Danang Ballroom 2 | Area 258 sqm | 12x21.5x5.5 m | Banquet 130 | Theatre 250 | Classroom 120 | U-Shape 70 | Cocktail 250",
    "meeting.ballroom_3": "Danang Ballroom 3 | Area 258 sqm | 12x21.5x5.5 m | Banquet 130 | Theatre 250 | Classroom 120 | U-Shape 70 | Cocktail 250",
    "meeting.han_river_1": "Han River Room 1 | Area 39 sqm | 8.5x4.5x2.5 m | Banquet 20 | Theatre 30 | Classroom 20 | U-Shape 15 | Cocktail 30",
    "meeting.han_river_2": "Han River Room 2 | Area 41 sqm | 6x6.9x2.5 m | Banquet 20 | Theatre 30 | Classroom 20 | U-Shape 15 | Cocktail 30",
    "meeting.son_tra_1": "Son Tra Room | Area 62 sqm | 6.5x9.5x2.5 m | Banquet 40 | Theatre 60 | Classroom 36 | U-Shape 24 | Cocktail 60",
    "meeting.ocean_ballroom": "Ocean Ballroom | Area 363 sqm | 33x11x3 m | Banquet 200 | Theatre 300 | Classroom 160 | U-Shape 100 | Cocktail 250",
}


def provenance(eid: str) -> list[dict]:
    evidence = ROW_EVIDENCE[eid]
    return [{
        "document_id": "official_icp_capacity_floorplan_2025",
        "source_url": PDF_URL,
        "evidence": evidence,
        "evidence_sha256": hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
        "is_dedicated_page": True,
        "observed_at": "2026-10-01",
    }]


def update_facts() -> tuple[int, int]:
    facts = [json.loads(line) for line in FACTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = 0
    suppressed = 0
    for fact in facts:
        eid = fact.get("entity_id")
        if eid == "meeting.son_tra_2":
            fact["publication_status"] = "legacy_unverified"
            fact["status_reason"] = "Current official ICP source publishes one Son Tra Room, not Son Tra Room 2. Retained only for migration traceability."
            suppressed += 1
            continue
        if eid not in CURRENT:
            continue
        values = CURRENT[eid]
        kind = fact.get("fact_type")
        context = fact.get("context")
        new_value = None
        raw_value = None
        if kind == "area_sqm" and context == "floor_area":
            new_value = values["area_sqm"]
            raw_value = f"{new_value:g} sqm"
        elif kind == "height_m" and context == "ceiling_height":
            new_value = values["height_m"]
            raw_value = f"{new_value:g} m"
        elif kind == "capacity" and context in {"theatre", "classroom", "banquet", "cocktail", "u_shape"}:
            new_value = values[context]
            raw_value = f"{new_value} pax"
        if new_value is None:
            continue
        fact["raw_value"] = raw_value
        fact["normalized_value"] = new_value
        # Keep every canonical representation consistent with the refreshed
        # normalized value.  locale_support is metadata, not a second source
        # of truth, so stale pre-refresh numbers must never survive here.
        if kind == "area_sqm":
            fact["locale_support"] = {
                "en": f"Floor Area: {new_value:g} sqm",
                "vi": f"Diện tích sàn: {new_value:g} m²",
                "ko": f"바닥 면적: {new_value:g} m²",
                "zh": f"面积：{new_value:g} m²",
            }
        elif kind == "height_m":
            fact["locale_support"] = {
                "en": f"Ceiling Height: {new_value:g} m",
                "vi": f"Chiều cao trần: {new_value:g} m",
                "ko": f"천장 높이: {new_value:g} m",
                "zh": f"层高：{new_value:g} m",
            }
        elif kind == "capacity":
            labels = {
                "theatre": ("Theatre Setup", "Kiểu rạp hát", "극장식", "剧院式"),
                "classroom": ("Classroom Setup", "Kiểu lớp học", "교실식", "课堂式"),
                "banquet": ("Banquet Setup", "Tiệc bàn tròn", "연회식", "宴会式"),
                "cocktail": ("Cocktail Setup", "Tiệc đứng", "칵테일식", "鸡尾酒会式"),
                "u_shape": ("U-Shape Setup", "Kiểu chữ U", "U자형", "U型"),
            }[context]
            fact["locale_support"] = {
                "en": f"{labels[0]}: {new_value} guests",
                "vi": f"{labels[1]}: {new_value} khách",
                "ko": f"{labels[2]}: {new_value}명",
                "zh": f"{labels[3]}：{new_value}人",
            }
        fact["provenance_sources"] = provenance(eid)
        fact["publication_status"] = "approved"
        changed += 1
    FACTS_PATH.write_text("\n".join(json.dumps(f, ensure_ascii=False) for f in facts) + "\n", encoding="utf-8")
    return changed, suppressed


def update_entities() -> int:
    entities = [json.loads(line) for line in ENTITIES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = 0
    for entity in entities:
        if entity.get("entity_id") == "meeting.son_tra_1":
            entity["name"] = "Son Tra Room"
            names = entity.setdefault("names_by_locale", {})
            names.update({"en": "Son Tra Room", "vi": "Phòng Sơn Trà", "ko": "손트라 룸", "zh": "山茶厅"})
            changed += 1
        elif entity.get("entity_id") == "meeting.son_tra_2":
            entity["publication_status"] = "legacy_unverified"
            entity["status_reason"] = "Current official ICP source publishes one Son Tra Room."
            changed += 1
    ENTITIES_PATH.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entities) + "\n", encoding="utf-8")
    return changed


def update_map() -> int:
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    changed = 0
    ocean = None
    for zone in data.get("zones", []):
        retained = []
        for loc in zone.get("locations", []):
            eid = loc.get("entity_id")
            if eid == "meeting.han_river_1" or eid == "meeting.han_river_2":
                loc["level"] = "ICP Level 1"
                loc["source_url"] = PDF_URL
                loc["verification_status"] = "verified"
                changed += 1
            elif eid == "meeting.son_tra_1":
                loc["name"] = "Son Tra Room"
                loc["level"] = "ICP Level 2"
                loc["source_url"] = PDF_URL
                loc["verification_status"] = "verified"
                changed += 1
            elif eid == "meeting.son_tra_2":
                loc["verification_status"] = "legacy_unverified"
                loc["source_url"] = PAGE_URL
                changed += 1
            elif eid in {"meeting.danang_grand_ballroom", "meeting.ballroom_1", "meeting.ballroom_2", "meeting.ballroom_3"}:
                loc["level"] = "ICP Level 1"
                loc["source_url"] = PDF_URL
                loc["verification_status"] = "verified"
                changed += 1
            elif eid == "meeting.ocean_ballroom":
                loc["level"] = "Upper level of main pavilion"
                loc["source_url"] = PAGE_URL
                loc["verification_status"] = "verified"
                ocean = loc
                changed += 1
                continue
            retained.append(loc)
        zone["locations"] = retained
    if ocean:
        main = next(z for z in data["zones"] if z["zone_id"] == "zone.main_lobby")
        main["locations"].append(ocean)
    MAP_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


if __name__ == "__main__":
    fact_changed, suppressed = update_facts()
    print(json.dumps({"facts_refreshed": fact_changed, "legacy_facts_suppressed": suppressed,
                      "entities_updated": update_entities(), "map_entries_updated": update_map()}, sort_keys=True))
