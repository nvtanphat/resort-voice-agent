"""Create an operator-signed, fail-closed production readiness receipt.

This command runs on an operator/admin machine after the learned multilingual
embedding model is installed and the independently reviewed field RAG benchmark
has passed. The private Ed25519 key must remain outside the kiosk/repository.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from concierge_kiosk.rag.model_manifest import (
    learned_embedding_profile,
    manifest_sha256,
    model_identity,
    verify_rag_model_manifest,
)
from concierge_kiosk.runtime.production_signoff import FORMAT, canonical_receipt_bytes
from tools.packaging.knowledge import read_signed_package

TRANSIENT_TABLES = (
    "sessions", "service_requests", "proposals", "audit_events", "rate_limits",
    "agent_checkpoints", "agent_memory_facts", "autonomous_action_receipts",
    "metric_counts", "read_task_projections", "staff_idempotency", "telemetry_receipts",
)


def _sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Required release artifact unavailable: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _corpus_digest_from_package(documents: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(documents):
        if not name.startswith("knowledge/"):
            raise ValueError("Unexpected packaged knowledge path")
        relative = name[len("knowledge/"):]
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(documents[name].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _read_db(db_path: Path, property_id: str) -> tuple[dict, dict[str, int]]:
    if not db_path.is_file() or db_path.is_symlink():
        raise ValueError("Release database must already exist")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT property_id,release_version,bundle_sha256,chunk_policy_hash,embedding_model_id,"
                "document_count,chunk_count,public_language_count,domain_count,quality_status "
                "FROM knowledge_release_evidence WHERE property_id=?", (property_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Signed knowledge release evidence is missing from the runtime DB")
            existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {table: int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])  # nosec B608  # constant SQL fragments; all values are bound parameters
                      for table in TRANSIENT_TABLES if table in existing}
    except sqlite3.Error as exc:
        raise ValueError("Release database cannot be audited read-only") from exc
    return dict(row), counts


def _validate_benchmark(report: dict, *, model_id: str, corpus_digest: str,
                        min_cases: int, min_per_language: int,
                        min_recall: float, min_mrr: float) -> dict:
    acceptance = report.get("acceptance")
    per_language = report.get("per_language")
    if (report.get("type") != "operator_attested_field_retrieval_not_hotel_certification"
            or report.get("knowledge_release_sha256") != corpus_digest
            or report.get("embedding_model") != model_id
            or report.get("mode") != "hybrid" or report.get("top_k") != 5
            or report.get("passed") is not True
            or not isinstance(acceptance, dict) or not all(acceptance.values())
            or int(report.get("case_count", 0)) < min_cases
            or float(report.get("recall_at_5", 0.0)) < min_recall
            or float(report.get("mrr_at_5", 0.0)) < min_mrr
            or float(report.get("citation_exactness", 0.0)) != 1.0
            or float(report.get("unsupported_claim_authorization", 0.0)) != 1.0
            or not isinstance(per_language, dict)
            or any(int(per_language.get(lang, {}).get("cases", 0)) < min_per_language
                   for lang in ("vi", "en", "zh", "ko"))):
        raise ValueError("Reviewed multilingual RAG benchmark does not satisfy the production gate")
    return {
        "report_sha256": None,
        "type": report["type"],
        "mode": report["mode"],
        "top_k": report["top_k"],
        "case_count": report["case_count"],
        "recall_at_5": report["recall_at_5"],
        "mrr_at_5": report["mrr_at_5"],
        "citation_exactness": report["citation_exactness"],
        "unsupported_claim_authorization": report["unsupported_claim_authorization"],
        "acceptance": report["acceptance"],
        "passed": True,
        "embedding_model": model_id,
        "per_language": {lang: {"cases": int(per_language[lang]["cases"])} for lang in ("vi", "en", "zh", "ko")},
    }


def create_signoff(*, property_id: str, db_path: Path, embedding_model: Path,
                   embedding_manifest: Path, benchmark_report: Path,
                   knowledge_archive: Path, knowledge_manifest: Path,
                   knowledge_signature: Path, knowledge_public_key: Path,
                   map_release: Path, planning_release: Path, property_profile: Path,
                   private_key: Path, receipt_path: Path, signature_path: Path,
                   min_documents: int = 300, min_cases: int = 80,
                   min_per_language: int = 20, min_recall: float = 0.90,
                   min_mrr: float = 0.75, code_revision: str = "") -> dict:
    if receipt_path.exists() or signature_path.exists():
        raise FileExistsError("Refusing to overwrite an existing production sign-off")
    if not verify_rag_model_manifest(str(embedding_model), str(embedding_manifest)):
        raise ValueError("Embedding model manifest integrity check failed")
    if not learned_embedding_profile(str(embedding_model)):
        raise ValueError("Production sign-off refuses deterministic/hash embedding fallbacks")
    model_id = model_identity(str(embedding_model), str(embedding_manifest))

    manifest, documents = read_signed_package(
        knowledge_archive, knowledge_manifest, knowledge_signature, knowledge_public_key,
        property_id=property_id,
    )
    corpus_digest = _corpus_digest_from_package(documents)
    benchmark = json.loads(benchmark_report.read_text(encoding="utf-8"))
    benchmark_summary = _validate_benchmark(
        benchmark, model_id=model_id, corpus_digest=corpus_digest,
        min_cases=min_cases, min_per_language=min_per_language,
        min_recall=min_recall, min_mrr=min_mrr,
    )
    benchmark_summary["report_sha256"] = _sha256(benchmark_report)

    evidence, transient_counts = _read_db(db_path, property_id)
    if any(transient_counts.values()):
        raise ValueError(f"Runtime DB contains mutable/dev state: {transient_counts}")
    if (evidence.get("release_version") != manifest.get("release_version")
            or evidence.get("bundle_sha256") != manifest.get("sha256")
            or evidence.get("chunk_policy_hash") != manifest.get("chunk_policy_hash")
            or evidence.get("embedding_model_id") != model_id
            or evidence.get("quality_status") != "validated"
            or int(evidence.get("document_count", 0)) < min_documents
            or int(evidence.get("public_language_count", 0)) < 4
            or int(evidence.get("chunk_count", 0)) < int(evidence.get("document_count", 0))):
        raise ValueError("Runtime DB evidence does not match the signed learned-model knowledge release")

    receipt = {
        "format": FORMAT,
        "property_id": property_id,
        "signed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "code_revision": code_revision or None,
        "fresh_runtime_image": True,
        "embedding": {
            "model_id": model_id,
            "manifest_sha256": manifest_sha256(str(embedding_manifest)),
            "learned": True,
        },
        "retrieval_benchmark": benchmark_summary,
        "knowledge_release": {key: evidence[key] for key in (
            "release_version", "bundle_sha256", "chunk_policy_hash", "embedding_model_id",
            "document_count", "chunk_count", "public_language_count", "domain_count", "quality_status",
        )},
        "immutable_artifacts": {
            "map_release_sha256": _sha256(map_release),
            "planning_release_sha256": _sha256(planning_release),
            "property_profile_sha256": _sha256(property_profile),
            "knowledge_manifest_sha256": _sha256(knowledge_manifest),
        },
        "release_gate": {
            "min_documents": min_documents,
            "min_cases": min_cases,
            "min_per_language": min_per_language,
            "min_recall_at_5": min_recall,
            "min_mrr_at_5": min_mrr,
            "citation_exactness_required": 1.0,
            "unsupported_claim_authorization_required": 1.0,
            "transient_state_counts_at_signoff": transient_counts,
        },
    }

    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = load_pem_private_key(private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Only Ed25519 production sign-off keys are accepted")
    raw = canonical_receipt_bytes(receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(raw)
    signature_path.write_bytes(base64.b64encode(key.sign(raw)))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--property-id", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--embedding-manifest", type=Path, required=True)
    parser.add_argument("--benchmark-report", type=Path, required=True)
    parser.add_argument("--knowledge-archive", type=Path, required=True)
    parser.add_argument("--knowledge-manifest", type=Path, required=True)
    parser.add_argument("--knowledge-signature", type=Path, required=True)
    parser.add_argument("--knowledge-public-key", type=Path, required=True)
    parser.add_argument("--map-release", type=Path, required=True)
    parser.add_argument("--planning-release", type=Path, required=True)
    parser.add_argument("--property-profile", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--min-documents", type=int, default=300)
    parser.add_argument("--min-cases", type=int, default=80)
    parser.add_argument("--min-per-language", type=int, default=20)
    parser.add_argument("--min-recall", type=float, default=0.90)
    parser.add_argument("--min-mrr", type=float, default=0.75)
    parser.add_argument("--code-revision", default="")
    args = parser.parse_args()
    receipt = create_signoff(
        property_id=args.property_id, db_path=args.db,
        embedding_model=args.embedding_model, embedding_manifest=args.embedding_manifest,
        benchmark_report=args.benchmark_report,
        knowledge_archive=args.knowledge_archive, knowledge_manifest=args.knowledge_manifest,
        knowledge_signature=args.knowledge_signature, knowledge_public_key=args.knowledge_public_key,
        map_release=args.map_release, planning_release=args.planning_release,
        property_profile=args.property_profile, private_key=args.private_key,
        receipt_path=args.receipt, signature_path=args.signature,
        min_documents=args.min_documents, min_cases=args.min_cases,
        min_per_language=args.min_per_language, min_recall=args.min_recall,
        min_mrr=args.min_mrr, code_revision=args.code_revision,
    )
    print(json.dumps({
        "property_id": receipt["property_id"],
        "embedding_model": receipt["embedding"]["model_id"],
        "recall_at_5": receipt["retrieval_benchmark"]["recall_at_5"],
        "mrr_at_5": receipt["retrieval_benchmark"]["mrr_at_5"],
        "release_version": receipt["knowledge_release"]["release_version"],
        "production_signoff": "SIGNED",
    }, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
