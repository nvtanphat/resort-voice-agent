from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.ingestion import ingest_bundle
from concierge_kiosk.core.model_manifest import model_identity, rag_model_manifest
from concierge_kiosk.operations.production_signoff import verify_runtime_signoff
from tools.operations.production_signoff import _corpus_digest_from_package, create_signoff
from tools.packaging.knowledge import package, read_signed_package
from tools.knowledge.prepare_release import prepare_release

ROOT = Path(__file__).resolve().parents[2]


class _FakeLearnedEmbedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.is_learned = True

    def encode_passage(self, text: str):
        return [1.0, 0.0, 0.0]

    def encode_query(self, text: str):
        return [1.0, 0.0, 0.0]

    def encode(self, text: str):
        return self.encode_passage(text)


class ProductionSignoffTests(unittest.TestCase):
    def _fixture(self, root: Path):
        private = Ed25519PrivateKey.generate()
        private_path = root / "operator-private.pem"
        public_path = root / "operator-public.pem"
        private_path.write_bytes(private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        public_path.write_bytes(private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ))

        prepared = root / "prepared"
        prepare_release(prepared)
        archive = root / "furama-knowledge.zip"
        manifest_path, knowledge_signature = package(
            prepared, archive, private_path, "FURAMA_DANANG", 1
        )
        manifest, documents = read_signed_package(
            archive, manifest_path, knowledge_signature, public_path,
            property_id="FURAMA_DANANG",
        )

        model = root / "multilingual-e5-small"
        model.mkdir()
        (model / "concierge_embedding.json").write_text(json.dumps({
            "format": "concierge-embedding-profile",
            "backend": "sentence-transformers",
            "query_prefix": "query: ",
            "passage_prefix": "passage: ",
            "normalize_embeddings": True,
            "max_seq_length": 512,
            "embedding_dimension": 384,
        }), encoding="utf-8")
        (model / "model.safetensors").write_bytes(b"test-learned-model-bytes")
        model_manifest = root / "multilingual-e5-small.manifest.json"
        model_manifest.write_text(
            json.dumps(rag_model_manifest(str(model)), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        model_id = model_identity(str(model), str(model_manifest))

        store = Store(root / "runtime.sqlite3")
        ingest_bundle(
            store, documents, property_id="FURAMA_DANANG",
            embedder=_FakeLearnedEmbedder(model_id),
            release_version=manifest["release_version"],
            bundle_sha256=manifest["sha256"],
            signed_chunk_policy_hash=manifest["chunk_policy_hash"],
            effective_date="2026-10-01",
        )

        corpus_digest = _corpus_digest_from_package(documents)
        benchmark = root / "hotel-rag.json"
        benchmark.write_text(json.dumps({
            "type": "operator_attested_field_retrieval_not_hotel_certification",
            "knowledge_release_sha256": corpus_digest,
            "mode": "hybrid",
            "top_k": 5,
            "case_count": 80,
            "recall_at_5": 0.95,
            "mrr_at_5": 0.86,
            "embedding_model": model_id,
            "citation_exactness": 1.0,
            "unsupported_claim_authorization": 1.0,
            "acceptance": {
                "recall_at_5_gte_0_90": True,
                "citation_exactness_eq_1_00": True,
                "unsupported_claim_authorization_eq_1_00": True,
            },
            "passed": True,
            "per_language": {lang: {"cases": 20} for lang in ("vi", "en", "zh", "ko")},
        }, sort_keys=True), encoding="utf-8")

        return {
            "private": private_path, "public": public_path,
            "archive": archive, "knowledge_manifest": manifest_path,
            "knowledge_signature": knowledge_signature,
            "model": model, "model_manifest": model_manifest,
            "model_id": model_id, "db": root / "runtime.sqlite3",
            "benchmark": benchmark,
        }

    def test_signed_gate_verifies_current_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fx = self._fixture(root)
            receipt = root / "production-signoff.json"
            signature = root / "production-signoff.sig"
            result = create_signoff(
                property_id="FURAMA_DANANG", db_path=fx["db"],
                embedding_model=fx["model"], embedding_manifest=fx["model_manifest"],
                benchmark_report=fx["benchmark"],
                knowledge_archive=fx["archive"], knowledge_manifest=fx["knowledge_manifest"],
                knowledge_signature=fx["knowledge_signature"], knowledge_public_key=fx["public"],
                map_release=ROOT / "releases/map-release.json",
                planning_release=ROOT / "releases/planning-release.json",
                property_profile=ROOT / "releases/property-profile.json",
                private_key=fx["private"], receipt_path=receipt, signature_path=signature,
            )
            self.assertTrue(result["fresh_runtime_image"])
            self.assertEqual(result["embedding"]["model_id"], fx["model_id"])

            import hashlib
            sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            cfg = SimpleNamespace(
                property_id="FURAMA_DANANG", db_path=fx["db"],
                embedding_model_path=str(fx["model"]),
                embedding_manifest_path=str(fx["model_manifest"]),
                map_release_path=str(ROOT / "releases/map-release.json"),
                map_release_sha256=sha(ROOT / "releases/map-release.json"),
                planning_release_path=str(ROOT / "releases/planning-release.json"),
                planning_release_sha256=sha(ROOT / "releases/planning-release.json"),
                property_profile_path=str(ROOT / "releases/property-profile.json"),
                property_profile_sha256=sha(ROOT / "releases/property-profile.json"),
                production_signoff_path=str(receipt),
                production_signoff_signature_path=str(signature),
                production_signoff_public_key_path=str(fx["public"]),
            )
            verified = verify_runtime_signoff(cfg)
            self.assertGreaterEqual(verified["knowledge_release"]["document_count"], 300)

    def test_gate_rejects_hash_fallback_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fx = self._fixture(root)
            with Store(fx["db"]).connection(write=True) as con:
                con.execute(
                    "UPDATE knowledge_release_evidence SET embedding_model_id=? WHERE property_id=?",
                    ("hash-multilingual@sha256:deadbeefdeadbeef", "FURAMA_DANANG"),
                )
            with self.assertRaisesRegex(ValueError, "does not match"):
                create_signoff(
                    property_id="FURAMA_DANANG", db_path=fx["db"],
                    embedding_model=fx["model"], embedding_manifest=fx["model_manifest"],
                    benchmark_report=fx["benchmark"], knowledge_archive=fx["archive"],
                    knowledge_manifest=fx["knowledge_manifest"],
                    knowledge_signature=fx["knowledge_signature"], knowledge_public_key=fx["public"],
                    map_release=ROOT / "releases/map-release.json",
                    planning_release=ROOT / "releases/planning-release.json",
                    property_profile=ROOT / "releases/property-profile.json",
                    private_key=fx["private"], receipt_path=root / "receipt.json",
                    signature_path=root / "receipt.sig",
                )

    def test_runtime_rejects_artifact_drift_after_signoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fx = self._fixture(root)
            map_copy = root / "map.json"
            map_copy.write_bytes((ROOT / "releases/map-release.json").read_bytes())
            planning_copy = root / "planning.json"
            planning_copy.write_bytes((ROOT / "releases/planning-release.json").read_bytes())
            profile_copy = root / "property.json"
            profile_copy.write_bytes((ROOT / "releases/property-profile.json").read_bytes())
            receipt = root / "production-signoff.json"
            signature = root / "production-signoff.sig"
            create_signoff(
                property_id="FURAMA_DANANG", db_path=fx["db"],
                embedding_model=fx["model"], embedding_manifest=fx["model_manifest"],
                benchmark_report=fx["benchmark"], knowledge_archive=fx["archive"],
                knowledge_manifest=fx["knowledge_manifest"], knowledge_signature=fx["knowledge_signature"],
                knowledge_public_key=fx["public"], map_release=map_copy,
                planning_release=planning_copy, property_profile=profile_copy,
                private_key=fx["private"], receipt_path=receipt, signature_path=signature,
            )
            import hashlib
            sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            original_map_sha = sha(map_copy)
            cfg = SimpleNamespace(
                property_id="FURAMA_DANANG", db_path=fx["db"],
                embedding_model_path=str(fx["model"]), embedding_manifest_path=str(fx["model_manifest"]),
                map_release_path=str(map_copy), map_release_sha256=original_map_sha,
                planning_release_path=str(planning_copy), planning_release_sha256=sha(planning_copy),
                property_profile_path=str(profile_copy), property_profile_sha256=sha(profile_copy),
                production_signoff_path=str(receipt), production_signoff_signature_path=str(signature),
                production_signoff_public_key_path=str(fx["public"]),
            )
            map_copy.write_bytes(map_copy.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "changed after sign-off"):
                verify_runtime_signoff(cfg)


if __name__ == "__main__":
    unittest.main()
