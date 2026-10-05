from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.embedding.cache import query_embedding
from concierge_kiosk.rag.embedding.local import LocalEmbedder
from concierge_kiosk.rag.ingestion import ingest_text


class EmbeddingProfileTests(unittest.TestCase):
    def test_learned_profile_applies_e5_query_and_passage_prefixes(self):
        seen: list[str] = []

        class FakeVector(list):
            def tolist(self):
                return list(self)

        class FakeSentenceTransformer:
            def __init__(self, path, device, local_files_only):
                self.max_seq_length = None

            def encode(self, text, normalize_embeddings):
                seen.append(text)
                return FakeVector([1.0, 0.0, 0.0])

        module = types.ModuleType("sentence_transformers")
        module.SentenceTransformer = FakeSentenceTransformer
        previous = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = module
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "multilingual-e5-small"
                root.mkdir()
                (root / "placeholder.bin").write_bytes(b"model")
                (root / "concierge_embedding.json").write_text(json.dumps({
                    "format": "concierge-embedding-profile",
                    "backend": "sentence-transformers",
                    "query_prefix": "query: ",
                    "passage_prefix": "passage: ",
                    "normalize_embeddings": True,
                    "max_seq_length": 512,
                }), encoding="utf-8")
                embedder = LocalEmbedder(str(root))
                self.assertTrue(embedder.is_learned)
                self.assertEqual(embedder.backend, "sentence-transformers")
                self.assertEqual(query_embedding(embedder, "Where is the spa?"), [1.0, 0.0, 0.0])
                self.assertEqual(embedder.encode_passage("Spa is near the lobby."), [1.0, 0.0, 0.0])
                self.assertEqual(seen, ["query: Where is the spa?", "passage: Spa is near the lobby."])
        finally:
            if previous is None:
                sys.modules.pop("sentence_transformers", None)
            else:
                sys.modules["sentence_transformers"] = previous

    def test_ingestion_uses_passage_encoder_not_query_encoder(self):
        class ProbeEmbedder:
            model_name = "probe"

            def __init__(self):
                self.query_calls = 0
                self.passage_calls = 0

            def encode(self, text):
                raise AssertionError("neutral encoder should not be used")

            def encode_query(self, text):
                self.query_calls += 1
                return [1.0, 0.0]

            def encode_passage(self, text):
                self.passage_calls += 1
                return [0.0, 1.0]

        doc = """---
document_id: test_doc
property_id: TEST_PROPERTY
title: Test Document
language: en
classification: public
effective_from: 2026-01-01
domain: general
---
# Test
This is an approved test passage for embedding behavior.
"""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "db.sqlite3")
            embedder = ProbeEmbedder()
            count = ingest_text(store, doc, property_id="TEST_PROPERTY", embedder=embedder)
            self.assertGreaterEqual(count, 1)
            self.assertEqual(embedder.query_calls, 0)
            self.assertEqual(embedder.passage_calls, count)

    def test_hash_fallback_remains_non_learned(self):
        embedder = LocalEmbedder(
            str(ROOT / "models/embeddings/hash-multilingual"),
            str(ROOT / "models/embeddings/hash-multilingual.manifest.json"),
        )
        self.assertFalse(embedder.is_learned)
        self.assertEqual(embedder.backend, "builtin-hash")
        self.assertEqual(len(embedder.encode_query("hello")), 384)
        self.assertEqual(len(embedder.encode_passage("hello")), 384)


if __name__ == "__main__":
    unittest.main()
