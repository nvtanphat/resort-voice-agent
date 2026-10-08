"""Score semantic routing on a reproducible perturbed review set.

The high-precision fast router intentionally sends ambiguous service language
to understanding as ``knowledge``.  Therefore service cases must be scored at
the same semantic-selector boundary used by the conversation engine, not from
``classify_dialogue`` alone.
"""
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
from concierge_kiosk.agent.understanding.service_selector import ServiceSelector, load_command_examples
from concierge_kiosk.core.dataset_layout import (
    SERVICE_CATALOG,
    TRAIN_AGENT_CANDIDATES,
    TRAIN_AGENT_MULTILINGUAL,
    TRAIN_AGENT_VI_GOLD,
)
from concierge_kiosk.core.domain_profile import nlu_policy
from concierge_kiosk.domain.service_registry import route_branch_for_request_kind, service_definition
from concierge_kiosk.rag.embedding.local import LocalEmbedder
from tools.nlu.perturb import generate_variants


_EXPECTED_BRANCHES = {
    "service": {"service", "handoff"},
    "knowledge": {"knowledge"},
    "knowledge_abstain": {"knowledge"},
    "emergency": {"emergency"},
    "status": {"request_status"},
    "request_change": {"request_change"},
}


def _enabled_request_kinds() -> frozenset[str]:
    profile = json.loads((ROOT / "releases/property-profile.json").read_text(encoding="utf-8"))
    return frozenset(str(item["request_kind"]) for item in profile.get("service_catalog", ())
                     if isinstance(item, dict) and item.get("request_kind"))


def score(rows: list[dict[str, Any]], *, selector: ServiceSelector | None = None) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []
    enabled = _enabled_request_kinds()
    policy = nlu_policy().service_selector
    for row in rows:
        expected = str(row.get("expected_route", ""))
        query = str(row.get("utterance", ""))
        language = str(row.get("language", ""))
        actual = classify_dialogue(query, language).branch
        fallback = None
        if expected == "service" and selector is not None:
            expected_service = str(row.get("service_code") or "")
            candidates, _ = selector.understand(query, language=language,
                                                enabled_request_kinds=enabled)
            candidate_codes = {str(item.get("service_mode") or "") for item in candidates}
            definition = service_definition(expected_service)
            if definition is not None and expected_service in candidate_codes:
                actual = route_branch_for_request_kind(definition.request_kind)
            fallback_commands = selector.fallback_commands(
                query, language=language, enabled_request_kinds=enabled,
                min_score=float(policy["fallback_min_score"]),
                min_margin=float(policy["fallback_min_margin"]))
            fallback = next((str(item.get("goal") or "") for item in (fallback_commands or ())
                             if item.get("type") == "StartGoal"), None)
            counts["service:fallback_answered"] += int(fallback is not None)
            counts["service:fallback_exact"] += int(fallback == expected_service)
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
                             "fallback": fallback,
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
    parser.add_argument("--embedding-model", default="ollama://bge-m3")
    parser.add_argument("--embedding-manifest", type=Path,
                        default=ROOT / "models/embeddings/bge-m3.ollama.manifest.json")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        rows = rows[:args.limit]
    variants = generate_variants(rows, per_case=args.per_case, seed=args.seed,
                                 languages=set(args.languages or ()))
    selector = None
    if any(str(row.get("expected_route")) == "service" for row in variants):
        examples = load_command_examples([
            dataset_path(TRAIN_AGENT_VI_GOLD),
            dataset_path(TRAIN_AGENT_MULTILINGUAL),
            dataset_path(TRAIN_AGENT_CANDIDATES),
        ])
        selector = ServiceSelector(
            dataset_path(SERVICE_CATALOG),
            LocalEmbedder(args.embedding_model, str(args.embedding_manifest)),
            examples=examples)
    report = score(variants, selector=selector)
    report["config"] = {"per_case": args.per_case, "seed": args.seed,
                         "source_rows": len(rows), "variant_rows": len(variants)}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
