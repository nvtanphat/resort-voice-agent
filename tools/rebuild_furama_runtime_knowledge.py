"""Rebuild Furama runtime knowledge from compiled locale-native documents.

Defaults to the pinned local Ollama model. Production rebuilds should use
--require-learned so a fallback cannot be mistaken for the production index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.common import LocalEmbedder
from concierge_kiosk.rag.ingestion import chunk_policy_hash, ingest_bundle

COMPILED = ROOT / "knowledge/compiled/furama"
DB = ROOT / "data/concierge.sqlite3"
DEFAULT_MODEL = "ollama://bge-m3"
DEFAULT_MANIFEST = ROOT / "models/embeddings/bge-m3.ollama.manifest.json"


def _bundle_digest(documents: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name, raw in sorted(documents.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def rebuild(*, model: str = DEFAULT_MODEL, manifest: Path = DEFAULT_MANIFEST,
            database: Path = DB, require_learned: bool = False) -> dict[str, object]:
    documents = {
        path.relative_to(COMPILED).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(COMPILED.rglob("*.md"))
    }
    if not documents or len(documents) % 4 != 0:
        raise ValueError(f"Expected a non-empty 4-locale compiled corpus, got {len(documents)} documents")
    by_locale = {}
    for rel in documents:
        locale = rel.split("/", 1)[0]
        by_locale[locale] = by_locale.get(locale, 0) + 1
    if set(by_locale) != {"en", "vi", "ko", "zh"} or len(set(by_locale.values())) != 1:
        raise ValueError(f"Compiled corpus is not balanced across en/vi/ko/zh: {by_locale}")
    embedder = LocalEmbedder(str(model), str(manifest))
    if require_learned and not getattr(embedder, "is_learned", False):
        raise RuntimeError("--require-learned refused the deterministic hash fallback")
    store = Store(database)
    with store.connection() as con:
        current = con.execute(
            "SELECT release_version FROM knowledge_releases WHERE property_id=?",
            ("FURAMA_DANANG",),
        ).fetchone()
    release_version = int(current[0]) + 1 if current is not None else 1
    count = ingest_bundle(
        store, documents, property_id="FURAMA_DANANG", embedder=embedder,
        release_version=release_version, bundle_sha256=_bundle_digest(documents),
        signed_chunk_policy_hash=chunk_policy_hash(), effective_date=date.today().isoformat(),
        replace_snapshot=True,
    )
    with store.connection() as con:
        total = con.execute("SELECT COUNT(*) FROM knowledge WHERE property_id=? AND active=1", ("FURAMA_DANANG",)).fetchone()[0]
        embedded = con.execute("SELECT COUNT(*) FROM knowledge WHERE property_id=? AND active=1 AND embedding IS NOT NULL", ("FURAMA_DANANG",)).fetchone()[0]
        models = [row[0] for row in con.execute("SELECT DISTINCT embedding_model FROM knowledge WHERE property_id=? AND active=1 ORDER BY embedding_model", ("FURAMA_DANANG",)).fetchall()]
        release = con.execute(
            "SELECT release_version,bundle_sha256,chunk_policy_hash FROM knowledge_releases WHERE property_id=?",
            ("FURAMA_DANANG",),
        ).fetchone()
        languages = dict(con.execute("SELECT language,COUNT(*) FROM knowledge WHERE property_id=? AND active=1 GROUP BY language", ("FURAMA_DANANG",)).fetchall())
    if total != embedded or total != count:
        raise RuntimeError(f"Dense rebuild incomplete: ingested={count}, active={total}, embedded={embedded}")
    return {
        "chunks": total,
        "embedded_chunks": embedded,
        "embedding_models": models,
        "languages": languages,
        "embedding_backend": getattr(embedder, "backend", "unknown"),
        "learned_embedding": bool(getattr(embedder, "is_learned", False)),
        "release_version": int(release[0]) if release else None,
        "bundle_sha256": release[1] if release else None,
        "chunk_policy_hash": release[2] if release else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=str, default=str(DEFAULT_MODEL))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--require-learned", action="store_true")
    args = parser.parse_args()
    print(json.dumps(rebuild(model=args.model, manifest=args.manifest,
                             database=args.database, require_learned=args.require_learned),
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
