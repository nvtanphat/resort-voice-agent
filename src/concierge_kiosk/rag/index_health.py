"""Detect a silently disabled dense retrieval path.

Retrieval filters stored vectors with ``embedding_model=?``. If the running
embedder is not the model that built the index, every dense query returns zero
rows and retrieval quietly degrades to lexical-only. This makes that visible.
"""
from __future__ import annotations


def dense_index_status(store, property_id: str, embedder) -> dict:
    with store.connection() as con:
        rows = con.execute(
            "SELECT embedding_model, COUNT(*) AS n FROM knowledge "
            "WHERE property_id=? AND active=1 AND embedding IS NOT NULL GROUP BY embedding_model",
            (property_id,)).fetchall()
    indexed = {str(row['embedding_model']): int(row['n']) for row in rows}
    if embedder is None:
        return {'state': 'lexical_only', 'reason': 'no_embedder', 'indexed_models': sorted(indexed)}
    model = getattr(embedder, 'model_name', '')
    if model in indexed:
        return {'state': 'ok', 'model': model, 'indexed_chunks': indexed[model]}
    return {'state': 'model_mismatch', 'model': model, 'indexed_models': sorted(indexed)}
