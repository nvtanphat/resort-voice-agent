"""Embedder contract and vector validation shared by every backend."""
from __future__ import annotations
import math
from typing import Protocol


class Embedder(Protocol):
    model_name: str

    def encode(self, text: str) -> list[float]: ...
    def encode_query(self, text: str) -> list[float]: ...
    def encode_passage(self, text: str) -> list[float]: ...


def valid_vector(value: object) -> bool:
    return (isinstance(value, (list, tuple)) and 0 < len(value) <= 4096
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) for v in value)
            and any(v != 0 for v in value))


def cosine(a: list[float], b: list[float]) -> float:
    if not valid_vector(a) or not valid_vector(b) or len(a) != len(b):
        return -1.0
    norm = math.sqrt(sum(v * v for v in a)) * math.sqrt(sum(v * v for v in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else -1.0
