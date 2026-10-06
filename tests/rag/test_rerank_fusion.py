from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.ingestion import ingest_text
from concierge_kiosk.rag.retrieval.engine import retrieve
from concierge_kiosk.rag.retrieval.policy import (
    RAGPolicy,
    _RERANK_SLOT,
    _fuse_rerank,
)


def _doc(doc_id: str, body: str) -> str:
    return f"""---
document_id: {doc_id}
property_id: TEST_PROPERTY
title: Pool information {doc_id}
language: en
classification: public
effective_from: 2026-01-01
domain: recreation
---
# Pool
{body}
"""


class _RecordingReranker:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.bodies: list[str] = []
        self.max_length: int | None = None
        self.failure = failure

    def score(self, _query: str, bodies: list[str], *, max_length: int = 512) -> list[float]:
        self.bodies = list(bodies)
        self.max_length = max_length
        if self.failure is not None:
            raise self.failure
        return [1.0 if "context-b" in body else 0.0 for body in bodies]


class RerankFusionTests(unittest.TestCase):
    def test_fusion_alpha_and_tie_break_are_deterministic(self):
        subset = ["a", "b", "c"]
        rrf = {"a": 0.3, "b": 0.2, "c": 0.1}
        rerank = {"a": 0.1, "b": 0.3, "c": 0.2}
        self.assertEqual(_fuse_rerank(subset, rrf, rerank, set(), alpha=1.0, bonus=0.0), ["a", "b", "c"])
        self.assertEqual(_fuse_rerank(subset, rrf, rerank, set(), alpha=0.0, bonus=0.0), ["b", "c", "a"])
        self.assertEqual(_fuse_rerank(["b", "a"], {"b": 0.2, "a": 0.2},
                                      {"b": 0.4, "a": 0.4}, set(), alpha=0.5, bonus=0.0), ["a", "b"])

    def test_structured_match_receives_metadata_bonus(self):
        ordered = _fuse_rerank(
            ["plain", "structured"], {"plain": 0.2, "structured": 0.1},
            {"plain": 1.0, "structured": 1.01}, {"structured"},
            alpha=0.0, bonus=0.2,
        )
        self.assertEqual(ordered, ["structured", "plain"])

    def _store(self) -> Store:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Store(Path(directory.name) / "db.sqlite3")

    def _ambiguous_store(self) -> Store:
        store = self._store()
        ingest_text(store, _doc("pool-a", "Pool hours are 07:00 to 20:00."), property_id="TEST_PROPERTY")
        ingest_text(store, _doc("pool-b", "Pool hours are available at reception."), property_id="TEST_PROPERTY")
        with store.connection() as con:
            con.execute("UPDATE knowledge SET context_text=? WHERE source=?", ("context-a", "pool-a"))
            con.execute("UPDATE knowledge SET context_text=? WHERE source=?", ("context-b", "pool-b"))
        return store

    def test_context_text_and_max_length_are_sent_to_reranker(self):
        store = self._ambiguous_store()
        reranker = _RecordingReranker()
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="en", query="pool hours",
            effective_date="2026-10-01", mode="lexical", reranker=reranker,
            policy=RAGPolicy(rerank_top_k=2, rerank_max_length=64),
        )
        self.assertEqual(result.rerank_status, "ok")
        self.assertEqual(reranker.max_length, 64)
        self.assertEqual(set(reranker.bodies), {"context-a", "context-b"})
        self.assertEqual(result.sources[0]["source_id"], "pool-b")

    def test_body_input_is_used_when_context_text_is_not_selected(self):
        store = self._ambiguous_store()
        reranker = _RecordingReranker()
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="en", query="pool hours",
            effective_date="2026-10-01", mode="lexical", reranker=reranker,
            policy=RAGPolicy(rerank_top_k=2, rerank_input="body"),
        )
        self.assertEqual(result.rerank_status, "ok")
        self.assertTrue(any("Pool hours" in body for body in reranker.bodies))

    def test_context_input_falls_back_to_body_when_context_is_empty(self):
        store = self._ambiguous_store()
        with store.connection() as con:
            con.execute("UPDATE knowledge SET context_text='' WHERE source='pool-a'")
            con.execute("UPDATE knowledge SET context_text='' WHERE source='pool-b'")
        reranker = _RecordingReranker()
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="en", query="pool hours",
            effective_date="2026-10-01", mode="lexical", reranker=reranker,
            policy=RAGPolicy(rerank_top_k=2, rerank_input="context_text"),
        )
        self.assertEqual(result.rerank_status, "ok")
        self.assertTrue(all("Pool hours" in body for body in reranker.bodies))

    def test_error_keeps_rrf_sources_by_default(self):
        store = self._ambiguous_store()
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="en", query="pool hours",
            effective_date="2026-10-01", mode="lexical",
            reranker=_RecordingReranker(failure=RuntimeError("model failed")),
            policy=RAGPolicy(rerank_top_k=2),
        )
        self.assertTrue(result.sources)
        self.assertEqual(result.rerank_status, "error")

    def test_busy_keeps_rrf_sources(self):
        store = self._ambiguous_store()
        self.assertTrue(_RERANK_SLOT.acquire(blocking=False))
        try:
            result = retrieve(
                store, property_id="TEST_PROPERTY", language="en", query="pool hours",
                effective_date="2026-10-01", mode="lexical",
                reranker=_RecordingReranker(), policy=RAGPolicy(rerank_top_k=2),
            )
        finally:
            _RERANK_SLOT.release()
        self.assertTrue(result.sources)
        self.assertEqual(result.rerank_status, "busy")

    def test_abstain_is_explicit_opt_in(self):
        store = self._ambiguous_store()
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="en", query="pool hours",
            effective_date="2026-10-01", mode="lexical",
            reranker=_RecordingReranker(failure=RuntimeError("model failed")),
            policy=RAGPolicy(rerank_top_k=2, rerank_on_failure="abstain"),
        )
        self.assertEqual(result.sources, [])
        self.assertEqual(result.mode, "rerank_unavailable")
        self.assertEqual(result.rerank_status, "error")


if __name__ == "__main__":
    unittest.main()
