"""Calibrate the semantic-agreement path of the command gate on the training split.

The gate accepts a model ``StartGoal`` that the reviewed concept lists do not cover
only when the goal is the turn's top-ranked service by embedding similarity
(``ServiceSelector.goal_ranking``) above ``nlu.service_selector.semantic_support_min_score``
and a clause passes the modality guards (``intent_evidence.semantic_agreement``).

Leave-one-group-out over the reviewed train examples (paraphrases of one situation
are held out together, so this measures unseen wording):

* acceptance: share of single-service requests whose own goal ranks first above the
  threshold and whose clause passes the modality guards;
* exposure: share of non-service turns (questions, status, chit-chat, changes, ...)
  that *would* pass if the model wrongly proposed their top-ranked service. This is a
  conditional worst case; guest confirmation still gates every write.

The recommended threshold is the highest one that keeps acceptance at or above
``--target-acceptance`` in every language. Evaluation data is never read; the
config is not modified.

Needs the selector embedding cache (``data/service-selector-cache``), which the
server writes at startup; no model is called when the cache is warm.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from concierge_kiosk.agent.understanding import intent_evidence  # noqa: E402
from concierge_kiosk.agent.understanding.service_selector import (  # noqa: E402
    ServiceSelector,
    load_configured_examples,
)
from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path  # noqa: E402
from concierge_kiosk.core.domain_profile import get_domain_profile, nlu_policy  # noqa: E402
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS  # noqa: E402
from concierge_kiosk.rag.embedding.local import LocalEmbedder  # noqa: E402


def _unit(rows) -> np.ndarray:
    matrix = np.asarray(rows, dtype=np.float32)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def held_out_rankings(selector: ServiceSelector) -> list[dict]:
    """Per example: goal ranking from other situations' examples plus catalog text."""
    examples = selector.examples
    example_vectors = _unit(selector._example_vectors)
    catalog_vectors = _unit(selector._vectors)
    modes = [selector._mode_for_entry(entry, ACTION_REQUEST_KINDS) for entry in selector.entries]
    policy = get_domain_profile().semantic_authorization
    rows = []
    for index, example in enumerate(examples):
        if example.pending_field or example.context_topic:
            continue
        query = example_vectors[index]
        best: dict[str, float] = defaultdict(lambda: -1.0)
        similarities = example_vectors @ query
        for other in np.argsort(-similarities):
            peer = examples[other]
            if (other == index or (peer.group and peer.group == example.group) or peer.goal is None
                    or peer.pending_field or peer.context_topic or peer.goal in best):
                continue
            best[peer.goal] = float(similarities[other])
        for mode, score in zip(modes, catalog_vectors @ query):
            if mode:
                best[mode] = max(best[mode], float(score))
        ranked = sorted(best.items(), key=lambda item: -item[1])
        top_goal, top_score = ranked[0]
        # The modality guards with a ranking that always agrees: what remains is the
        # clause-level check (negation, past, completed, reported, informational).
        intent_evidence.bind_turn_service_ranking([(top_goal, 1.0)])
        modality = intent_evidence.semantic_agreement(top_goal, example.utterance, example.language, policy)
        types = sorted({command.get("type") for command in example.commands})
        rows.append({"language": example.language, "types": types, "goal": example.goal,
                     "top_goal": top_goal, "top_score": top_score, "modality": modality})
    intent_evidence.bind_turn_service_ranking(())
    return rows


def evaluate(rows: list[dict], threshold: float) -> dict:
    positives = [row for row in rows if row["types"] == ["StartGoal"] and row["goal"]]
    negatives = [row for row in rows if "StartGoal" not in row["types"] and "Emergency" not in row["types"]]
    accepted = Counter(); totals = Counter()
    for row in positives:
        totals[row["language"]] += 1
        accepted[row["language"]] += (row["modality"] and row["top_goal"] == row["goal"]
                                      and row["top_score"] >= threshold)
    exposed = [row for row in negatives if row["modality"] and row["top_score"] >= threshold]
    return {
        "threshold": round(threshold, 2),
        "acceptance": sum(accepted.values()) / max(1, len(positives)),
        "acceptance_by_language": {language: accepted[language] / totals[language] for language in sorted(totals)},
        "positives": len(positives),
        "exposure": len(exposed) / max(1, len(negatives)),
        "exposure_by_type": dict(Counter(row["types"][0] for row in exposed).most_common()),
        "negatives": len(negatives),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="ollama://bge-m3")
    parser.add_argument("--manifest", default="models/embeddings/bge-m3.ollama.manifest.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data/service-selector-cache")
    parser.add_argument("--target-acceptance", type=float, default=0.80)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/nlu/semantic-support-calibration.json")
    args = parser.parse_args()

    selector = ServiceSelector(dataset_path(SERVICE_CATALOG), LocalEmbedder(args.model, args.manifest),
                               examples=load_configured_examples(), cache_dir=args.cache_dir)
    selector.warm()  # loads the cache; embeds only when it is missing or stale
    rows = held_out_rankings(selector)
    grid = [evaluate(rows, step / 100) for step in range(50, 96)]
    eligible = [item for item in grid
                if min(item["acceptance_by_language"].values()) >= args.target_acceptance]
    recommended = max(eligible, key=lambda item: item["threshold"]) if eligible else None
    report = {
        "method": "leave-one-situation-out on reviewed train examples",
        "target_acceptance": args.target_acceptance,
        "configured": nlu_policy().service_selector.get("semantic_support_min_score"),
        "recommended": recommended,
        "grid": grid,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"configured": report["configured"], "recommended": recommended}, ensure_ascii=False, indent=2))
    return 0 if recommended else 1


if __name__ == "__main__":
    raise SystemExit(main())
