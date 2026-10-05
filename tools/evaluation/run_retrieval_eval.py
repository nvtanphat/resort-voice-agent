"""Retrieval evaluation over datasets/evaluation/retrieval/* (R@1/3/5, MRR, latency).

A hit is the expected ``canonical_fact_id`` among the retrieved sources. Every
miss is written with its top-5 so failures can be triaged by cause.

Usage:
  python tools/evaluation/run_retrieval_eval.py --suite grounded --output reports/retrieval/grounded.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.core.dataset_layout import dataset_path  # noqa: E402
from concierge_kiosk.core.settings import load_settings  # noqa: E402
from concierge_kiosk.persistence.sqlite_store import Store  # noqa: E402
from concierge_kiosk.rag.embedding.local import LocalEmbedder  # noqa: E402
from concierge_kiosk.rag.rerank.local import LocalReranker  # noqa: E402
from concierge_kiosk.rag.retrieval import RAGPolicy, retrieve  # noqa: E402

SUITES = {
    "grounded": "evaluation/retrieval/grounded.jsonl",
    "fact_holdout": "evaluation/retrieval/grounded_fact_holdout.jsonl",
    "compositional": "evaluation/retrieval/compositional.jsonl",
    "seen": "evaluation/retrieval/grounded_seen.jsonl",
}


def _policy(cfg, *, rerank_alpha: float | None = None,
            rerank_top_k: int | None = None,
            rerank_budget_ms: int | None = None) -> RAGPolicy:
    policy = RAGPolicy(
        rrf_k=cfg.rag_rrf_k, min_dense_similarity=cfg.rag_min_dense_similarity,
        lexical_coverage=cfg.rag_lexical_coverage, dense_max_rows=cfg.rag_dense_max_rows,
        dense_budget_ms=cfg.rag_dense_budget_ms, rerank_budget_ms=cfg.rag_rerank_budget_ms,
        rerank_top_k=cfg.rag_rerank_top_k, rerank_max_length=cfg.rag_rerank_max_length,
        rerank_input=cfg.rag_rerank_input, rerank_fusion_alpha=cfg.rag_rerank_fusion_alpha,
        rerank_metadata_bonus=cfg.rag_rerank_metadata_bonus,
        rerank_on_failure=cfg.rag_rerank_on_failure,
    )
    changes = {}
    if rerank_alpha is not None:
        changes['rerank_fusion_alpha'] = rerank_alpha
    if rerank_top_k is not None:
        changes['rerank_top_k'] = rerank_top_k
    if rerank_budget_ms is not None:
        changes['rerank_budget_ms'] = rerank_budget_ms
    return replace(policy, **changes) if changes else policy


def _fact_ids(store: Store, sources: list[dict]) -> list[tuple[str, str, str, str]]:
    out = []
    with store.connection() as con:
        for source in sources:
            row = con.execute("SELECT canonical_fact_id, entity_id, fact_type, fact_context FROM knowledge WHERE id=?",
                              (source.get("chunk_id"),)).fetchone()
            out.append(tuple(row) if row else ("", "", "", ""))
    return out


def run(suite: str, mode: str, rerank: bool, limit: int | None, *,
        rerank_alpha: float | None = None, rerank_top_k: int | None = None,
        rerank_budget_ms: int | None = None) -> dict:
    cfg = load_settings()
    store = Store(Path(cfg.db_path))
    embedder = LocalEmbedder(cfg.embedding_model_path, cfg.embedding_manifest_path) if mode != "lexical" else None
    reranker = LocalReranker(cfg.rerank_model_path, cfg.rerank_manifest_path) if rerank else None
    policy = _policy(cfg, rerank_alpha=rerank_alpha, rerank_top_k=rerank_top_k,
                     rerank_budget_ms=rerank_budget_ms)
    if reranker is not None:
        # Exclude one-time tokenizer/OpenVINO initialization from per-case
        # latency, matching the application bootstrap warm-up contract.
        reranker.score('warmup', ['warmup'], max_length=policy.rerank_max_length)
    path = dataset_path(SUITES[suite])
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit:
        cases = cases[:limit]
    per_lang: dict[str, dict[str, float]] = {}
    latencies, rerank_latencies, misses = [], [], []
    rerank_statuses: dict[str, int] = {}
    case_results: dict[str, dict] = {}
    for case in cases:
        started = time.perf_counter()
        try:
            result = retrieve(store, property_id=cfg.property_id, language=case["language"], query=case["query"],
                              embedder=embedder, reranker=reranker, effective_date=cfg_date(cfg), top_k=5,
                              policy=policy, mode=mode)
            sources, retrieval_mode, rerank_status = list(result.sources), result.mode, result.rerank_status
            if result.rerank_ms is not None:
                rerank_latencies.append(result.rerank_ms)
        except Exception as exc:  # noqa: BLE001 - an exception is a measured failure, not a crash
            sources, retrieval_mode, rerank_status = [], f"error:{type(exc).__name__}", "n/a"
        latencies.append((time.perf_counter() - started) * 1000)
        rerank_statuses[rerank_status] = rerank_statuses.get(rerank_status, 0) + 1
        found = _fact_ids(store, sources)
        expected = case.get("expected_fact_ids") or [case.get("expected_fact_id")]
        ranks = [next((i for i, row in enumerate(found, 1) if row[0] == fid), None) for fid in expected]
        stats = per_lang.setdefault(case["language"], {"n": 0, "r1": 0, "r3": 0, "r5": 0, "mrr": 0.0})
        stats["n"] += 1
        stats["r1"] += all(r == 1 for r in ranks) if len(ranks) == 1 else all(r is not None and r <= len(ranks) for r in ranks)
        stats["r3"] += all(r is not None and r <= 3 for r in ranks)
        stats["r5"] += all(r is not None and r <= 5 for r in ranks)
        stats["mrr"] += sum(1 / r for r in ranks if r) / len(ranks)
        correct = all(r == 1 for r in ranks[:1]) and None not in ranks
        case_id = str(case.get("case_id"))
        case_results[case_id] = {"correct": correct, "ranks": ranks, "rerank_status": rerank_status}
        if not correct:
            misses.append({
                "case_id": case.get("case_id"), "language": case["language"], "query": case["query"],
                "expected_entity_id": case.get("expected_entity_id"), "fact_type": case.get("fact_type"),
                "fact_context": case.get("fact_context"), "expected_rank": ranks, "mode": retrieval_mode,
                "rerank": rerank_status,
                "top5": [{"entity_id": r[1], "fact_type": r[2], "fact_context": r[3],
                          "body": (s.get("content") or "")[:120]} for r, s in zip(found, sources)],
                "failure": _classify(case, found),
            })
    total = {k: sum(v[k] for v in per_lang.values()) for k in ("n", "r1", "r3", "r5", "mrr")}
    summary = {lang: {m: round(v[m] / v["n"], 4) for m in ("r1", "r3", "r5", "mrr")} for lang, v in per_lang.items()}
    summary["ALL"] = {m: round(total[m] / total["n"], 4) for m in ("r1", "r3", "r5", "mrr")}
    summary["latency_ms"] = {"p50": round(statistics.median(latencies)),
                             "p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)])}
    summary["rerank_status"] = rerank_statuses
    if rerank_latencies:
        summary["rerank_latency_ms"] = {
            "p50": round(statistics.median(rerank_latencies)),
            "p95": round(sorted(rerank_latencies)[max(0, int(len(rerank_latencies) * 0.95) - 1)]),
        }
    else:
        summary["rerank_latency_ms"] = {"p50": None, "p95": None}
    causes: dict[str, int] = {}
    for miss in misses:
        causes[miss["failure"]] = causes.get(miss["failure"], 0) + 1
    summary["failure_causes"] = causes
    return {"suite": suite, "mode": mode, "rerank": rerank, "cases": len(cases),
            "policy": {"rerank_fusion_alpha": policy.rerank_fusion_alpha,
                        "rerank_top_k": policy.rerank_top_k,
                        "rerank_budget_ms": policy.rerank_budget_ms},
            "summary": summary, "case_results": case_results, "misses": misses}


def _failed_case_ids(report: dict) -> set[str]:
    details = report.get("case_results") or {}
    if details:
        return {case_id for case_id, result in details.items() if not result.get("correct")}
    return {str(item.get("case_id")) for item in report.get("misses", [])}


def compare_reports(current: dict, reference_path: Path) -> dict[str, list[str]]:
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    before = _failed_case_ids(reference)
    after = _failed_case_ids(current)
    return {
        "fixed": sorted(before - after),
        "broken": sorted(after - before),
        "still_wrong": sorted(before & after),
    }


def cfg_date(cfg) -> str:
    from concierge_kiosk.core.clock import property_today
    return property_today(cfg.property_timezone)


def _classify(case: dict, found: list[tuple[str, str, str, str]]) -> str:
    if not found:
        return "no_result"
    entities = [row[1] for row in found]
    if case.get("expected_entity_id") not in entities:
        return "wrong_entity"
    top = found[0]
    if top[1] != case.get("expected_entity_id"):
        return "right_entity_not_top"
    if top[2] != case.get("fact_type"):
        return "right_entity_wrong_attribute"
    return "right_attribute_wrong_context"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=sorted(SUITES), default="grounded")
    parser.add_argument("--mode", choices=("hybrid", "lexical", "dense"), default="hybrid")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path, help="compare failed case IDs with another report")
    parser.add_argument("--rerank-alpha", type=float)
    parser.add_argument("--rerank-top-k", type=int)
    parser.add_argument("--rerank-budget-ms", type=int)
    args = parser.parse_args()
    report = run(args.suite, args.mode, not args.no_rerank, args.limit,
                 rerank_alpha=args.rerank_alpha, rerank_top_k=args.rerank_top_k,
                 rerank_budget_ms=args.rerank_budget_ms)
    print(json.dumps(report["summary"], ensure_ascii=False))
    if args.compare:
        comparison = compare_reports(report, args.compare)
        print(json.dumps({key: {"count": len(value), "case_ids": value}
                          for key, value in comparison.items()}, ensure_ascii=False))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
