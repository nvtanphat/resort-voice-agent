"""Benchmark semantic dense retrieval against the canonical multilingual holdout.

Unlike ``evaluate_vector_backends.py``, this command embeds the holdout query
text with the operator-pinned BGE-M3 model and queries the actual FAISS
index.  It measures dense retrieval only: hybrid fusion, reranking, citation
binding and answer faithfulness remain separate gates.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.rag.embedding.ollama import OllamaEmbedder  # noqa: E402
from concierge_kiosk.core.dataset_layout import dataset_path  # noqa: E402
from concierge_kiosk.persistence.sqlite_store import Store  # noqa: E402
from concierge_kiosk.rag.vectorstore import open_vector_store  # noqa: E402
from tools.evaluation.run_retrieval_eval import SUITES  # noqa: E402
from tools.maintenance.rebuild_vector_index import build  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return round(ordered[index], 3)


def _load_cases(suite: str, limit: int | None) -> tuple[list[dict], str]:
    path = dataset_path(SUITES[suite], root=ROOT)
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return (cases[:limit] if limit else cases), _sha256(path)


def evaluate(*, db: Path, property_id: str, vector_path: Path,
             model: str = "bge-m3", manifest: Path = ROOT / "models/embeddings/bge-m3.ollama.manifest.json",
             suite: str = "grounded", limit: int | None = None,
             effective_on: str = "2026-10-06", base_url: str = "http://127.0.0.1:11434",
             timeout: float = 30.0) -> dict:
    if suite not in SUITES:
        raise ValueError("Invalid semantic vector benchmark options")
    cases, dataset_sha256 = _load_cases(suite, limit)
    if not cases:
        raise ValueError("Semantic benchmark dataset is empty")

    # Rebuild the candidate index from SQLite before measuring.  SQLite remains
    # the source of truth and this makes the report reproducible after an OTA.
    build_report = build(db=db, property_id=property_id,
                         path=vector_path, collection=f"{property_id}-knowledge")
    embedder = OllamaEmbedder(model=model, base_url=base_url, timeout=timeout,
                              manifest_path=str(manifest))
    store = Store(db)
    with store.connection() as con:
        rows = con.execute(
            "SELECT id,revision,canonical_fact_id FROM knowledge WHERE property_id=?",
            (property_id,),
        ).fetchall()
    fact_by_key = {f"{row['id']}::{row['revision']}": str(row['canonical_fact_id'] or "")
                   for row in rows}
    vector_store = open_vector_store(path=vector_path,
                                     collection=f"{property_id}-knowledge")
    try:
        # Warm the model and network path separately from the measured holdout.
        warmup = [case["query"] for case in cases[:min(4, len(cases))]]
        for query in warmup:
            embedder.encode_query(query)
        language_totals: dict[str, dict[str, float]] = defaultdict(
            lambda: {"cases": 0, "r1": 0, "r3": 0, "r5": 0, "mrr": 0.0})
        embedding_ms: list[float] = []
        vector_ms: list[float] = []
        failures: list[dict] = []
        for case in cases:
            language = str(case["language"])
            expected = {str(value) for value in (case.get("expected_fact_ids")
                                                  or [case.get("expected_fact_id")]) if value}
            started = time.perf_counter()
            query_vector = embedder.encode_query(str(case["query"]))
            encoded_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            matches = vector_store.query(
                query_vector, k=5,
                filters={"property_id": property_id, "language": language,
                         "classification": "public", "active": 1,
                         "embedding_model": embedder.model_name,
                         "effective_on": effective_on},
            )
            queried_ms = (time.perf_counter() - started) * 1000
            embedding_ms.append(encoded_ms)
            vector_ms.append(queried_ms)
            found = [fact_by_key.get(match.key, "") for match in matches]
            ranks = [index for index, fact in enumerate(found, 1) if fact in expected]
            first = ranks[0] if ranks else None
            totals = language_totals[language]
            totals["cases"] += 1
            totals["r1"] += int(first == 1)
            totals["r3"] += int(first is not None and first <= 3)
            totals["r5"] += int(first is not None and first <= 5)
            totals["mrr"] += 1 / first if first else 0
            if first is None or first > 1:
                failures.append({
                    "case_id": case.get("case_id"), "language": language,
                    "query": case["query"], "expected_fact_ids": sorted(expected),
                    "ranks": ranks,
                    "top5_fact_ids": found,
                    "top5_keys": [match.key for match in matches],
                })
        per_language = {}
        for language, totals in sorted(language_totals.items()):
            count = totals["cases"]
            per_language[language] = {
                "cases": int(count),
                "r_at_1": round(totals["r1"] / count, 4),
                "r_at_3": round(totals["r3"] / count, 4),
                "r_at_5": round(totals["r5"] / count, 4),
                "mrr_at_5": round(totals["mrr"] / count, 4),
            }
        count = len(cases)
        aggregate = {
            "r_at_1": round(sum(item["r1"] for item in language_totals.values()) / count, 4),
            "r_at_3": round(sum(item["r3"] for item in language_totals.values()) / count, 4),
            "r_at_5": round(sum(item["r5"] for item in language_totals.values()) / count, 4),
            "mrr_at_5": round(sum(item["mrr"] for item in language_totals.values()) / count, 4),
        }
        acceptance = {
            "minimum_cases": count >= 80,
            "minimum_cases_per_language": all(item["cases"] >= 20 for item in per_language.values()),
            "recall_at_5_ge_0_90": aggregate["r_at_5"] >= 0.90,
            "mrr_at_5_ge_0_75": aggregate["mrr_at_5"] >= 0.75,
        }
        return {
            "schema_version": 1,
            "type": "vector_backend_semantic_dense_holdout_not_full_grounding_gate",
            "property_id": property_id, "backend": "faiss",
            "vector_path": str(vector_path), "suite": suite,
            "dataset_sha256": dataset_sha256, "cases": count,
            "embedding_model": embedder.model_name,
            "embedding_manifest_sha256": _sha256(manifest),
            "effective_on": effective_on,
            "warmup_cases": len(warmup),
            "aggregate": aggregate, "per_language": per_language,
            "latency_ms": {
                "embedding_p50": _percentile(embedding_ms, 0.50),
                "embedding_p95": _percentile(embedding_ms, 0.95),
                "vector_query_p50": _percentile(vector_ms, 0.50),
                "vector_query_p95": _percentile(vector_ms, 0.95),
            },
            "acceptance": acceptance,
            "passed": all(acceptance.values()),
            "failures": failures[:100],
            "failure_count": len(failures),
            "index_build": build_report,
            "release_gate": False,
            "release_gate_note": "Dense semantic retrieval is not full hybrid grounding/answer evidence.",
        }
    finally:
        vector_store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/concierge.sqlite3"))
    parser.add_argument("--property-id", required=True)
    parser.add_argument("--vector-path", type=Path, required=True)
    parser.add_argument("--suite", choices=sorted(SUITES), default="grounded")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model", default="bge-m3")
    parser.add_argument("--manifest", type=Path, default=ROOT / "models/embeddings/bge-m3.ollama.manifest.json")
    parser.add_argument("--effective-on", default="2026-10-06")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(
        db=args.db, property_id=args.property_id,
        vector_path=args.vector_path, suite=args.suite, limit=args.limit,
        model=args.model, manifest=args.manifest, effective_on=args.effective_on,
        base_url=args.base_url, timeout=args.timeout,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "backend", "cases", "aggregate", "per_language", "latency_ms", "passed", "release_gate")},
        ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
