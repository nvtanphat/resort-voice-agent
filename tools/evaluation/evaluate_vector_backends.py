"""Measure local vector-store persistence/query latency with transparent scope.

The default workload is a self-retrieval storage smoke: the query vector is
the already-approved vector from the SQLite source row.  This is useful for
R@k/index/filter/latency plumbing, but it is explicitly *not* a semantic RAG
quality or hotel release benchmark.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.embedding.cache import decoded_embedding
from concierge_kiosk.rag.vectorstore import open_vector_store


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 3)


def evaluate(*, db: Path, property_id: str, backend: str, vector_path: Path,
             sample_per_language: int = 20, effective_on: str = '2026-10-05') -> dict:
    if backend not in {'faiss', 'chroma'} or not 1 <= sample_per_language <= 1000:
        raise ValueError('Invalid vector benchmark options')
    store = Store(db)
    with store.connection() as con:
        rows = con.execute(
            'SELECT id,revision,language,embedding,effective_from,effective_to FROM knowledge '
            'WHERE property_id=? AND embedding IS NOT NULL ORDER BY language,id',
            (property_id,),
        ).fetchall()
    selected = []
    counts: dict[str, int] = {}
    for row in rows:
        language = str(row['language'])
        if counts.get(language, 0) >= sample_per_language:
            continue
        counts[language] = counts.get(language, 0) + 1
        selected.append(row)
    vector_store = open_vector_store(
        backend=backend, path=vector_path, collection=f'{property_id}-knowledge')
    try:
        latencies: list[float] = []
        hits_1 = hits_5 = 0
        for row in selected:
            started = time.perf_counter()
            matches = vector_store.query(
                decoded_embedding(row['embedding']), k=5,
                filters={'property_id': property_id, 'language': row['language'],
                         'active': 1, 'effective_on': effective_on})
            latencies.append((time.perf_counter() - started) * 1000)
            expected = f"{row['id']}::{row['revision']}"
            keys = [item.key for item in matches]
            hits_1 += int(bool(keys) and keys[0] == expected)
            hits_5 += int(expected in keys)
        cases = len(selected)
        return {
            'schema_version': 1,
            'type': 'vector_backend_storage_spike_self_retrieval_not_rag_certification',
            'property_id': property_id, 'backend': backend,
            'vector_path': str(vector_path), 'cases': cases,
            'by_language': counts, 'r_at_1': round(hits_1 / cases, 4) if cases else None,
            'r_at_5': round(hits_5 / cases, 4) if cases else None,
            'latency_ms_p95': _p95(latencies),
            'latency_ms_max': round(max(latencies), 3) if latencies else None,
            'index_stats': vector_store.stats(),
            'query_mode': 'approved_sqlite_embedding_self_retrieval',
            'release_gate': False,
        }
    finally:
        vector_store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('data/concierge.sqlite3'))
    parser.add_argument('--property-id', required=True)
    parser.add_argument('--backend', choices=('faiss', 'chroma'), required=True)
    parser.add_argument('--vector-path', type=Path, required=True)
    parser.add_argument('--sample-per-language', type=int, default=20)
    parser.add_argument('--effective-on', default='2026-10-05')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = evaluate(db=args.db, property_id=args.property_id, backend=args.backend,
                      vector_path=args.vector_path, sample_per_language=args.sample_per_language,
                      effective_on=args.effective_on)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
