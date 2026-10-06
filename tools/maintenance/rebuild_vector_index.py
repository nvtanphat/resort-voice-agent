"""Build a local dense index from the SQLite knowledge source of truth.

This command is operator-only.  It never accepts content from the network and
does not make the vector index authoritative: retrieval rechecks each id in
SQLite before returning evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.persistence.sqlite_store import Store  # noqa: E402
from concierge_kiosk.rag.embedding.cache import decoded_embedding  # noqa: E402
from concierge_kiosk.rag.vectorstore import VectorRecord, open_vector_store  # noqa: E402


def build(*, db: Path, property_id: str, backend: str, path: Path,
          collection: str | None = None) -> dict:
    store = Store(db)
    with store.connection() as con:
        release = con.execute(
            "SELECT release_version FROM knowledge_releases WHERE property_id=?",
            (property_id,),
        ).fetchone()
        release_version = int(release[0]) if release is not None else 0
        rows = con.execute(
            "SELECT id,revision,property_id,language,classification,active,embedding_model,"
            "entity_id,fact_type,fact_context,effective_from,effective_to,embedding "
            "FROM knowledge WHERE property_id=? AND embedding IS NOT NULL",
            (property_id,),
        ).fetchall()
    records = []
    for row in rows:
        metadata = {
            "doc_id": str(row["id"]),
            "revision": str(row["revision"]),
            "property_id": str(row["property_id"]),
            "language": str(row["language"]),
            "classification": str(row["classification"]),
            "active": int(row["active"]),
            "embedding_model": str(row["embedding_model"] or ""),
            "entity_id": str(row["entity_id"] or ""),
            "fact_type": str(row["fact_type"] or ""),
            "fact_context": str(row["fact_context"] or ""),
            "effective_from": str(row["effective_from"]),
            "effective_to": str(row["effective_to"] or ""),
            "release_version": release_version,
        }
        records.append(VectorRecord(
            key=f"{row['id']}::{row['revision']}",
            vector=decoded_embedding(str(row["embedding"])),
            metadata=metadata,
        ))
    vector_store = open_vector_store(
        backend=backend, path=path,
        collection=collection or f"{property_id}-knowledge",
    )
    try:
        count = vector_store.upsert(records)
        stats = vector_store.stats()
    finally:
        vector_store.close()
    return {
        "schema_version": 1,
        "property_id": property_id,
        "backend": backend,
        "path": str(path),
        "release_version": release_version,
        "source_rows": len(rows),
        "upserted": count,
        "stats": stats,
        "source_of_truth": str(db),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/concierge.sqlite3"))
    parser.add_argument("--property-id", required=True)
    parser.add_argument("--backend", choices=("chroma", "faiss"), required=True)
    parser.add_argument("--path", type=Path, default=Path("data/vectors"))
    parser.add_argument("--collection")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build(db=args.db, property_id=args.property_id, backend=args.backend,
                   path=args.path, collection=args.collection)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
