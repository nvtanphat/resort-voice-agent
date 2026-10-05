"""Semantic quality gates for Furama canonical facts.

These checks intentionally go beyond schema/reference integrity. They reject
values that are syntactically present but operationally impossible or conflict
with their own source evidence.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tools.validate_property_dataset import verify_dataset_manifest
from concierge_kiosk.core.dataset_layout import (
    CONTACTS,
    DEPARTMENTS,
    FACTS,
    MAP,
    PLANNING,
    PROPERTY,
    RELATIONS,
    SERVICE_CATALOG,
    WORKFLOWS,
    dataset_path,
    dataset_root,
)

_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_TIME_IN_TEXT = re.compile(r"(?<!\d)([0-2]?\d)\s*[:.]\s*([0-5]\d)(?!\d)")

_PAIR = re.compile(r"(?i)(\d{1,2})\s*[:.]\s*(\d{2})\s*(am|pm)?\s*(?:-|–|—|to|until)\s*(\d{1,2})\s*[:.]\s*(\d{2})\s*(am|pm)?")

def _ampm_clock(hour: str, minute: str, suffix: str) -> str | None:
    h, m = int(hour), int(minute)
    suffix = (suffix or "").casefold()
    if suffix == "pm" and h < 12:
        h += 12
    elif suffix == "am" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"

def _evidence_windows(text: str) -> set[tuple[str, str]]:
    out = set()
    for a, b, ap, c, d, cp in _PAIR.findall(text or ""):
        start, end = _ampm_clock(a, b, ap), _ampm_clock(c, d, cp)
        if start and end:
            out.add((start, end))
    return out


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _clock(value: object) -> bool:
    return isinstance(value, str) and bool(_CLOCK.fullmatch(value))


def _evidence_text(fact: dict) -> str:
    return " ".join(str(src.get("evidence", "")) for src in fact.get("provenance_sources", []))


def _text_times(text: str) -> set[str]:
    out: set[str] = set()
    for hour_s, minute_s in _TIME_IN_TEXT.findall(text or ""):
        hour = int(hour_s)
        minute = int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            out.add(f"{hour:02d}:{minute:02d}")
    return out


def validate_facts(facts: list[dict]) -> list[str]:
    errors: list[str] = []
    for fact in facts:
        fid = fact.get("canonical_fact_id", "<unknown>")
        normalized = fact.get("normalized_value")
        evidence = _evidence_text(fact)

        if fact.get("fact_type") == "price_vnd":
            if not isinstance(normalized, (int, float)) or normalized <= 0:
                errors.append(f"{fid}: price_vnd normalized_value must be a positive number")
            if fact.get("price_basis") != "per_guest":
                errors.append(f"{fid}: price_vnd must declare price_basis=per_guest")
            tax_basis = fact.get("tax_basis")
            if tax_basis not in {"++", "net", "inclusive"}:
                errors.append(f"{fid}: unsupported or missing tax_basis {tax_basis!r}")
            valid_until = fact.get("valid_until")
            if valid_until:
                try:
                    date.fromisoformat(str(valid_until))
                except ValueError:
                    errors.append(f"{fid}: invalid valid_until {valid_until!r}")
            # Protect pricing meaning from being flattened during curation.
            evidence_lower = evidence.casefold()
            if tax_basis == "++" and "++" not in evidence:
                errors.append(f"{fid}: tax_basis '++' is not supported by evidence")
            if tax_basis == "net" and "net" not in evidence_lower:
                errors.append(f"{fid}: tax_basis 'net' is not supported by evidence")

        if fact.get("fact_type") == "opening_hours":
            if not isinstance(normalized, dict):
                errors.append(f"{fid}: opening_hours normalized_value must be object")
                continue
            start, end = normalized.get("start"), normalized.get("end")
            if not _clock(start) or not _clock(end):
                errors.append(f"{fid}: invalid 24-hour clock window {start!r}-{end!r}")
                continue
            evidence_windows = _evidence_windows(evidence)
            if evidence_windows and (start, end) not in evidence_windows:
                raw_windows = _evidence_windows(str(fact.get("raw_value", "")))
                # Only enforce when evidence clearly contains the same schedule or
                # the raw extractor produced a recognizable time pair.
                if len(evidence_windows) == 1 or raw_windows:
                    errors.append(f"{fid}: normalized window {(start, end)} conflicts with evidence {sorted(evidence_windows)}")

        locales = fact.get("locale_support") or {}
        if isinstance(locales, dict):
            locale_blob = " ".join(str(v) for v in locales.values()).casefold()
            evidence_blob = evidence.casefold()
            # Do not let localization turn a bounded service window into 24/7.
            bounded = bool(re.search(r"available\s+from\s+\d|from\s+\d{1,2}[:.]\s*\d{2}\s+to", evidence_blob))
            if bounded and ("24/7" in locale_blob or "24 hours" in locale_blob):
                errors.append(f"{fid}: localized value claims 24/7 but evidence defines a bounded window")

        # Explicit regression guard for known parser corruption patterns.
        blob = json.dumps({"raw": fact.get("raw_value"), "normalized": normalized, "locales": locales}, ensure_ascii=False)
        if "30:00" in blob or "34:30" in blob:
            errors.append(f"{fid}: known corrupted clock token remains in canonical fact")

    return errors



def _normalize_address(value: str) -> str:
    value = value.casefold().replace("đ", "d")
    value = re.sub(r"[–—-]", " ", value)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def validate_cross_artifact_invariants(root: Path) -> list[str]:
    """Reject guest-facing projections that disagree with approved canonical facts."""
    errors: list[str] = []
    def artifact(relative: str) -> Path:
        return dataset_path(relative, root)

    facts = _load_jsonl(artifact(FACTS))
    approved = [fact for fact in facts if fact.get("publication_status", "approved") == "approved"]
    services = json.loads(artifact(SERVICE_CATALOG).read_text(encoding="utf-8"))
    contacts = json.loads(artifact(CONTACTS).read_text(encoding="utf-8"))
    profile = json.loads(artifact(PROPERTY).read_text(encoding="utf-8"))
    workflows = _load_jsonl(artifact(WORKFLOWS))

    def fact_value(entity_id: str, fact_type: str, context: str) -> str | None:
        matches = [fact for fact in approved if fact.get("entity_id") == entity_id
                   and fact.get("fact_type") == fact_type and fact.get("context") == context]
        if len(matches) != 1:
            errors.append(f"expected exactly one approved {entity_id}/{fact_type}/{context} fact, got {len(matches)}")
            return None
        return str(matches[0].get("normalized_value", ""))

    property_address = fact_value("property.furama_resort_danang", "address", "main_property")
    office_address = fact_value("contact.danang_office", "address", "office_address")
    profile_address = ", ".join(str(profile.get("address", {}).get(key, "")) for key in ("street", "ward", "city", "country") if profile.get("address", {}).get(key))
    contact = next((item for item in contacts if item.get("contact_id") == "contact.danang_office"), None)
    if property_address and _normalize_address(profile_address) != _normalize_address(property_address):
        errors.append("property-profile address disagrees with approved main_property address fact")
    if office_address and (contact is None or _normalize_address(str(contact.get("address", ""))) != _normalize_address(office_address)):
        errors.append("contacts Danang office address disagrees with approved office_address fact")
    if profile.get("address", {}).get("district"):
        errors.append("property-profile carries a stale district field not present in the current official address")

    room_service = next((item for item in services if item.get("service_id") == "service.in_room_dining"), None)
    room_extension = fact_value("service.in_room_dining", "extension", "front_desk_contact")
    if room_service is None or str(room_service.get("contact_extension", "")) != str(room_extension or ""):
        errors.append("In-Room Dining service contact extension disagrees with approved Front Desk contact fact")
    room_workflow = next((item for item in workflows if item.get("workflow_id") == "wf_in_room_dining_order"), None)
    if room_workflow is None:
        errors.append("In-Room Dining operational workflow is missing")
    else:
        blob = json.dumps(room_workflow, ensure_ascii=False).casefold()
        if room_extension and f"ext{room_extension}" not in str(room_workflow.get("channel", "")).casefold().replace("_", ""):
            errors.append("In-Room Dining workflow channel is not aligned with the approved guest contact extension")
        if room_extension and not re.search(rf"ext\s*\.?\s*{re.escape(str(room_extension))}\b", blob, re.I):
            errors.append("In-Room Dining workflow does not expose the approved guest fallback extension")
        if room_extension != "12" and re.search(r"ext\s*\.?\s*12\b|ext12", blob, re.I):
            errors.append("In-Room Dining workflow still exposes stale Ext 12")
    return errors

def validate_dataset(root: Path = dataset_root()) -> dict[str, int]:
    facts = _load_jsonl(dataset_path(FACTS, root))
    errors = validate_facts(facts)
    errors.extend(validate_cross_artifact_invariants(root))
    if errors:
        raise ValueError("Semantic validation failed:\n- " + "\n- ".join(errors))
    manifest = verify_dataset_manifest(root)
    return {"facts_checked": len(facts), "semantic_errors": 0, **manifest}


if __name__ == "__main__":
    result = validate_dataset()
    print(json.dumps({"status": "ok", **result}, sort_keys=True))
