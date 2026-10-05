from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.packaging.knowledge import package, read_signed_package
from tools.prepare_furama_knowledge_release import prepare_release
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.common import LocalEmbedder
from concierge_kiosk.rag.ingestion import ingest_bundle

ROOT = Path(__file__).resolve().parents[1]


class SignedKnowledgeReleasePipelineTests(unittest.TestCase):
    def test_prepare_sign_verify_and_apply_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prepared = tmp / "prepared"
            result = prepare_release(prepared)
            expected_documents = result["documents"]
            self.assertGreaterEqual(expected_documents, 300)

            private = Ed25519PrivateKey.generate()
            private_path = tmp / "operator-private.pem"
            public_path = tmp / "operator-public.pem"
            private_path.write_bytes(private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            public_path.write_bytes(private.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ))

            archive = tmp / "furama-knowledge.zip"
            manifest_path, signature_path = package(
                prepared, archive, private_path, "FURAMA_DANANG", 1
            )
            manifest, documents = read_signed_package(
                archive, manifest_path, signature_path, public_path,
                property_id="FURAMA_DANANG",
            )
            self.assertEqual(len(documents), expected_documents)

            store = Store(tmp / "release.sqlite3")
            embedder = LocalEmbedder(
                str(ROOT / "models/embeddings/hash-multilingual"),
                str(ROOT / "models/embeddings/hash-multilingual.manifest.json"),
            )
            count = ingest_bundle(
                store,
                documents,
                property_id="FURAMA_DANANG",
                embedder=embedder,
                release_version=manifest["release_version"],
                bundle_sha256=manifest["sha256"],
                signed_chunk_policy_hash=manifest["chunk_policy_hash"],
                effective_date="2026-10-01",
            )
            with store.connection() as con:
                release = con.execute(
                    "SELECT release_version,bundle_sha256 FROM knowledge_releases WHERE property_id=?",
                    ("FURAMA_DANANG",),
                ).fetchone()
                evidence = con.execute(
                    "SELECT document_count,chunk_count,public_language_count,embedding_model_id,quality_status "
                    "FROM knowledge_release_evidence WHERE property_id=?",
                    ("FURAMA_DANANG",),
                ).fetchone()
            self.assertEqual(release["release_version"], 1)
            self.assertEqual(release["bundle_sha256"], manifest["sha256"])
            self.assertEqual(evidence["document_count"], expected_documents)
            self.assertEqual(evidence["chunk_count"], count)
            self.assertEqual(evidence["public_language_count"], 4)
            self.assertTrue(evidence["embedding_model_id"].startswith("hash-multilingual@sha256:"))
            self.assertEqual(evidence["quality_status"], "validated")


if __name__ == "__main__":
    unittest.main()
