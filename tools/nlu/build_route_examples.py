"""Build a pinned, reviewed-example route corpus from human-review gold data."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import (
    TRAIN_AGENT_MULTILINGUAL,
    TRAIN_AGENT_VI_GOLD,
    dataset_path,
)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _split(concept: str) -> str:
    # Keep complete concepts in one split; this prevents paraphrases of the
    # same reviewed scenario from making calibration look better than it is.
    digest = hashlib.sha256(concept.encode("utf-8")).digest()
    return "validation" if digest[0] % 5 == 0 else "train"


def build(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for row in rows:
        if row.get("context_requirements") or row.get("expected_route") in {"multi_step", "policy_guard"}:
            continue
        text = str(row.get("utterance") or "").strip()
        route = str(row.get("expected_route") or "").strip()
        language = str(row.get("language") or "").strip()
        if not text or not route or not language:
            continue
        examples.append({
            "example_id": str(row["scenario_id"]),
            "language": language,
            "text": text,
            "route": route,
            "split": _split(str(row.get("concept_key") or row["scenario_id"])),
            "source": "human_review_gold",
            "source_scenario_id": str(row["scenario_id"]),
            "surface_style": row.get("surface_style"),
            "service_code": row.get("service_code"),
        })
    return examples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path,
                        help="Optional single JSONL input; by default both agent training releases are used")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "releases/route-examples.jsonl")
    args = parser.parse_args()
    inputs = [args.input] if args.input else [
        dataset_path(TRAIN_AGENT_VI_GOLD), dataset_path(TRAIN_AGENT_MULTILINGUAL)]
    rows = [json.loads(line) for input_path in inputs
            for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    examples = build(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in examples), encoding="utf-8")
    print(json.dumps({"examples": len(examples), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
