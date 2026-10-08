"""FAISS CPU adapter with atomic sidecar metadata and rebuild semantics."""
from __future__ import annotations

import json
from pathlib import Path
import os
import tempfile
from threading import RLock
from typing import Any, Iterable, Mapping, Sequence

from .base import VectorMatch, VectorRecord, VectorStoreError, metadata_matches, validate_filter


class FaissVectorStore:
    backend = "faiss"

    def __init__(self, path: str | Path, *, name: str = "knowledge"):
        root = Path(path)
        if root.is_symlink():
            raise VectorStoreError("Vector directory may not be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        self._root = root
        self._stem = root / name
        self._index_path = self._stem.with_suffix(".index")
        self._meta_path = self._stem.with_suffix(".json")
        self._lock = RLock()
        try:
            import faiss
            import numpy
        except ImportError as exc:  # pragma: no cover - depends on deployment extra
            raise VectorStoreError("FAISS and numpy extras are not installed") from exc
        self._faiss = faiss
        self._numpy = numpy
        self._records: list[dict[str, Any]] = []
        self._dimension = 0
        self._index = None
        self._load()

    def _load(self) -> None:
        with self._lock:
            if self._meta_path.exists() != self._index_path.exists():
                raise VectorStoreError("FAISS index and metadata sidecar are inconsistent")
            if not self._meta_path.exists():
                return
            try:
                payload = json.loads(self._meta_path.read_text(encoding="utf-8"))
                if payload.get("schema_version") != 1 or not isinstance(payload.get("records"), list):
                    raise ValueError("unsupported metadata schema")
                self._records = payload["records"]
                for item in self._records:
                    if (not isinstance(item, dict) or not isinstance(item.get("key"), str)
                            or not isinstance(item.get("vector"), list)
                            or not isinstance(item.get("metadata"), dict)):
                        raise ValueError("invalid vector metadata record")
                self._dimension = int(payload.get("dimension", 0))
                self._index = self._faiss.read_index(str(self._index_path))
                if self._index.ntotal != len(self._records) or self._index.d != self._dimension:
                    raise ValueError("index/metadata count or dimension mismatch")
            except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise VectorStoreError("FAISS index is corrupt or unreadable") from exc

    @staticmethod
    def _record_payload(record: VectorRecord) -> dict[str, Any]:
        return {"key": record.key, "vector": list(record.vector),
                "metadata": dict(record.metadata)}

    def _rebuild(self, records: list[VectorRecord]) -> None:
        if records:
            dimension = len(records[0].vector)
            if any(len(record.vector) != dimension for record in records):
                raise VectorStoreError("FAISS records have inconsistent dimensions")
            matrix = self._numpy.asarray([record.vector for record in records], dtype="float32")
            self._faiss.normalize_L2(matrix)
            index = self._faiss.IndexFlatIP(dimension)
            index.add(matrix)
        else:
            dimension = self._dimension
            index = self._faiss.IndexFlatIP(dimension) if dimension else None
        payload = {"schema_version": 1, "dimension": dimension,
                   "records": [self._record_payload(record) for record in records]}
        self._root.mkdir(parents=True, exist_ok=True)
        fd_index, tmp_index_name = tempfile.mkstemp(prefix=".vector-", suffix=".index", dir=self._root)
        fd_meta, tmp_meta_name = tempfile.mkstemp(prefix=".vector-", suffix=".json", dir=self._root)
        try:
            os.close(fd_index)
            os.close(fd_meta)
            if index is not None:
                self._faiss.write_index(index, tmp_index_name)
            else:
                Path(tmp_index_name).unlink(missing_ok=True)
            Path(tmp_meta_name).write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            if index is not None:
                os.replace(tmp_index_name, self._index_path)
            else:
                self._index_path.unlink(missing_ok=True)
            os.replace(tmp_meta_name, self._meta_path)
        finally:
            Path(tmp_index_name).unlink(missing_ok=True)
            Path(tmp_meta_name).unlink(missing_ok=True)
        self._records = payload["records"]
        self._dimension = dimension
        self._index = index

    def upsert(self, records: Iterable[VectorRecord]) -> int:
        batch = list(records)
        if not batch:
            return 0
        with self._lock:
            merged: dict[str, VectorRecord] = {
                str(item["key"]): VectorRecord(
                    str(item["key"]), tuple(float(value) for value in item["vector"]),
                    item["metadata"])
                for item in self._records
            }
            merged.update({record.key: record for record in batch})
            self._rebuild(list(merged.values()))
        return len(batch)


    def query(self, vector: Sequence[float], *, k: int,
              filters: Mapping[str, Any] | None = None) -> list[VectorMatch]:
        if not vector or not 1 <= k <= 1000:
            raise ValueError("Invalid vector query")
        validate_filter(filters)
        with self._lock:
            if self._index is None or not self._records:
                return []
            if len(vector) != self._dimension:
                raise VectorStoreError("Query vector dimension does not match index")
            query = self._numpy.asarray([vector], dtype="float32")
            self._faiss.normalize_L2(query)
            scores, positions = self._index.search(query, len(self._records))
            matches = []
            for score, position in zip(scores[0], positions[0]):
                if position < 0:
                    continue
                item = self._records[int(position)]
                metadata = dict(item.get("metadata") or {})
                if metadata_matches(metadata, filters):
                    matches.append(VectorMatch(str(item["key"]), float(1.0 - score), metadata))
                if len(matches) >= k:
                    break
            return matches


    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"backend": self.backend, "count": len(self._records), "dimension": self._dimension}

    def close(self) -> None:
        self._index = None
