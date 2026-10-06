"""Chroma PersistentClient adapter with explicit local-only semantics."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .base import VectorMatch, VectorRecord, VectorStoreError, metadata_matches, validate_filter


_NAME = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_collection_name(name: str) -> str:
    value = _NAME.sub("-", name).strip("-") or "concierge"
    return value[:48]


class ChromaVectorStore:
    backend = "chroma"

    def __init__(self, path: str | Path, *, collection: str = "knowledge"):
        root = Path(path)
        if root.is_symlink():
            raise VectorStoreError("Vector directory may not be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:  # pragma: no cover - depends on deployment extra
            raise VectorStoreError("Chroma extra is not installed") from exc
        try:
            self._client = chromadb.PersistentClient(
                path=str(root), settings=Settings(anonymized_telemetry=False))
            self._collection = self._client.get_or_create_collection(
                name=_safe_collection_name(collection), metadata={"hnsw:space": "cosine"})
        except Exception as exc:  # pragma: no cover - backend-specific errors
            raise VectorStoreError("Unable to open Chroma persistent index") from exc

    @staticmethod
    def _metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
        validate_filter(metadata)
        # Chroma does not accept null metadata.  Empty strings preserve the
        # distinction without making the adapter invent a default fact.
        return {key: ("" if value is None else value) for key, value in metadata.items()}

    @staticmethod
    def _where(filters: Mapping[str, Any] | None) -> dict[str, Any] | None:
        validate_filter(filters)
        if not filters:
            return None
        clauses = []
        for key, value in filters.items():
            if key == "effective_on":
                continue
            if isinstance(value, (tuple, list, set, frozenset)):
                clauses.append({key: {"$in": list(value)}})
            else:
                clauses.append({key: {"$eq": value}})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def upsert(self, records: Iterable[VectorRecord]) -> int:
        batch = list(records)
        if not batch:
            return 0
        try:
            self._collection.upsert(
                ids=[record.key for record in batch],
                embeddings=[list(record.vector) for record in batch],
                metadatas=[self._metadata(record.metadata) for record in batch],
            )
        except Exception as exc:  # pragma: no cover
            raise VectorStoreError("Chroma upsert failed") from exc
        return len(batch)

    def query(self, vector: Sequence[float], *, k: int,
              filters: Mapping[str, Any] | None = None) -> list[VectorMatch]:
        if not vector or not 1 <= k <= 1000:
            raise ValueError("Invalid vector query")
        validate_filter(filters)
        try:
            result = self._collection.query(
                query_embeddings=[list(vector)],
                n_results=max(k, min(1000, k * 8)),
                where=self._where(filters),
                include=["metadatas", "distances"],
            )
        except Exception as exc:  # pragma: no cover
            raise VectorStoreError("Chroma query failed") from exc
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        matches = []
        for key, distance, metadata in zip(ids, distances, metadatas):
            item = dict(metadata or {})
            if metadata_matches(item, filters):
                matches.append(VectorMatch(str(key), float(distance), item))
            if len(matches) >= k:
                break
        return matches

    def delete_release(self, release_version: int | str) -> int:
        value = int(release_version)
        before = self._collection.count()
        try:
            self._collection.delete(where={"release_version": {"$eq": value}})
        except Exception as exc:  # pragma: no cover
            raise VectorStoreError("Chroma release deletion failed") from exc
        return max(0, before - self._collection.count())

    def stats(self) -> dict[str, Any]:
        try:
            return {"backend": self.backend, "count": int(self._collection.count())}
        except Exception as exc:  # pragma: no cover
            raise VectorStoreError("Chroma stats failed") from exc

    def close(self) -> None:
        # PersistentClient owns no explicit close API.  Dropping the reference
        # is deliberate; callers must not delete the directory while in use.
        self._collection = None
        self._client = None
