"""Calibrate model-free service fallback and fast router thresholds on the training split.

Leave-one-group-out over the reviewed train examples: every example is
classified by its nearest examples from *other* situations (paraphrases that
share a frame/concept are held out together, so this measures unseen wording)
with the runtime ``nearest_label`` rule and ``example_eligible``.

A 2D grid of ``min_score`` (0.50–0.95, step 0.01) and ``min_margin`` (0.00–0.15, step 0.01)
is evaluated to recommend two threshold sets:
1) Fallback: precision >= 0.98 on action turns (StartGoal, Cancel, Modify, CheckAvailability),
   info-to-StartGoal misclassification rate <= 0.5%, maximizing action recall.
2) Layer B Router: precision >= 0.99 on router-governed turns (chitchat:*, slot:*, cancel).
Vietnamese precision must meet the same targets; thresholds are applied to
config/agent-domain.json unless --no-apply-config is given.

Evaluation data is never read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
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
    example_eligible,
    load_configured_examples,
    nearest_label,
)
from concierge_kiosk.core.dataset_layout import (  # noqa: E402
    dataset_path,
)
from concierge_kiosk.rag.embedding.local import LocalEmbedder  # noqa: E402

ACTION_BASE_TYPES = frozenset({"cancel", "modify", "checkavailability"})
INFO_LABELS = frozenset({"askinfo", "navigate", "checkavailability"})


def is_action_label(label: str | None) -> bool:
    if label is None:
        return False
    return label.startswith("service:") or label in ACTION_BASE_TYPES


def router_matches(truth: str, pred: str, pending_field: str | None) -> bool:
    if pred.startswith("chitchat:"):
        return truth.startswith("chitchat:")
    if pending_field and pred == f"slot:{pending_field}":
        return truth == f"slot:{pending_field}"
    if pred == "cancel":
        return truth == "cancel"
    return False


def _get_vectors(examples: tuple[CommandExample, ...], model: str, manifest: str, cache_dir: Path | None) -> list[list[float]]:
    cache_path: Path | None = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        key_src = json.dumps(
            {"model": model, "manifest": manifest, "utterances": [ex.utterance for ex in examples]},
            sort_keys=True, ensure_ascii=False
        )
        digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
        cache_path = cache_dir / f"calibrate_{digest}.json"
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, list) and len(cached) == len(examples):
                    return cached
            except (json.JSONDecodeError, OSError):
                pass

    embedder = LocalEmbedder(model, manifest)
    texts = [example.utterance for example in examples]
    batch = getattr(embedder, "encode_many", None)
    if batch is not None:
        vectors = list(batch(texts))
    else:
        vectors = [embedder.encode_query(t) for t in texts]

    if cache_path is not None:
        try:
            cache_path.write_text(json.dumps(vectors), encoding="utf-8")
        except OSError:
            pass
    return vectors


def calibrate(args: argparse.Namespace) -> dict[str, Any]:
    examples = load_configured_examples()
    if not examples:
        raise ValueError("No training command examples found")

    cache_dir = Path(".cache") if args.use_cache else None
    vectors = _get_vectors(examples, args.embedding_model, str(args.embedding_manifest), cache_dir)

    # Normalize vectors so dot product is cosine
    normed_vectors: list[list[float]] = []
    for v in vectors:
        norm = math.sqrt(sum(x * x for x in v))
        if norm > 0:
            normed_vectors.append([x / norm for x in v])
        else:
            normed_vectors.append(v)

    # Leave-one-group-out predictions
    predictions: list[dict[str, Any]] = []
    total_actions = 0
    total_infos = 0
    total_router_targets = 0

    for i, ex in enumerate(examples):
        vi = normed_vectors[i]
        eligible_candidates = [
            (sum(a * b for a, b in zip(vi, normed_vectors[j])), examples[j])
            for j in range(len(examples))
            if j != i
            and (ex.group is None or examples[j].group != ex.group)
            and example_eligible(examples[j], ex.pending_field)
        ]
        eligible_candidates.sort(key=lambda item: -item[0])
        pred_label, best, runner_up = nearest_label(eligible_candidates)
        margin = best - runner_up if runner_up >= -1.0 else 0.0

        truth = ex.label
        pf = ex.pending_field
        if is_action_label(truth):
            total_actions += 1
        if truth in INFO_LABELS:
            total_infos += 1
        if truth.startswith("chitchat:") or truth == "cancel" or (pf and truth == f"slot:{pf}"):
            total_router_targets += 1

        predictions.append({
            "index": i,
            "truth": truth,
            "pred": pred_label,
            "best": best,
            "margin": margin,
            "language": ex.language,
            "pending_field": pf,
        })

    # Evaluate grids
    fallback_grid: list[dict[str, Any]] = []
    router_grid: list[dict[str, Any]] = []

    for score_step in range(50, 96):
        min_score = round(score_step / 100, 2)
        for margin_step in range(0, 16):
            min_margin = round(margin_step / 100, 2)

            # Fallback evaluation
            fb_acted_action = 0
            fb_correct_action = 0
            fb_misclassified_info = 0

            # Router evaluation
            r_acted = 0
            r_correct = 0

            # Vietnamese-only counters (the priority language selects thresholds)
            fb_vi_acted = fb_vi_correct = r_vi_acted = r_vi_correct = 0

            for p in predictions:
                if p["best"] >= min_score and p["margin"] >= min_margin:
                    pred = p["pred"]
                    if pred is not None:
                        # Fallback action metrics
                        if is_action_label(pred):
                            fb_acted_action += 1
                            if p["language"] == "vi":
                                fb_vi_acted += 1
                            if pred == p["truth"]:
                                fb_correct_action += 1
                                if p["language"] == "vi":
                                    fb_vi_correct += 1
                        if p["truth"] in INFO_LABELS and pred.startswith("service:"):
                            fb_misclassified_info += 1

                        # Router metrics
                        pf = p["pending_field"]
                        r_acts = (
                            pred.startswith("chitchat:")
                            or pred == "cancel"
                            or (pf is not None and pred == f"slot:{pf}")
                        )
                        if r_acts:
                            r_acted += 1
                            if p["language"] == "vi":
                                r_vi_acted += 1
                            if router_matches(p["truth"], pred, pf):
                                r_correct += 1
                                if p["language"] == "vi":
                                    r_vi_correct += 1

            fb_precision = fb_correct_action / fb_acted_action if fb_acted_action > 0 else 1.0
            fb_recall = fb_correct_action / total_actions if total_actions > 0 else 0.0
            info_err_rate = fb_misclassified_info / total_infos if total_infos > 0 else 0.0

            r_precision = r_correct / r_acted if r_acted > 0 else 1.0
            r_recall = r_correct / total_router_targets if total_router_targets > 0 else 0.0

            fb_row = {
                "min_score": min_score,
                "min_margin": min_margin,
                "acted": fb_acted_action,
                "correct": fb_correct_action,
                "precision": round(fb_precision, 4),
                "recall": round(fb_recall, 4),
                "info_misclassified": fb_misclassified_info,
                "info_misclassification_rate": round(info_err_rate, 4),
                "vi_acted": fb_vi_acted,
                "vi_precision": round(fb_vi_correct / fb_vi_acted if fb_vi_acted else 1.0, 4),
            }
            fallback_grid.append(fb_row)

            r_row = {
                "min_score": min_score,
                "min_margin": min_margin,
                "acted": r_acted,
                "correct": r_correct,
                "precision": round(r_precision, 4),
                "recall": round(r_recall, 4),
                "vi_acted": r_vi_acted,
                "vi_precision": round(r_vi_correct / r_vi_acted if r_vi_acted else 1.0, 4),
            }
            router_grid.append(r_row)

    # Select thresholds. Vietnamese precision is a hard requirement; recall is
    # maximized among thresholds that meet it.  If none does, take the most
    # precise Vietnamese setting that still acts on enough turns (reported).
    def pick(rows, target, extra_ok):
        eligible = [row for row in rows if row["precision"] >= target and row["vi_precision"] >= target
                    and row["acted"] > 0 and extra_ok(row)]
        if eligible:
            return max(eligible, key=lambda r: (r["recall"], r["precision"], -r["min_score"], -r["min_margin"])), True
        usable = [row for row in rows if row["vi_acted"] >= 10 and extra_ok(row)] or [row for row in rows if row["acted"] > 0]
        return max(usable, key=lambda r: (r["vi_precision"], r["precision"], r["recall"])), False

    best_fallback, fallback_met = pick(fallback_grid, args.target_precision,
                                       lambda r: r["info_misclassification_rate"] <= 0.005)
    best_router, router_met = pick(router_grid, args.router_precision, lambda r: True)
    best_fallback = dict(best_fallback, criteria_met=fallback_met)
    best_router = dict(best_router, criteria_met=router_met)

    # Compute detailed metrics on recommended thresholds
    fb_s, fb_m = best_fallback["min_score"], best_fallback["min_margin"]
    r_s, r_m = best_router["min_score"], best_router["min_margin"]

    # Breakdown by label and by language
    labels = sorted(set(p["truth"] for p in predictions))
    all_langs = sorted(set(p["language"] for p in predictions))
    languages = ["vi"] + [lng for lng in all_langs if lng != "vi"]

    label_metrics_fb: dict[str, dict[str, Any]] = {}
    for lbl in labels:
        lbl_preds = [p for p in predictions if p["truth"] == lbl]
        acted = [p for p in lbl_preds if p["best"] >= fb_s and p["margin"] >= fb_m and is_action_label(p["pred"])]
        correct = sum(1 for p in acted if p["pred"] == lbl)
        # For precision: all examples predicted as lbl
        pred_as_lbl = [p for p in predictions if p["best"] >= fb_s and p["margin"] >= fb_m and p["pred"] == lbl]
        prec = (sum(1 for p in pred_as_lbl if p["truth"] == lbl) / len(pred_as_lbl)) if pred_as_lbl else 1.0
        rec = (correct / len(lbl_preds)) if lbl_preds else 0.0
        label_metrics_fb[lbl] = {
            "total": len(lbl_preds),
            "acted": len(acted),
            "correct": correct,
            "precision": round(prec, 4),
            "recall": round(rec, 4),
        }

    lang_metrics_fb: dict[str, dict[str, Any]] = {}
    for lng in languages:
        lng_preds = [p for p in predictions if p["language"] == lng and is_action_label(p["truth"])]
        acted = [p for p in lng_preds if p["best"] >= fb_s and p["margin"] >= fb_m and is_action_label(p["pred"])]
        correct = sum(1 for p in acted if p["pred"] == p["truth"])
        all_pred_action_lng = [
            p for p in predictions
            if p["language"] == lng and p["best"] >= fb_s and p["margin"] >= fb_m and is_action_label(p["pred"])
        ]
        prec = (correct / len(all_pred_action_lng)) if all_pred_action_lng else 1.0
        rec = (correct / len(lng_preds)) if lng_preds else 0.0
        lang_metrics_fb[lng] = {
            "total_actions": len(lng_preds),
            "acted": len(acted),
            "correct": correct,
            "precision": round(prec, 4),
            "recall": round(rec, 4),
        }

    lang_metrics_router: dict[str, dict[str, Any]] = {}
    chitchat_by_lang: dict[str, dict[str, Any]] = {}
    for lng in languages:
        lng_r_targets = [
            p for p in predictions
            if p["language"] == lng
            and (p["truth"].startswith("chitchat:") or p["truth"] == "cancel" or (p["pending_field"] and p["truth"] == f"slot:{p['pending_field']}"))
        ]
        acted_r = [
            p for p in predictions
            if p["language"] == lng
            and p["best"] >= r_s and p["margin"] >= r_m
            and p["pred"] is not None
            and (p["pred"].startswith("chitchat:") or p["pred"] == "cancel" or (p["pending_field"] and p["pred"] == f"slot:{p['pending_field']}"))
        ]
        correct_r = sum(1 for p in acted_r if router_matches(p["truth"], p["pred"], p["pending_field"]))
        prec_r = (correct_r / len(acted_r)) if acted_r else 1.0
        rec_r = (correct_r / len(lng_r_targets)) if lng_r_targets else 0.0
        lang_metrics_router[lng] = {
            "total_targets": len(lng_r_targets),
            "acted": len(acted_r),
            "correct": correct_r,
            "precision": round(prec_r, 4),
            "recall": round(rec_r, 4),
        }

        cc_targets = [p for p in predictions if p["language"] == lng and p["truth"].startswith("chitchat:")]
        cc_acted = [
            p for p in cc_targets
            if p["best"] >= r_s and p["margin"] >= r_m
            and p["pred"] is not None
            and p["pred"].startswith("chitchat:")
        ]
        cc_correct = sum(1 for p in cc_acted if p["truth"].startswith("chitchat:"))
        cc_rec = (cc_correct / len(cc_targets)) if cc_targets else 0.0
        chitchat_by_lang[lng] = {
            "total": len(cc_targets),
            "acted": len(cc_acted),
            "correct": cc_correct,
            "recall": round(cc_rec, 4),
        }

    # Confusion matrix top 10
    confusion_counts: Counter[tuple[str, str]] = Counter()
    for p in predictions:
        if p["best"] >= fb_s and p["margin"] >= fb_m and p["pred"] is not None:
            if p["truth"] != p["pred"]:
                confusion_counts[(p["truth"], p["pred"])] += 1

    top_10_confusion = [
        {"truth": pair[0], "predicted": pair[1], "count": count}
        for pair, count in confusion_counts.most_common(10)
    ]

    # Identify low recall labels by language
    low_recall_items: list[dict[str, Any]] = []
    for lbl in labels:
        if not is_action_label(lbl):
            continue
        for lng in languages:
            subset = [p for p in predictions if p["truth"] == lbl and p["language"] == lng]
            if not subset:
                continue
            correct = sum(1 for p in subset if p["best"] >= fb_s and p["margin"] >= fb_m and p["pred"] == lbl)
            recall = correct / len(subset)
            if recall < 0.5:
                low_recall_items.append({
                    "label": lbl,
                    "language": lng,
                    "total": len(subset),
                    "correct": correct,
                    "recall": round(recall, 4),
                })

    return {
        "config": {
            "examples": len(examples),
            "total_actions": total_actions,
            "total_infos": total_infos,
            "total_router_targets": total_router_targets,
            "embedding_model": args.embedding_model,
            "target_precision": args.target_precision,
            "router_precision": args.router_precision,
        },
        "recommended": {
            "fallback": best_fallback,
            "router": best_router,
        },
        "metrics": {
            "fallback_by_label": label_metrics_fb,
            "fallback_by_language": lang_metrics_fb,
            "router_by_language": lang_metrics_router,
            "chitchat_by_language": chitchat_by_lang,
            "top_10_confusion": top_10_confusion,
            "low_recall_action_labels": low_recall_items,
        },
        "grids": {
            "fallback": fallback_grid,
            "router": router_grid,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", "--embedding-model", dest="embedding_model", default="ollama://bge-m3")
    parser.add_argument("--manifest", "--embedding-manifest", dest="embedding_manifest", type=Path,
                        default=ROOT / "models/embeddings/bge-m3.ollama.manifest.json")
    parser.add_argument("--target-precision", type=float, default=0.98)
    parser.add_argument("--router-precision", type=float, default=0.99)
    parser.add_argument("--no-cache", dest="use_cache", action="store_false", default=True)
    parser.add_argument("--no-apply-config", dest="apply_config", action="store_false", default=True)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/nlu/command-calibration.json")
    args = parser.parse_args()

    # Read previous report to preserve
    previous_report: dict[str, Any] | None = None
    if args.output.is_file():
        try:
            old_raw = json.loads(args.output.read_text(encoding="utf-8"))
            if "previous" in old_raw and isinstance(old_raw["previous"], dict):
                previous_report = old_raw["previous"]
            else:
                old_copy = dict(old_raw)
                old_copy.pop("previous", None)
                previous_report = old_copy
        except Exception:
            previous_report = None

    report = calibrate(args)
    if previous_report:
        report["previous"] = previous_report

    # Check criteria:
    # fallback precision >= 0.98 & info_misclassification_rate <= 0.005
    # router precision >= 0.99
    # mandatory for vi
    rec_fb = report["recommended"]["fallback"]
    rec_router = report["recommended"]["router"]
    vi_fb = report["metrics"]["fallback_by_language"].get("vi", {})
    vi_router = report["metrics"]["router_by_language"].get("vi", {})

    fb_ok = (rec_fb.get("precision", 0) >= 0.98
             and rec_fb.get("info_misclassification_rate", 1.0) <= 0.005
             and vi_fb.get("precision", 0) >= 0.98)
    router_ok = (rec_router.get("precision", 0) >= 0.99
                 and vi_router.get("precision", 0) >= 0.99)

    report["criteria"] = {
        "fallback_precision_target_met": fb_ok,
        "router_precision_target_met": router_ok,
        "overall_criteria_met": fb_ok and router_ok,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n--- COMMAND CALIBRATION RESULTS BY LANGUAGE (VI FIRST) ---")
    print("Fallback by language:")
    for lng, m in report["metrics"]["fallback_by_language"].items():
        print(f"  [{lng.upper()}] precision={m['precision']*100:.2f}%, recall={m['recall']*100:.2f}%, acted={m['acted']}/{m['total_actions']}")
    print("Router by language:")
    for lng, m in report["metrics"]["router_by_language"].items():
        print(f"  [{lng.upper()}] precision={m['precision']*100:.2f}%, recall={m['recall']*100:.2f}%, acted={m['acted']}/{m['total_targets']}")

    print(f"\nCRITERIA: Fallback OK={fb_ok} (VI prec={vi_fb.get('precision', 0)*100:.2f}%), Router OK={router_ok} (VI prec={vi_router.get('precision', 0)*100:.2f}%)")
    if not (fb_ok and router_ok):
        print("=> Không có ngưỡng đạt đủ tiêu chí; đã chọn ngưỡng có precision tiếng Việt cao nhất (xem criteria trong báo cáo).")
        print(f"=> Nhãn yếu (top confusion): {report['metrics']['top_10_confusion'][:5]}")

    if args.apply_config:
        domain_path = ROOT / "config" / "agent-domain.json"
        domain = json.loads(domain_path.read_text(encoding="utf-8"))
        selector = domain["nlu"]["service_selector"]
        selector["fallback_min_score"] = rec_fb["min_score"]
        selector["fallback_min_margin"] = rec_fb["min_margin"]
        selector["router_min_score"] = rec_router["min_score"]
        selector["router_min_margin"] = rec_router["min_margin"]
        domain_path.write_text(json.dumps(domain, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("Updated config/agent-domain.json (run tools/config/repin_configs.py).")

    summary = {
        "config": report["config"],
        "recommended": report["recommended"],
        "low_recall_count": len(report["metrics"]["low_recall_action_labels"]),
        "criteria": report["criteria"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
