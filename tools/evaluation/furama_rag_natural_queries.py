"""Evaluate natural multilingual guest paraphrases against the current local RAG index."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.common import LocalEmbedder
from concierge_kiosk.rag.retrieval.engine import retrieve

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = ROOT / "datasets/evaluation/furama-rag-natural-queries.json"
DEFAULT_DB = ROOT / "data/concierge.sqlite3"
DEFAULT_MODEL = ROOT / "models/embeddings/hash-multilingual"
DEFAULT_MANIFEST = ROOT / "models/embeddings/hash-multilingual.manifest.json"


def evaluate(cases_path: Path, db_path: Path, model_path: Path, manifest_path: Path,
             *, top_k: int = 5, mode: str = "hybrid") -> dict:
    spec = json.loads(cases_path.read_text(encoding="utf-8"))
    store = Store(str(db_path))
    embedder = LocalEmbedder(str(model_path), str(manifest_path))
    rows = []
    reciprocal_rank_sum = 0.0
    hits = 0
    abstentions = 0
    by_language: dict[str, dict[str, int]] = {}
    for case in spec["cases"]:
        result = retrieve(
            store,
            property_id=spec["property_id"],
            language=case["language"],
            query=case["query"],
            effective_date=spec["effective_date"],
            mode=mode,
            top_k=top_k,
            embedder=embedder,
        )
        source_ids = [source["source_id"] for source in result.sources]
        expected = case["expected_source_id"]
        rank = source_ids.index(expected) + 1 if expected in source_ids else None
        hit = rank is not None
        abstained = not result.sources
        hits += int(hit)
        abstentions += int(abstained)
        reciprocal_rank_sum += 1.0 / rank if rank else 0.0
        bucket = by_language.setdefault(case["language"], {"cases": 0, "hits": 0, "abstentions": 0})
        bucket["cases"] += 1
        bucket["hits"] += int(hit)
        bucket["abstentions"] += int(abstained)
        rows.append({
            "id": case["id"],
            "language": case["language"],
            "expected_source_id": expected,
            "rank": rank,
            "retrieved_source_ids": source_ids,
            "retrieval_mode": result.mode,
        })
    count = len(rows)
    for bucket in by_language.values():
        bucket["recall_at_k"] = round(bucket["hits"] / bucket["cases"], 4)
        bucket["abstain_rate"] = round(bucket["abstentions"] / bucket["cases"], 4)
    return {
        "dataset_id": spec["dataset_id"],
        "embedding_model": embedder.model_name,
        "embedding_is_learned": bool(getattr(embedder, "is_learned", False)),
        "mode": mode,
        "top_k": top_k,
        "case_count": count,
        "recall_at_k": round(hits / count, 4) if count else 0.0,
        "mrr_at_k": round(reciprocal_rank_sum / count, 4) if count else 0.0,
        "abstain_rate": round(abstentions / count, 4) if count else 0.0,
        "per_language": by_language,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--mode", choices=("lexical", "dense", "hybrid"), default="hybrid")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--min-recall-at-k", type=float, default=None)
    args = parser.parse_args()
    result = evaluate(args.cases, args.db, args.model, args.manifest, top_k=args.top_k, mode=args.mode)
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if args.min_recall_at_k is not None and result["recall_at_k"] < args.min_recall_at_k:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
