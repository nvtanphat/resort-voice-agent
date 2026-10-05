from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.ingestion import ingest_text
from concierge_kiosk.rag.retrieval.engine import retrieve


DOC_ITALIAN = """---
document_id: italian_dining
property_id: TEST_PROPERTY
title: Don Cipriani's
language: en
classification: public
effective_from: 2026-01-01
domain: dining
---
# Don Cipriani's
Traditional Italian cuisine is served for dinner.
"""

DOC_POOL = """---
document_id: pool_info
property_id: TEST_PROPERTY
title: Swimming Pool
language: en
classification: public
effective_from: 2026-01-01
domain: recreation
---
# Swimming Pool
The swimming pool is available for resort guests.
"""


class _SemanticProbeEmbedder:
    model_name = "semantic-probe"
    is_learned = True

    def encode_passage(self, text: str):
        return [1.0, 0.0] if "Italian cuisine" in text else [0.0, 1.0]

    def encode_query(self, text: str):
        return [1.0, 0.0]

    def encode(self, text: str):
        return self.encode_passage(text)


class _HashLikeProbeEmbedder(_SemanticProbeEmbedder):
    model_name = "hash-probe"
    is_learned = False


class SemanticDenseRetrievalTests(unittest.TestCase):
    def _store(self, embedder):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = Store(Path(directory.name) / "db.sqlite3")
        ingest_text(store, DOC_ITALIAN, property_id="TEST_PROPERTY", embedder=embedder)
        ingest_text(store, DOC_POOL, property_id="TEST_PROPERTY", embedder=embedder)
        return store

    def test_learned_dense_can_retrieve_semantic_paraphrase_without_lexical_overlap(self):
        embedder = _SemanticProbeEmbedder()
        result = retrieve(
            self._store(embedder), property_id="TEST_PROPERTY", language="en",
            query="I want pasta tonight, where should I go?", effective_date="2026-10-01",
            mode="dense", top_k=3, embedder=embedder,
        )
        self.assertTrue(result.sources)
        self.assertEqual(result.sources[0]["source_id"], "italian_dining")
        self.assertIn("Italian cuisine", result.sources[0]["content"])

    def test_nonlearned_dense_keeps_conservative_lexical_gate(self):
        embedder = _HashLikeProbeEmbedder()
        result = retrieve(
            self._store(embedder), property_id="TEST_PROPERTY", language="en",
            query="I want pasta tonight, where should I go?", effective_date="2026-10-01",
            mode="dense", top_k=3, embedder=embedder,
        )
        self.assertEqual(result.sources, [])


if __name__ == "__main__":
    unittest.main()
