"""Calibrate the logistic emergency gate on the training split.

Group-held-out cross-validation: situations (normalized ``group``) are split
into folds; each fold is scored by a classifier trained on the other folds, so
the held-out situation (and its translations/paraphrases) is never seen.

Positives are ``emergency`` examples, negatives every other example.  For each
regularization strength and threshold pair the tool reports recall and false
positives per language (Vietnamese first).

Selection criteria (Vietnamese, safety first):
- recall (emergency + emergency_check) >= 0.98
- false positives routed to full ``emergency`` <= 1%
- false positives routed to ``emergency_check`` (one-tap confirmation) <= 15%
Among settings that meet them, the preferred false-alarm budget is 5%: it is
spent on recall by taking the lowest review threshold inside that budget.
If no combination meets all three, the best-recall combination that keeps both
false-positive limits is applied and the report says so.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from concierge_kiosk.agent.understanding.service_selector import (  # noqa: E402
    CommandExample,
    load_configured_examples,
)
from concierge_kiosk.core.dataset_layout import (  # noqa: E402
    dataset_path,
)
from concierge_kiosk.rag.embedding.local import LocalEmbedder  # noqa: E402

FOLDS = 10
L2_GRID = (0.00003, 0.0001, 0.0003, 0.001, 0.003, 0.01, 0.03)
TARGET_RECALL = 0.98
MAX_FULL_FPR = 0.01
MAX_CHECK_FPR = 0.15
PREFERRED_CHECK_FPR = 0.05


def _get_vectors(examples: tuple[CommandExample, ...], model: str, manifest: str,
                 cache_dir: Path | None) -> list[list[float]]:
    cache_path = cache_dir / "emergency_embeddings_dict.json" if cache_dir else None
    cache: dict[str, list[float]] = {}
    if cache_path and cache_path.is_file():
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cache = raw
        except (json.JSONDecodeError, OSError):
            cache = {}
    missing = [ex for ex in examples if ex.utterance not in cache]
    if missing:
        print(f"Encoding {len(missing)} new utterances with {model}...")
        embedder = LocalEmbedder(model, manifest)
        texts = [ex.utterance for ex in missing]
        batch = getattr(embedder, "encode_many", None)
        encoded = list(batch(texts)) if batch is not None else [embedder.encode_query(t) for t in texts]
        for ex, vector in zip(missing, encoded):
            cache[ex.utterance] = vector
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
    return [cache[ex.utterance] for ex in examples]


def _fold_of(group: str) -> int:
    return int(hashlib.sha1(group.encode("utf-8"), usedforsecurity=False).hexdigest(), 16) % FOLDS


def _out_of_fold(vectors: Any, labels: Any, folds: Any, l2: float) -> Any:
    import numpy as np

    from concierge_kiosk.agent.understanding.emergency_gate import LogisticClassifier

    probs = np.zeros(len(labels))
    for fold in range(FOLDS):
        held = folds == fold
        if not held.any():
            continue
        model = LogisticClassifier.fit(vectors[~held], labels[~held], l2=l2)
        probs[held] = [model.probability(v) for v in vectors[held]]
    return probs


def _rates(probs: Any, labels: Any, mask: Any, min_prob: float, review_prob: float) -> dict[str, float]:
    import numpy as np

    pos, neg = mask & (labels == 1), mask & (labels == 0)
    full = probs >= min_prob
    flagged = probs >= review_prob
    n_pos, n_neg = max(int(pos.sum()), 1), max(int(neg.sum()), 1)
    return {
        "recall": float(np.sum(flagged & pos) / n_pos),
        "confident_recall": float(np.sum(full & pos) / n_pos),
        "full_fpr": float(np.sum(full & neg) / n_neg),
        "check_fpr": float(np.sum(flagged & ~full & neg) / n_neg),
        "total_fpr": float(np.sum(flagged & neg) / n_neg),
    }


def calibrate_emergency_gate(
    embedding_model: str = "ollama://bge-m3",
    embedding_manifest: str = str(ROOT / "models/embeddings/bge-m3.ollama.manifest.json"),
    use_cache: bool = True,
    apply_config: bool = True,
    example_statuses: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    import numpy as np

    examples = load_configured_examples(statuses=example_statuses)
    if not examples:
        raise ValueError("No training command examples found")
    cache_dir = Path(".cache") if use_cache else None
    vectors = np.asarray(_get_vectors(examples, embedding_model, embedding_manifest, cache_dir))
    labels = np.asarray([1 if ex.label == "emergency" else 0 for ex in examples])
    languages = np.asarray([ex.language for ex in examples])
    folds = np.asarray([_fold_of(ex.group or f"row-{i}") for i, ex in enumerate(examples)])
    vi = languages == "vi"
    everything = np.ones(len(examples), dtype=bool)
    print(f"examples={len(examples)} emergency={int(labels.sum())} "
          f"vi emergency={int((labels[vi] == 1).sum())} vi other={int((labels[vi] == 0).sum())}")

    min_grid = [round(x / 100, 2) for x in range(50, 100)]
    review_grid = [round(x / 100, 2) for x in range(2, 91)]
    candidates: list[dict[str, Any]] = []
    out_of_fold: dict[float, Any] = {}
    for l2 in L2_GRID:
        probs = _out_of_fold(vectors, labels, folds, l2)
        out_of_fold[l2] = probs
        for review in review_grid:
            for full in min_grid:
                if full < review:
                    continue
                vi_rates = _rates(probs, labels, vi, full, review)
                candidates.append({"l2": l2, "emergency_min_prob": full, "emergency_review_prob": review,
                                   **{f"vi_{k}": v for k, v in vi_rates.items()}})
        print(f"l2={l2}: out-of-fold done")

    limits = [c for c in candidates
              if c["vi_full_fpr"] <= MAX_FULL_FPR and c["vi_check_fpr"] <= MAX_CHECK_FPR]
    meets = [c for c in limits if c["vi_recall"] >= TARGET_RECALL]
    criteria_met = bool(meets)
    pool = meets or limits
    if not pool:
        raise RuntimeError("no threshold keeps the false-positive limits; add reviewed negatives")
    if criteria_met:
        # Spend the one-tap false-alarm budget on recall: among settings that
        # meet the criteria and stay within the preferred check-FPR budget, take
        # the highest recall, then the lowest review threshold (widest safety margin).
        budget = [c for c in pool if c["vi_check_fpr"] <= PREFERRED_CHECK_FPR] or pool
        chosen = max(budget, key=lambda c: (round(c["vi_recall"], 4), c["vi_confident_recall"],
                                            -c["emergency_review_prob"], -c["l2"]))
    else:
        chosen = max(pool, key=lambda c: (c["vi_recall"], c["vi_confident_recall"], -c["vi_total_fpr"]))
    l2 = chosen["l2"]
    full, review = chosen["emergency_min_prob"], chosen["emergency_review_prob"]
    probs = out_of_fold[l2]

    by_language = {}
    for lng in ("vi", "en", "zh", "ko"):
        mask = languages == lng
        by_language[lng] = {
            "pos_count": int((mask & (labels == 1)).sum()),
            "neg_count": int((mask & (labels == 0)).sum()),
            **{k: round(v, 4) for k, v in _rates(probs, labels, mask, full, review).items()},
        }
    overall = {k: round(v, 4) for k, v in _rates(probs, labels, everything, full, review).items()}

    weak = [{"group": examples[i].group, "utterance": examples[i].utterance, "p": round(float(probs[i]), 4)}
            for i in np.flatnonzero(vi & (labels == 1) & (probs < review))]
    false_alarms = [{"utterance": examples[i].utterance, "label": examples[i].label, "p": round(float(probs[i]), 4)}
                    for i in np.flatnonzero(vi & (labels == 0) & (probs >= full))]

    report_path = ROOT / "reports" / "nlu" / "emergency-calibration.json"
    if example_statuses is not None:
        # A comparison run must never replace the record behind the live thresholds.
        report_path = ROOT / "reports" / "nlu" / "v1" / (
            "emergency-calibration-" + "+".join(sorted(example_statuses)).lower() + ".json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    if report_path.is_file():
        try:
            old = json.loads(report_path.read_text(encoding="utf-8"))
            previous = old.get("previous") if "effective" in old else {
                key: value for key, value in old.items() if key != "previous"}
        except (OSError, json.JSONDecodeError):
            previous = None
    report = {
        "method": "logistic regression on bge-m3 embeddings, group-held-out cross-validation",
        "folds": FOLDS,
        "criteria": {"vi_recall": TARGET_RECALL, "vi_full_fpr_max": MAX_FULL_FPR,
                     "vi_check_fpr_max": MAX_CHECK_FPR},
        "criteria_met": criteria_met,
        "dataset": {"examples": len(examples), "emergency": int(labels.sum())},
        "effective": {"emergency_min_prob": full, "emergency_review_prob": review, "emergency_l2": l2},
        "performance_overall": overall,
        "by_language": by_language,
        "weak_vi_situations": {"count": len(weak), "samples": weak[:12]},
        "vi_false_alarms_full": false_alarms[:12],
        "l2_grid": {str(k): {"best_vi_recall_within_fpr_limits": round(max(
            (c["vi_recall"] for c in limits if c["l2"] == k), default=0.0), 4)} for k in L2_GRID},
    }
    if previous:
        report["previous"] = previous
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n--- EMERGENCY GATE (group-held-out) ---")
    for lng, m in by_language.items():
        print(f"[{lng.upper()}] recall={m['recall']*100:.1f}% (confident {m['confident_recall']*100:.1f}%) "
              f"| FPR full={m['full_fpr']*100:.2f}% check={m['check_fpr']*100:.2f}%")
    print(f"chosen l2={l2} min_prob={full} review_prob={review} criteria_met={criteria_met}")

    if apply_config:
        domain_path = ROOT / "config" / "agent-domain.json"
        domain = json.loads(domain_path.read_text(encoding="utf-8"))
        selector = domain["nlu"]["service_selector"]
        for stale in ("emergency_min_score", "emergency_min_margin", "emergency_review_score"):
            selector.pop(stale, None)
        selector["emergency_min_prob"] = full
        selector["emergency_review_prob"] = review
        selector["emergency_l2"] = l2
        domain_path.write_text(json.dumps(domain, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("Updated config/agent-domain.json (run tools/config/repin_configs.py).")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calibrate the logistic emergency gate")
    parser.add_argument("--embedding-model", default="ollama://bge-m3")
    parser.add_argument("--embedding-manifest", default=ROOT / "models/embeddings/bge-m3.ollama.manifest.json")
    parser.add_argument("--no-apply-config", dest="apply_config", action="store_false", default=True)
    parser.add_argument("--no-cache", dest="use_cache", action="store_false", default=True)
    parser.add_argument("--example-statuses", default=None,
                        help="comma-separated gold_status values to train on (default: runtime config); "
                             "combine with --no-apply-config for a comparison run")
    args = parser.parse_args()
    if args.example_statuses is not None and args.apply_config:
        parser.error("--example-statuses changes the example set; pass --no-apply-config as well")
    calibrate_emergency_gate(
        embedding_model=args.embedding_model,
        embedding_manifest=str(args.embedding_manifest),
        use_cache=args.use_cache,
        apply_config=args.apply_config,
        example_statuses=(None if args.example_statuses is None else
                          tuple(s.strip() for s in args.example_statuses.split(",") if s.strip())),
    )
