from __future__ import annotations

import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.common import evidence_passage
from concierge_kiosk.rag.ingestion import ingest_text
from concierge_kiosk.rag.citations import bind_citations
from concierge_kiosk.rag.retrieval.engine import retrieve
from concierge_kiosk.rag.retrieval.policy import _policy_conflict
from concierge_kiosk.rag.retrieval.policy import RAGPolicy


def _doc(doc_id: str, title: str, language: str, body: str, *, heading: str = "Information",
         domain: str = "general") -> str:
    return f"""---
document_id: {doc_id}
property_id: TEST_PROPERTY
title: {title}
language: {language}
classification: public
effective_from: 2026-01-01
domain: {domain}
---
# {heading}
{body}
"""


class _CrossLanguageEmbedder:
    model_name = "cross-language-probe"
    is_learned = True

    def encode_passage(self, text: str):
        if "swimming pool" in text.casefold():
            return [1.0, 0.0]
        return [0.0, 1.0]

    def encode_query(self, text: str):
        return [1.0, 0.0]

    def encode(self, text: str):
        return self.encode_passage(text)


class _WeakLearnedEmbedder:
    model_name = "weak-learned-probe"
    is_learned = True

    def encode_passage(self, text: str):
        return [0.65, math.sqrt(1 - 0.65**2)]

    def encode_query(self, text: str):
        return [1.0, 0.0]

    def encode(self, text: str):
        return self.encode_passage(text)


class _WeakHashEmbedder(_WeakLearnedEmbedder):
    model_name = "weak-hash-probe"
    is_learned = False


class _UnavailableReranker:
    def score(self, _query, _bodies, *, max_length=512):
        raise TimeoutError('test reranker timeout')


