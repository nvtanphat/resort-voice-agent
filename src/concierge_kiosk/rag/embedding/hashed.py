"""Deterministic offline hashed n-gram embedder."""
from __future__ import annotations
import hashlib
import math
import re
import unicodedata


class HashedNgramModel:
    """Tiny deterministic offline dense embedder.

    This is deliberately not presented as a learned semantic model. It provides a
    reproducible CPU-only dense path for typo/substring robustness and keeps the
    production interface identical to a locally pinned SentenceTransformer. A
    learned multilingual model can replace it without changing retrieval code.
    """
    FORMAT = "concierge-hash-embedding"

    def __init__(self, config: dict) -> None:
        if config.get("format") != self.FORMAT:
            raise ValueError("Unsupported built-in embedding format")
        self.dimension = int(config.get("dimension", 384))
        self.min_n = int(config.get("min_n", 2))
        self.max_n = int(config.get("max_n", 5))
        if not (64 <= self.dimension <= 4096 and 1 <= self.min_n <= self.max_n <= 8):
            raise ValueError("Invalid built-in embedding configuration")

    @staticmethod
    def _normalized(text: str) -> str:
        value = unicodedata.normalize("NFKC", text).casefold()
        value = re.sub(r"\s+", " ", value).strip()
        return f" {value} "

    def encode(self, text: str) -> list[float]:
        value = self._normalized(text)
        vector = [0.0] * self.dimension
        features: list[tuple[str, float]] = []
        # Whole tokens receive a little more weight; character n-grams make the
        # vector robust to accents, inflection and minor typing differences in
        # Vietnamese/Korean/Chinese/English without any network/model download.
        for token in re.findall(r"[\w\u4e00-\u9fff\uac00-\ud7a3]+", value, re.UNICODE):
            if len(token) >= 2:
                features.append(("w:" + token, 2.0))
        for n in range(self.min_n, self.max_n + 1):
            for i in range(max(0, len(value) - n + 1)):
                gram = value[i:i+n]
                if gram.strip():
                    features.append((f"c{n}:" + gram, 1.0))
        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8, person=b"ck-embed").digest()
            number = int.from_bytes(digest, "big", signed=False)
            bucket = number % self.dimension
            sign = -1.0 if (number >> 63) else 1.0
            vector[bucket] += sign * weight
        norm = math.sqrt(sum(item * item for item in vector))
        if norm == 0:
            raise ValueError("Cannot embed empty text")
        return [item / norm for item in vector]
