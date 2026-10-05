"""Synchronize generated key-fact lines in approved Furama Markdown from canonical facts.

This keeps the RAG source aligned with the canonical fact layer after data fixes.
It only rewrites the generated ``## Key Specifications & Facts`` block and
leaves editorial/source description text untouched.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import FACTS, dataset_path

FACTS = dataset_path(FACTS)
KNOWLEDGE = Path("knowledge/approved/furama")


def _facts_by_entity() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for line in FACTS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            fact = json.loads(line)
            if fact.get("publication_status", "approved") != "approved":
                continue
            out[fact["entity_id"]].append(fact)
    return out


def _label(context: str) -> str:
    return " ".join(part.capitalize() for part in context.replace("-", "_").split("_") if part)


def _line(fact: dict) -> str | None:
    kind = fact.get("fact_type")
    context = str(fact.get("context") or kind or "fact")
    value = fact.get("normalized_value")
    if kind == "opening_hours" and isinstance(value, dict):
        return f"- **Operating Hours**: {value.get('start')} - {value.get('end')} ({_label(context)})"
    if kind == "phone":
        return f"- **Contact Number**: {value} ({context})"
    if kind == "extension":
        return f"- **Internal Telephone Extension**: **Ext {value}** ({context})"
    if kind == "price_vnd":
        shown = f"{value:,.0f} VND" if isinstance(value, (int, float)) else str(value)
        tax_basis = str(fact.get("tax_basis") or "")
        if tax_basis == "++":
            shown += "++"
        elif tax_basis == "net":
            shown += " net"
        elif tax_basis == "inclusive":
            shown += " inclusive"
        if fact.get("price_basis") == "per_guest":
            shown += " / guest"
        return f"- **Price**: {shown} ({_label(context)})"
    if kind in {"policy_rule", "service_feature", "amenity_feature", "cuisine_type", "capacity", "distance_km", "travel_time_min", "activity_schedule", "service_window"}:
        return f"- **{_label(kind)}**: {value} ({_label(context)})"
    return None


def sync() -> dict[str, int]:
    facts = _facts_by_entity()
    changed = 0
    checked = 0
    for path in sorted(KNOWLEDGE.rglob("*.md")):
        raw = path.read_text(encoding="utf-8")
        match = re.search(r'^entity_id:\s*["\']?([^"\'\n]+)', raw, re.M)
        if not match:
            continue
        entity_id = match.group(1).strip()
        generated = [line for fact in facts.get(entity_id, []) if (line := _line(fact))]
        if not generated or "## Key Specifications & Facts" not in raw:
            continue
        checked += 1
        block = "## Key Specifications & Facts\n" + "\n".join(generated) + "\n"
        updated = re.sub(
            r"## Key Specifications & Facts\n.*?(?=\n## |\Z)",
            block.rstrip("\n"),
            raw,
            flags=re.S,
        )
        if updated != raw:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return {"documents_checked": checked, "documents_changed": changed}


if __name__ == "__main__":
    print(json.dumps(sync(), sort_keys=True))
