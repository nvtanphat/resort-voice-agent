"""Validate the canonical reviewed/gold evaluation corpus.

The former generated human-review directory was removed from the dataset
contract.  Review labels now live in ``datasets/evaluation/gold`` and carry an
explicit source family and truth boundary.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "datasets" / "evaluation" / "gold"


def _rows() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(GOLD.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def validate() -> dict[str, int]:
    rows = _rows()
    if len(rows) < 500:
        raise ValueError("gold evaluation corpus is unexpectedly small")
    if len({row["scenario_id"] for row in rows}) != len(rows):
        raise ValueError("gold scenario IDs must be unique")
    if any(row.get("gold_status") != "GOLD" for row in rows):
        raise ValueError("gold corpus contains non-gold rows")
    if any(row.get("language") != "vi" for row in rows):
        raise ValueError("current reviewed gold corpus must declare its language")
    if any(not row.get("utterance", "").strip() or not row.get("source_id") for row in rows):
        raise ValueError("gold rows need source-bound utterances")
    if any({"guest_name", "email", "phone", "passport"}.intersection(row) for row in rows):
        raise ValueError("gold corpus must remain PII-free")
    routes = {row.get("expected_route") for row in rows}
    required = {"service", "knowledge", "knowledge_abstain", "emergency", "privacy_guard"}
    if not required <= routes:
        raise ValueError(f"gold corpus missing route coverage: {sorted(required - routes)}")
    return {"rows": len(rows), "concepts": len({row["concept_key"] for row in rows}),
            "splits": len(Counter(row["split"] for row in rows))}


if __name__ == "__main__":
    print(json.dumps({"status": "ok", **validate()}, ensure_ascii=False, sort_keys=True))
