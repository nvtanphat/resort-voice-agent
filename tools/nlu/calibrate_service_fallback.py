"""Calibrate the model-free service fallback on the training split.

Leave-one-group-out over the reviewed train examples: every example is
classified by its nearest examples from *other* situations (paraphrases that
share a frame/concept are held out together, so this measures unseen wording) with the runtime ``nearest_label`` rule, and a
grid of ``fallback_min_score``/``fallback_min_margin`` is scored for precision
(accepted labels that are right) and coverage (examples that get a label).
The recommendation is the highest-coverage pair whose precision meets
``--target-precision``. Evaluation data is never read. Use the same learned
embedder as the runtime; thresholds from the hash embedder are meaningless.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from concierge_kiosk.agent.understanding.service_selector import (  # noqa: E402
    load_command_examples,
    nearest_label,
)
from concierge_kiosk.core.dataset_layout import (  # noqa: E402
    TRAIN_AGENT_MULTILINGUAL,
    TRAIN_AGENT_VI_GOLD,
    dataset_path,
)
from concierge_kiosk.rag.embedding.base import cosine  # noqa: E402
from concierge_kiosk.rag.embedding.local import LocalEmbedder  # noqa: E402


def calibrate(args: argparse.Namespace) -> dict:
    examples = load_command_examples([dataset_path(TRAIN_AGENT_VI_GOLD),
                                      dataset_path(TRAIN_AGENT_MULTILINGUAL)])
    embedder = LocalEmbedder(args.embedding_model, str(args.embedding_manifest))
    vectors = [embedder.encode_query(example.utterance) for example in examples]
    predictions = []
    for index, (example, vector) in enumerate(zip(examples, vectors)):
        scored = sorted(((cosine(vector, other), examples[j])
                         for j, other in enumerate(vectors)
                         if j != index and (example.group is None
                                            or examples[j].group != example.group)),
                        key=lambda item: -item[0])
        label, best, runner_up = nearest_label(scored)
        predictions.append((example.goal, label, best, best - runner_up))

    grid = []
    for score_step in range(40, 96, 2):
        min_score = score_step / 100
        for margin_step in range(0, 16):
            min_margin = margin_step / 100
            accepted = [(truth, label) for truth, label, best, margin in predictions
                        if best >= min_score and margin >= min_margin]
            # A knowledge label means "no service": the fallback only acts on
            # service labels, so precision is measured over those.
            acted = [(truth, label) for truth, label in accepted if label is not None]
            correct = sum(1 for truth, label in acted if truth == label)
            services = sum(1 for truth, *_ in predictions if truth is not None)
            grid.append({
                "min_score": min_score, "min_margin": min_margin,
                "acted": len(acted),
                "precision": round(correct / len(acted), 4) if acted else None,
                "service_recall": round(correct / services, 4) if services else None,
            })
    eligible = [row for row in grid
                if row["precision"] is not None and row["precision"] >= args.target_precision]
    best = max(eligible, key=lambda row: (row["service_recall"], -row["min_score"]), default=None)
    return {
        "config": {"examples": len(examples), "embedding_model": args.embedding_model,
                   "target_precision": args.target_precision},
        "recommended": best,
        "grid": grid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--embedding-model", default="ollama://bge-m3")
    parser.add_argument("--embedding-manifest", type=Path,
                        default=ROOT / "models/embeddings/bge-m3.ollama.manifest.json")
    parser.add_argument("--target-precision", type=float, default=0.98)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = calibrate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"config": report["config"], "recommended": report["recommended"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
