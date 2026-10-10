"""Calibrate the semantic-agreement path of the command gate on the training split.

The gate accepts a model ``StartGoal`` that the reviewed concept lists do not cover
only when the embedding ranking of the command's own clause agrees with it
(``intent_evidence.semantic_agreement``): the goal is among the clause's ``top_k``
services, at least ``min_score``, within ``max_gap`` of the best service, and closer
than the nearest reviewed turn that requests no service by ``nonrequest_margin``.
The values live in ``nlu.service_selector.semantic_agreement``.

Paraphrases of one situation share a group; every group is held out in turn, together
with its training spans, so the numbers measure unseen wording:

* recall: share of single-service requests whose own goal the rule accepts;
* non-request acceptance: share of turns that request no service (questions, thanks,
  vague references, changes) whose top-ranked service the rule would accept if the
  model wrongly proposed it - the conditional worst case the gate exists for;
* confusion: share of single-service requests where a neighbouring service among the
  top three would also be accepted if the model proposed it.

The recommended setting has the highest recall with non-request acceptance at most
``--max-nonrequest`` and confusion at most ``--max-confusion``. Evaluation data is
never read; the config is not modified. Needs the selector embedding cache
(``data/service-selector-cache``), written by the server at startup; no model is
called when the cache is warm.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from concierge_kiosk.agent.understanding.service_selector import (  # noqa: E402
    ServiceSelector,
    load_configured_examples,
)
from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path  # noqa: E402
from concierge_kiosk.core.domain_profile import nlu_policy  # noqa: E402
from concierge_kiosk.domain.service_registry import (  # noqa: E402
    ACTION_REQUEST_KINDS,
    SERVICE_DEFINITIONS,
    service_definition,
)
from concierge_kiosk.rag.embedding.local import LocalEmbedder  # noqa: E402


def _unit(rows) -> np.ndarray:
    matrix = np.asarray(rows, dtype=np.float64)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def _kind(example) -> tuple[str, str | None]:
    starts = [command.get("goal") for command in example.commands if command.get("type") == "StartGoal"]
    if example.pending_field or example.context_topic or example.label == "emergency":
        return "skip", None
    if len(starts) == 1 and len(example.commands) == 1:
        return "request", starts[0]
    if not starts:
        return "nonrequest", None
    return "skip", None


def held_out_rows(selector: ServiceSelector) -> list[dict]:
    """Per example: goal scores and nearest non-request similarity, its group held out."""
    kinds = frozenset(ACTION_REQUEST_KINDS)
    examples = selector.examples
    vectors = _unit(selector._ensure_example_index())
    spans = list(selector._goal_sources)
    span_vectors = _unit(selector._ensure_goal_index())
    fixed: dict[str, list] = defaultdict(list)
    for entry, vector in zip(selector.entries, selector._ensure_index()):
        mode = selector._mode_for_entry(entry, kinds)
        if mode is not None and service_definition(mode) is not None:
            fixed[mode].append(vector)
    for (mode, definition), vector in zip(SERVICE_DEFINITIONS.items(), selector._definition_vectors or ()):
        if definition.request_kind in kinds:
            fixed[mode].append(vector)
    fixed_vectors = {mode: _unit(rows) for mode, rows in fixed.items()}
    groups = np.array([example.group or f"#{index}" for index, example in enumerate(examples)])
    labels = [_kind(example) for example in examples]
    requests = np.array([kind == "request" for kind, _ in labels])
    nonrequests = np.array([kind == "nonrequest" for kind, _ in labels])
    goals = np.array([goal or "" for _, goal in labels])
    span_groups = [{groups[i] for i, example in enumerate(examples) if source in example.utterance}
                   for _, source in spans]
    rows = []
    for index, (kind, goal) in enumerate(labels):
        if kind == "skip":
            continue
        query = vectors[index]
        others = groups != groups[index]
        similarities = vectors @ query
        span_scores = span_vectors @ query
        scores = {}
        for mode, matrix in fixed_vectors.items():
            best = float(np.max(matrix @ query))
            mask = others & requests & (goals == mode)
            if mask.any():
                best = max(best, float(np.max(similarities[mask])))
            for (span_goal, _), owners, score in zip(spans, span_groups, span_scores):
                if span_goal == mode and groups[index] not in owners:
                    best = max(best, float(score))
            scores[mode] = best
        mask = others & nonrequests
        rows.append({"kind": kind, "language": examples[index].language, "goal": goal,
                     "ranking": sorted(scores.items(), key=lambda item: -item[1]),
                     "nonrequest": float(np.max(similarities[mask])) if mask.any() else -1.0})
    return rows


def accepts(row: dict, goal: str, rule: dict) -> bool:
    ranking = row["ranking"]
    names = [name for name, _ in ranking]
    score = dict(ranking)[goal]
    return (names.index(goal) < rule["top_k"] and score >= rule["min_score"]
            and score >= ranking[0][1] - rule["max_gap"]
            and score >= row["nonrequest"] + rule["nonrequest_margin"])


def evaluate(rows: list[dict], rule: dict) -> dict:
    requests = [row for row in rows if row["kind"] == "request"]
    nonrequests = [row for row in rows if row["kind"] == "nonrequest"]
    by_language = defaultdict(list)
    for row in requests:
        by_language[row["language"]].append(accepts(row, row["goal"], rule))
    return {
        **rule,
        "recall": sum(accepts(row, row["goal"], rule) for row in requests) / max(1, len(requests)),
        "recall_by_language": {language: sum(hits) / len(hits) for language, hits in sorted(by_language.items())},
        "nonrequest_acceptance": sum(accepts(row, row["ranking"][0][0], rule) for row in nonrequests)
        / max(1, len(nonrequests)),
        "confusion": sum(any(accepts(row, goal, rule) for goal, _ in row["ranking"][:3] if goal != row["goal"])
                         for row in requests) / max(1, len(requests)),
        "requests": len(requests),
        "nonrequests": len(nonrequests),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="ollama://bge-m3")
    parser.add_argument("--manifest", default="models/embeddings/bge-m3.ollama.manifest.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data/service-selector-cache")
    parser.add_argument("--max-nonrequest", type=float, default=0.15)
    parser.add_argument("--max-confusion", type=float, default=0.15)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/nlu/semantic-support-calibration.json")
    args = parser.parse_args()

    selector = ServiceSelector(dataset_path(SERVICE_CATALOG), LocalEmbedder(args.model, args.manifest),
                               examples=load_configured_examples(), cache_dir=args.cache_dir)
    selector.warm()  # loads the cache; embeds only when it is missing or stale
    rows = held_out_rows(selector)
    grid = [evaluate(rows, {"top_k": k, "min_score": tau, "max_gap": gap, "nonrequest_margin": margin})
            for k, tau, gap, margin in itertools.product(
                (1, 2, 3), (0.60, 0.65, 0.70, 0.75), (0.0, 0.03, 0.05, 0.08), (-0.05, -0.02, 0.0, 0.03))]
    eligible = [item for item in grid
                if item["nonrequest_acceptance"] <= args.max_nonrequest and item["confusion"] <= args.max_confusion]
    recommended = max(eligible, key=lambda item: item["recall"]) if eligible else None
    configured = nlu_policy().service_selector.get("semantic_agreement")
    report = {
        "method": "leave-one-situation-out on reviewed train examples, training spans held out with their group",
        "limits": {"max_nonrequest": args.max_nonrequest, "max_confusion": args.max_confusion},
        "configured": evaluate(rows, configured) if configured else None,
        "rank1_at_0.70_baseline": evaluate(rows, {"top_k": 1, "min_score": 0.70, "max_gap": 0.0,
                                                  "nonrequest_margin": -9.0}),
        "recommended": recommended,
        "grid": grid,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("configured", "rank1_at_0.70_baseline", "recommended")},
                     ensure_ascii=False, indent=2))
    return 0 if recommended else 1


if __name__ == "__main__":
    raise SystemExit(main())
