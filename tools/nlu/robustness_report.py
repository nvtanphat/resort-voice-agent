"""Score deterministic routing on a reproducible perturbed review set."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import dataset_path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from concierge_kiosk.agent.understanding.routing import classify_dialogue
from tools.nlu.perturb import generate_variants


_EXPECTED_BRANCHES = {
    "service": {"service", "handoff"},
    "knowledge": {"knowledge"},
    "knowledge_abstain": {"knowledge"},
    "emergency": {"emergency"},
    "status": {"request_status"},
    "request_change": {"request_change"},
}


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []
    for row in rows:
        expected = str(row.get("expected_route", ""))
        actual = classify_dialogue(str(row.get("utterance", "")), str(row.get("language", ""))).branch
        passed = actual in _EXPECTED_BRANCHES.get(expected, {expected})
        counts["total"] += 1
        counts["passed"] += int(passed)
        counts[f"{row.get('language')}:total"] += 1
        counts[f"{row.get('language')}:passed"] += int(passed)
        transform = str(row.get("transform") or "unknown")
        counts[f"{row.get('language')}:{transform}:total"] += 1
        counts[f"{row.get('language')}:{transform}:passed"] += int(passed)
        if not passed and len(failures) < 100:
            failures.append({"id": row.get("variant_id", row.get("scenario_id")),
                             "language": row.get("language"),
                             "expected": expected, "actual": actual,
                             "transform": row.get("transform"),
                             "utterance": row.get("utterance")})
    return {"counts": dict(counts), "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path,
                        default=dataset_path("evaluation/holdout/service_workflow.jsonl"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--per-case", type=int, default=1)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--language", action="append", dest="languages")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        rows = rows[:args.limit]
    variants = generate_variants(rows, per_case=args.per_case, seed=args.seed,
                                 languages=set(args.languages or ()))
    report = score(variants)
    report["config"] = {"per_case": args.per_case, "seed": args.seed,
                         "source_rows": len(rows), "variant_rows": len(variants)}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
