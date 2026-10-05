"""Calibrate shadow semantic-router thresholds against held-out concepts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import dataset_path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from concierge_kiosk.agent.understanding.semantic_router import SemanticRouter, read_route_examples
from concierge_kiosk.rag.embedding.local import LocalEmbedder


def calibrate(train, validation, embedder, *, max_examples_per_route: int) -> dict:
    grid = [(round(score, 2), round(margin, 2))
            for score in (0.45, 0.50, 0.55, 0.58, 0.62, 0.66, 0.70, 0.75)
            for margin in (0.00, 0.02, 0.04, 0.06, 0.10)]
    probe = SemanticRouter(train, embedder, min_score=0.0, min_margin=0.0,
                           max_examples_per_route=max_examples_per_route)
    scored = [(item, probe.route(item.text, item.language)) for item in validation]
    rows = []
    best = None
    for min_score, min_margin in grid:
        accepted = correct = 0
        for item, decision in scored:
            if decision.route is None or decision.score < min_score or decision.margin < min_margin:
                continue
            accepted += 1
            correct += int(decision.route == item.route)
        coverage = accepted / len(validation) if validation else 0.0
        precision = correct / accepted if accepted else 0.0
        row = {"min_score": min_score, "min_margin": min_margin,
               "accepted": accepted, "coverage": round(coverage, 6),
               "precision": round(precision, 6)}
        rows.append(row)
        # Calibration is a measurement report, not an automatic config edit.
        objective = precision * coverage
        if best is None or objective > best[0]:
            best = (objective, row)
    return {"validation_examples": len(validation), "grid": rows,
            "recommended": best[1] if best else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path,
                        default=ROOT / "releases/route-examples.jsonl")
    parser.add_argument("--model", type=Path, default=ROOT / "models/embeddings/hash-multilingual")
    parser.add_argument("--manifest", type=Path, default=ROOT / "models/embeddings/hash-multilingual.manifest.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-examples-per-route", type=int, default=256)
    args = parser.parse_args()
    train = read_route_examples(args.examples, split="train")
    validation = read_route_examples(args.examples, split="validation")
    embedder = LocalEmbedder(str(args.model), str(args.manifest))
    report = calibrate(train, validation, embedder, max_examples_per_route=args.max_examples_per_route)
    report["model"] = embedder.model_name
    report["train_examples"] = len(train)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
