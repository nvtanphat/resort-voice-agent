"""Verify and atomically accept an offline knowledge update.

This is the local kiosk boundary around the signed package format.  It
enforces freshness, anti-rollback and an atomic accepted-version pointer before
calling the existing all-or-nothing SQLite ingestion transaction.  A future
deployment may replace the metadata envelope with python-tuf without changing
the ingestion or retrieval contract.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.ingestion import ingest_bundle
from tools.packaging.knowledge import read_signed_package


def _read_state(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_symlink():
        raise ValueError("OTA state may not be a symlink")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or type(value.get("accepted_release_version")) is not int:
        raise ValueError("Invalid OTA state")
    return int(value["accepted_release_version"])


def verify_update(*, archive: Path, manifest: Path, signature: Path, public_key: Path,
                  property_id: str, state_path: Path, now: int | None = None,
                  max_future_skew_seconds: int = 300) -> tuple[dict, dict[str, str]]:
    metadata, documents = read_signed_package(
        archive, manifest, signature, public_key, property_id=property_id)
    issued_at = metadata.get("issued_at")
    expires_at = metadata.get("expires_at")
    if type(issued_at) is not int or type(expires_at) is not int:
        raise ValueError("Knowledge OTA requires issued_at and expires_at metadata")
    now = int(time.time()) if now is None else int(now)
    if issued_at > now + max_future_skew_seconds:
        raise ValueError("Knowledge OTA issued_at is too far in the future")
    if now >= expires_at:
        raise ValueError("Knowledge OTA metadata has expired")
    accepted = _read_state(state_path)
    version = int(metadata["release_version"])
    if version <= accepted:
        raise ValueError("Knowledge OTA rollback or replay denied")
    return metadata, documents


def _write_state(path: Path, *, property_id: str, version: int, digest: str) -> None:
    if path.is_symlink():
        raise ValueError("OTA state may not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ota-state-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"schema_version": 1, "property_id": property_id,
                       "accepted_release_version": version, "bundle_sha256": digest},
                      handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_update(*, archive: Path, manifest: Path, signature: Path, public_key: Path,
                 property_id: str, db: Path, state_path: Path, embedder=None,
                 effective_date: str, now: int | None = None) -> dict:
    metadata, documents = verify_update(
        archive=archive, manifest=manifest, signature=signature, public_key=public_key,
        property_id=property_id, state_path=state_path, now=now)
    store = Store(db)
    count = ingest_bundle(
        store, documents, property_id=property_id, embedder=embedder,
        release_version=int(metadata["release_version"]), bundle_sha256=str(metadata["sha256"]),
        signed_chunk_policy_hash=str(metadata["chunk_policy_hash"]),
        effective_date=effective_date,
    )
    _write_state(state_path, property_id=property_id,
                 version=int(metadata["release_version"]), digest=str(metadata["sha256"]))
    return {"status": "accepted", "property_id": property_id,
            "release_version": int(metadata["release_version"]),
            "bundle_sha256": str(metadata["sha256"]), "chunks": count}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--property-id", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--effective-date", default="2026-10-05")
    args = parser.parse_args()
    if args.db is None:
        metadata, documents = verify_update(
            archive=args.archive, manifest=args.manifest, signature=args.signature,
            public_key=args.public_key, property_id=args.property_id, state_path=args.state)
        result = {"status": "verified", "property_id": args.property_id,
                  "release_version": metadata["release_version"], "documents": len(documents)}
    else:
        result = apply_update(
            archive=args.archive, manifest=args.manifest, signature=args.signature,
            public_key=args.public_key, property_id=args.property_id, db=args.db,
            state_path=args.state, effective_date=args.effective_date)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
