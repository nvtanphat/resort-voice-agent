"""Contracts and common validation for local vector indexes."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Protocol, Sequence


MetadataValue = str | int | float | bool | None
Metadata = Mapping[str, MetadataValue]

# These are the only fields an index may use for authorization/scoping.  The
# answer path still re-reads the matching SQLite rows before citing anything.
FILTER_FIELDS = frozenset({
    "doc_id",
    "property_id", "language", "classification", "active", "embedding_model",
    "entity_id", "fact_type", "fact_context", "revision", "release_version",
    "effective_from", "effective_to", "effective_on",
})


class VectorStoreError(RuntimeError):
    """A vector index is unavailable, corrupt or inconsistent."""


@dataclass(frozen=True)
class VectorRecord:
    """A vector plus only bounded, non-guest metadata."""

    key: str
    vector: tuple[float, ...]
    metadata: Metadata

    def __post_init__(self) -> None:
        if not self.key or len(self.key) > 512:
            raise ValueError("Vector record key is required and bounded")
        if not self.vector or any(not math.isfinite(float(item)) for item in self.vector):
            raise ValueError("Vector record contains a non-finite value")
        validate_filter(self.metadata)


@dataclass(frozen=True)
class VectorMatch:
    key: str
    distance: float
    metadata: Mapping[str, MetadataValue]


def validate_filter(filters: Mapping[str, Any] | None) -> None:
    if filters is None:
        return
    if not isinstance(filters, Mapping):
        raise ValueError("Vector filters must be a mapping")
    unknown = set(filters) - FILTER_FIELDS
    if unknown:
        raise ValueError(f"Unsupported vector filter fields: {sorted(unknown)}")
    for key, value in filters.items():
        values = value if isinstance(value, (tuple, list, set, frozenset)) else (value,)
        if not values or any(not isinstance(item, (str, int, float, bool)) for item in values):
            raise ValueError(f"Invalid vector filter value for {key}")


def metadata_matches(metadata: Metadata, filters: Mapping[str, Any] | None) -> bool:
    """Apply exact and effective-date filters consistently across backends."""
    validate_filter(filters)
    if not filters:
        return True
    effective_on = filters.get("effective_on")
    if effective_on is not None:
        start = str(metadata.get("effective_from", ""))
        end = metadata.get("effective_to")
        if not start or start > str(effective_on) or (end not in (None, "") and str(end) < str(effective_on)):
            return False
    for key, expected in filters.items():
        if key == "effective_on":
            continue
        actual = metadata.get(key)
        values = expected if isinstance(expected, (tuple, list, set, frozenset)) else (expected,)
        if actual not in values:
            return False
    return True


class VectorStore(Protocol):
    """Minimal backend contract used by ingestion, retrieval and probes."""

    backend: str

    def upsert(self, records: Iterable[VectorRecord]) -> int: ...

    def query(self, vector: Sequence[float], *, k: int,
              filters: Mapping[str, Any] | None = None) -> list[VectorMatch]: ...

    def delete_release(self, release_version: int | str) -> int: ...

    def stats(self) -> dict[str, Any]: ...

    def close(self) -> None: ...
