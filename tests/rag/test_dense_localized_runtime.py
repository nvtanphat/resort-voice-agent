from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DenseLocalizedRuntimeTests(unittest.TestCase):
    def test_all_active_runtime_chunks_have_pinned_dense_vectors(self):
        con = sqlite3.connect(ROOT / "data/concierge.sqlite3")
        try:
            total = con.execute("SELECT COUNT(*) FROM knowledge WHERE property_id=? AND active=1", ("FURAMA_DANANG",)).fetchone()[0]
            embedded = con.execute("SELECT COUNT(*) FROM knowledge WHERE property_id=? AND active=1 AND embedding IS NOT NULL", ("FURAMA_DANANG",)).fetchone()[0]
            models = {row[0] for row in con.execute("SELECT DISTINCT embedding_model FROM knowledge WHERE property_id=? AND active=1", ("FURAMA_DANANG",))}
        finally:
            con.close()
        self.assertGreater(total, 1000)
        self.assertEqual(total, embedded)
        self.assertEqual(len(models), 1)
        self.assertEqual(models, {"ollama:bge-m3"})

    def test_compiled_runtime_has_four_locale_documents_per_source(self):
        compiled = ROOT / "knowledge/compiled/furama"
        files = list(compiled.rglob("*.md"))
        facts = [json.loads(line) for line in (ROOT / "datasets/knowledge/canonical/facts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        entities = {f["entity_id"] for f in json.loads("[" + ",".join(
            line for line in (ROOT / "datasets/knowledge/canonical/entities.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()) + "]") if f.get("publication_status", "approved") == "approved"}
        self.assertEqual(len(files), len(entities) * 4)
        for language in ("en", "vi", "ko", "zh"):
            self.assertEqual(len(list((compiled / language).glob("*.md"))), len(entities))
        self.assertFalse(any(path.name == "son-tra-2.md" for path in files))

    def test_runtime_bodies_are_not_cross_language_clones(self):
        con = sqlite3.connect(ROOT / "data/concierge.sqlite3")
        try:
            grouped = defaultdict(dict)
            for source, language, body in con.execute(
                "SELECT source,language,group_concat(body,'\\n') FROM knowledge WHERE property_id=? AND active=1 GROUP BY source,language",
                ("FURAMA_DANANG",),
            ):
                grouped[source][language] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        finally:
            con.close()
        expected = len(list((ROOT / "knowledge/compiled/furama/en").glob("*.md")))
        self.assertEqual(len(grouped), expected)
        for source, hashes in grouped.items():
            self.assertEqual(set(hashes), {"en", "vi", "ko", "zh"}, source)
            self.assertEqual(len(set(hashes.values())), 4, source)


if __name__ == "__main__":
    unittest.main()
