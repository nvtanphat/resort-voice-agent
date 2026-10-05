"""Verify the operator-signed production readiness receipt at kiosk startup.

The receipt is created on an operator/admin machine only after a learned embedding
model, independently reviewed RAG benchmark, signed knowledge release and pinned
property artifacts all agree. Runtime verification is read-only and fail-closed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from concierge_kiosk.core.domain_profile import supported_languages

FORMAT = "concierge-production-signoff"


def _sha256(path: str | Path) -> str:
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"Pinned production artifact unavailable: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_receipt_bytes(receipt: dict[str, Any]) -> bytes:
    return json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify_signature(receipt_path: Path, signature_path: Path, public_key_path: Path) -> dict[str, Any]:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    for path, limit in ((receipt_path, 128_000), (signature_path, 4096), (public_key_path, 8192)):
        if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
            raise ValueError("Production sign-off trust material unavailable")
    raw = receipt_path.read_bytes()
    try:
        receipt = json.loads(raw.decode("utf-8"))
        key = serialization.load_pem_public_key(public_key_path.read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Only Ed25519 production sign-off keys are accepted")
        signature = base64.b64decode(signature_path.read_bytes(), validate=True)
        key.verify(signature, canonical_receipt_bytes(receipt))
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("Invalid production sign-off receipt or signature") from exc
    return receipt


def _read_release_evidence(db_path: Path, property_id: str) -> dict[str, Any]:
    if not db_path.is_file() or db_path.is_symlink():
        raise ValueError("Production database is unavailable")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT property_id,release_version,bundle_sha256,chunk_policy_hash,embedding_model_id,"
                "document_count,chunk_count,public_language_count,domain_count,quality_status "
                "FROM knowledge_release_evidence WHERE property_id=?",
                (property_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("Production knowledge release evidence is unreadable") from exc
    if row is None:
        raise ValueError("No production knowledge release evidence is recorded")
    return dict(row)


def _validate_benchmark(benchmark: dict[str, Any]) -> None:
    required_acceptance = {
        "recall_at_5_gte_0_90": True,
        "citation_exactness_eq_1_00": True,
        "unsupported_claim_authorization_eq_1_00": True,
    }
    if (benchmark.get("type") != "operator_attested_field_retrieval_not_hotel_certification"
            or benchmark.get("mode") != "hybrid"
            or benchmark.get("top_k") != 5
            or benchmark.get("passed") is not True
            or benchmark.get("acceptance") != required_acceptance
            or not isinstance(benchmark.get("case_count"), int) or benchmark["case_count"] < 80
            or not isinstance(benchmark.get("recall_at_5"), (int, float)) or benchmark["recall_at_5"] < 0.90
            or not isinstance(benchmark.get("mrr_at_5"), (int, float)) or benchmark["mrr_at_5"] < 0.75
            or benchmark.get("citation_exactness") != 1.0
            or benchmark.get("unsupported_claim_authorization") != 1.0):
        raise ValueError("Signed production receipt contains a benchmark below the release gate")
    per_language = benchmark.get("per_language")
    if (not isinstance(per_language, dict)
            or any(not isinstance(per_language.get(lang), dict)
                   or int(per_language[lang].get("cases", 0)) < 20
                   for lang in supported_languages())):
        raise ValueError("Production benchmark lacks independently reviewed multilingual coverage")


def verify_runtime_signoff(cfg) -> dict[str, Any]:
    """Verify the signed receipt and current immutable release identities.

    Mutable runtime tables are deliberately not required to remain empty after
    deployment; their emptiness is checked and signed when the receipt is created.
    """
    receipt = _verify_signature(
        Path(cfg.production_signoff_path),
        Path(cfg.production_signoff_signature_path),
        Path(cfg.production_signoff_public_key_path),
    )
    if (not isinstance(receipt, dict) or receipt.get("format") != FORMAT
            or receipt.get("property_id") != cfg.property_id
            or receipt.get("fresh_runtime_image") is not True):
        raise ValueError("Production sign-off receipt does not match this property/runtime")

    from concierge_kiosk.rag.model_manifest import (
        learned_embedding_profile,
        manifest_sha256,
        model_identity,
        verify_rag_model_manifest,
    )
    if (not verify_rag_model_manifest(cfg.embedding_model_path, cfg.embedding_manifest_path)
            or not learned_embedding_profile(cfg.embedding_model_path)):
        raise ValueError("Production sign-off requires a pinned learned embedding model")
    embedding = receipt.get("embedding", {})
    current_model_id = model_identity(cfg.embedding_model_path, cfg.embedding_manifest_path)
    if (embedding.get("model_id") != current_model_id
            or embedding.get("manifest_sha256") != manifest_sha256(cfg.embedding_manifest_path)
            or embedding.get("learned") is not True):
        raise ValueError("Learned embedding identity changed after production sign-off")

    immutable = receipt.get("immutable_artifacts", {})
    expected = {
        "map_release_sha256": (cfg.map_release_path, cfg.map_release_sha256),
        "planning_release_sha256": (cfg.planning_release_path, cfg.planning_release_sha256),
        "property_profile_sha256": (cfg.property_profile_path, cfg.property_profile_sha256),
    }
    for key, (path, configured_digest) in expected.items():
        actual = _sha256(path)
        if immutable.get(key) != actual or configured_digest != actual:
            raise ValueError(f"Production artifact changed after sign-off: {key}")

    benchmark = receipt.get("retrieval_benchmark", {})
    _validate_benchmark(benchmark)
    if benchmark.get("embedding_model") != current_model_id:
        raise ValueError("Production benchmark was not run with the active embedding model")

    evidence = _read_release_evidence(Path(cfg.db_path), cfg.property_id)
    signed_evidence = receipt.get("knowledge_release", {})
    comparable = (
        "release_version", "bundle_sha256", "chunk_policy_hash", "embedding_model_id",
        "document_count", "chunk_count", "public_language_count", "domain_count", "quality_status",
    )
    if any(signed_evidence.get(key) != evidence.get(key) for key in comparable):
        raise ValueError("Runtime knowledge release no longer matches production sign-off")
    if (evidence["embedding_model_id"] != current_model_id
            or evidence["quality_status"] != "validated"
            or evidence["public_language_count"] < 4
            or evidence["document_count"] < 1
            or evidence["chunk_count"] < evidence["document_count"]):
        raise ValueError("Runtime knowledge release evidence is not production-grade")
    return receipt