class RetrievalRecallSafetyTests(unittest.TestCase):
    def _store(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Store(Path(directory.name) / "db.sqlite3")

    def test_guest_numbers_do_not_hard_filter_relevant_policy(self):
        store = self._store()
        ingest_text(
            store,
            _doc("spa_policy", "Spa", "vi", "Dịch vụ spa cần đặt trước.", heading="Spa", domain="spa"),
            property_id="TEST_PROPERTY",
        )
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="vi",
            query="tôi muốn spa cho bé 3 tuổi lúc 3pm", effective_date="2026-10-01",
            mode="lexical",
        )
        self.assertEqual([source["source_id"] for source in result.sources], ["spa_policy"])

    def test_evidence_passage_prefers_numbers_but_does_not_require_them(self):
        passage = evidence_passage("Dịch vụ spa cần đặt trước.", "spa lúc 3pm")
        self.assertEqual(passage, "Dịch vụ spa cần đặt trước.")

    def test_accented_vietnamese_query_keeps_lexical_precision(self):
        store = self._store()
        ingest_text(store, _doc("desk_info", "Bàn làm việc", "vi", "Bàn làm việc có trong phòng.",
                                heading="Bàn làm việc"), property_id="TEST_PROPERTY")
        ingest_text(store, _doc("sale_info", "Bán hàng", "vi", "Bán hàng lưu niệm tại sảnh.",
                                heading="Bán hàng"), property_id="TEST_PROPERTY")
        result = retrieve(store, property_id="TEST_PROPERTY", language="vi", query="bàn làm việc",
                          effective_date="2026-10-01", mode="lexical", top_k=5)
        self.assertEqual([source["source_id"] for source in result.sources], ["desk_info"])

    def test_unaccented_vietnamese_query_keeps_fallback_recall(self):
        store = self._store()
        ingest_text(store, _doc("desk_info", "Bàn làm việc", "vi", "Bàn làm việc có trong phòng.",
                                heading="Bàn làm việc"), property_id="TEST_PROPERTY")
        result = retrieve(store, property_id="TEST_PROPERTY", language="vi", query="ban lam viec",
                          effective_date="2026-10-01", mode="lexical")
        self.assertEqual(result.sources[0]["source_id"], "desk_info")

    def test_long_voice_like_query_needs_only_two_substantive_matches(self):
        store = self._store()
        ingest_text(store, _doc("airport_shuttle", "Airport Shuttle", "en",
                                "The airport shuttle departs from the main lobby.",
                                heading="Airport Shuttle", domain="transportation"),
                    property_id="TEST_PROPERTY")
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="en",
            query="please can you tell me whether the airport shuttle is available sometime this afternoon",
            effective_date="2026-10-01", mode="lexical",
        )
        self.assertEqual(result.sources[0]["source_id"], "airport_shuttle")

    def test_learned_dense_can_fallback_to_english_only_fact(self):
        store = self._store()
        embedder = _CrossLanguageEmbedder()
        ingest_text(store, _doc("pool_info", "Swimming Pool", "en",
                                "The swimming pool is open from 07:00 to 19:00.",
                                heading="Swimming Pool", domain="recreation"),
                    property_id="TEST_PROPERTY", embedder=embedder)
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="vi", query="hồ bơi mở mấy giờ",
            effective_date="2026-10-01", mode="dense", embedder=embedder,
        )
        self.assertEqual(result.mode, "cross_language_dense")
        self.assertEqual(result.sources[0]["source_id"], "pool_info")
        self.assertEqual(result.sources[0]["language"], "en")
        self.assertEqual(result.sources[0]["requested_language"], "vi")

    def test_cross_language_source_can_be_reauthorized_for_citation(self):
        store = self._store()
        embedder = _CrossLanguageEmbedder()
        ingest_text(store, _doc("pool_info", "Swimming Pool", "en",
                                "The swimming pool is open from 07:00 to 19:00.",
                                heading="Swimming Pool", domain="recreation"),
                    property_id="TEST_PROPERTY", embedder=embedder)
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="vi", query="hồ bơi mở mấy giờ",
            effective_date="2026-10-01", mode="dense", embedder=embedder,
        )
        bound = bind_citations(
            store, property_id="TEST_PROPERTY", language="vi", answer=result.answer,
            sources=result.sources, effective_date="2026-10-01",
        )
        self.assertTrue(bound.citations)
        self.assertEqual(bound.citations[0]["language"], "en")
        self.assertEqual(bound.citations[0]["requested_language"], "vi")

    def test_learned_dense_similarity_floor_rejects_weak_match(self):
        store = self._store()
        embedder = _WeakLearnedEmbedder()
        ingest_text(store, _doc("weak_fact", "Pool", "en", "Pool information.", heading="Pool"),
                    property_id="TEST_PROPERTY", embedder=embedder)
        result = retrieve(store, property_id="TEST_PROPERTY", language="en", query="pool",
                          effective_date="2026-10-01", mode="dense", embedder=embedder)
        self.assertEqual(result.sources, [])

    def test_hash_fallback_does_not_pretend_learned_cosine_is_calibrated(self):
        store = self._store()
        embedder = _WeakHashEmbedder()
        ingest_text(store, _doc("pool_hash", "Pool", "en", "Pool information for guests.", heading="Pool"),
                    property_id="TEST_PROPERTY", embedder=embedder)
        result = retrieve(store, property_id="TEST_PROPERTY", language="en", query="pool",
                          effective_date="2026-10-01", mode="dense", embedder=embedder)
        self.assertEqual(result.sources[0]["source_id"], "pool_hash")

    def test_season_qualifiers_prevent_false_policy_conflict(self):
        rows = [
            {"domain": "recreation", "title": "Swimming Pool", "heading": "High season",
             "body": "The swimming pool is open 07:00-21:00."},
            {"domain": "recreation", "title": "Swimming Pool", "heading": "Low season",
             "body": "The swimming pool is open 08:00-20:00."},
        ]
        self.assertFalse(_policy_conflict(rows, "swimming pool open"))

    def test_same_scope_incompatible_numbers_still_conflict(self):
        rows = [
            {"domain": "recreation", "title": "Swimming Pool", "heading": "Opening hours",
             "body": "The swimming pool is open 07:00-21:00."},
            {"domain": "recreation", "title": "Swimming Pool", "heading": "Opening hours",
             "body": "The swimming pool is open 08:00-20:00."},
        ]
        self.assertTrue(_policy_conflict(rows, "swimming pool open"))

    def test_new_fts_schema_preserves_diacritics(self):
        store = self._store()
        with store.connection() as con:
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name='knowledge_fts'").fetchone()[0]
        self.assertIn("remove_diacritics 0", sql)

    def test_reranker_timeout_keeps_nonempty_rrf_evidence(self):
        store = self._store()
        ingest_text(store, _doc("pool_a", "Pool Information", "en",
                                "The pool information is available for guests.", heading="Pool"),
                    property_id="TEST_PROPERTY")
        ingest_text(store, _doc("pool_b", "Pool Information", "en",
                                "The pool information is available at reception.", heading="Pool"),
                    property_id="TEST_PROPERTY")
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="en",
            query="pool information", effective_date="2026-10-01", mode="lexical",
            reranker=_UnavailableReranker(),
            policy=RAGPolicy(rerank_budget_ms=20),
        )
        self.assertTrue(result.sources)
        self.assertEqual(result.mode, "lexical")
        self.assertEqual(result.rerank_status, "timeout")
        self.assertEqual(result.evidence_quality, "verified")

    def test_location_question_cannot_use_extension_or_policy_fact(self):
        store = self._store()
        ingest_text(store, _doc("spa_extension", "Spa", "en",
                                "The spa reception extension is 16.", heading="Spa", domain="spa"),
                    property_id="TEST_PROPERTY")
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="en",
            query="where is the spa", effective_date="2026-10-01", mode="lexical",
        )
        self.assertEqual(result.sources, [])

    def test_location_question_requires_location_evidence(self):
        store = self._store()
        ingest_text(store, _doc("spa_location", "Spa", "en",
                                "The spa is located beside the lobby.", heading="Spa", domain="spa"),
                    property_id="TEST_PROPERTY")
        result = retrieve(
            store, property_id="TEST_PROPERTY", language="en",
            query="where is the spa", effective_date="2026-10-01", mode="lexical",
        )
        self.assertEqual([source["source_id"] for source in result.sources], ["spa_location"])


def test_evidence_passage_abstains_when_no_query_term_occurs():
    assert evidence_passage("- **Số phòng**: 198", "wifi") == ""

if __name__ == "__main__":
    unittest.main()
