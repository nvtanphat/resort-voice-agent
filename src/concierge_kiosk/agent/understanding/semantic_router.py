"""Embeddings-backed route suggestion kept separate from production authority.

The router is intentionally a suggestion component.  It returns ``None`` when
the nearest reviewed examples are not sufficiently close or disagree by too
small a margin.  Callers must keep deterministic safety/action routing ahead of
it and must not use this class as confirmation or business-write authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Protocol

from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.rag.common import cosine


class _Embedder(Protocol):
    def encode_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class RouteExample:
    example_id: str
    language: str
    text: str
    route: str
    split: str = "train"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SemanticRouteDecision:
    route: str | None
    score: float
    margin: float
    accepted: bool
    example_id: str | None
    language: str
    service_code: str | None = None


class SemanticRouter:
    """Nearest reviewed-example route suggestions.

    The caller remains the authority: a semantic result can only be used for
    explicitly allowlisted read routes. It never grants write or emergency
    authority from a similarity match.
    """

    def __init__(self, examples: Iterable[RouteExample], embedder: _Embedder, *,
                 min_score: float, min_margin: float,
                 max_examples_per_route: int = 256,
                 mode: str = "active") -> None:
        if not 0 <= min_score <= 1 or not 0 <= min_margin <= 1:
            raise ValueError("semantic router thresholds must be in 0..1")
        if mode not in {"shadow", "active"}:
            raise ValueError("semantic router mode must be shadow or active")
        self.embedder = embedder
        self.min_score = min_score
        self.min_margin = min_margin
        self.mode = mode
        self._index: dict[str, list[tuple[RouteExample, list[float]]]] = {}
        counts: dict[tuple[str, str], int] = {}
        for example in examples:
            key = (example.language, example.route)
            if counts.get(key, 0) >= max_examples_per_route:
                continue
            normalized = normalize_intent_text(example.text, example.language)
            if not normalized:
                continue
            vector = embedder.encode_query(normalized)
            self._index.setdefault(example.language, []).append((example, vector))
            counts[key] = counts.get(key, 0) + 1

    def route(self, text: str, language: str) -> SemanticRouteDecision:
        normalized = normalize_intent_text(text, language)
        examples = self._index.get(language, ())
        if not normalized or not examples:
            return SemanticRouteDecision(None, -1.0, 0.0, False, None, language, None)
        query_vector = self.embedder.encode_query(normalized)
        nearest: dict[str, tuple[float, RouteExample]] = {}
        for example, vector in examples:
            score = cosine(query_vector, vector)
            current = nearest.get(example.route)
            if current is None or score > current[0]:
                nearest[example.route] = (score, example)
        ranked = sorted(nearest.items(), key=lambda item: item[1][0], reverse=True)
        winner_route, (score, winner) = ranked[0]
        runner_score = ranked[1][1][0] if len(ranked) > 1 else -1.0
        margin = score - runner_score
        accepted = score >= self.min_score and margin >= self.min_margin
        service_code = None
        if isinstance(winner.metadata, dict) and isinstance(winner.metadata.get('service_code'), str):
            service_code = winner.metadata['service_code']
        return SemanticRouteDecision(winner_route if accepted else None, score, margin,
                                    accepted, winner.example_id, language, service_code)

    def nearest_examples(self, text: str, language: str, *, limit: int = 5) -> tuple[dict[str, str], ...]:
        """Return bounded, data-owned ICL examples for an uncertain T3 turn."""
        if not 1 <= limit <= 5:
            raise ValueError("nearest-example limit must be between 1 and 5")
        normalized = normalize_intent_text(text, language)
        examples = self._index.get(language, ())
        if not normalized or not examples:
            return ()
        query_vector = self.embedder.encode_query(normalized)
        ranked = sorted(
            ((cosine(query_vector, vector), example) for example, vector in examples),
            key=lambda item: (-item[0], item[1].example_id),
        )
        return tuple({
            "example_id": example.example_id,
            "text": example.text[:240],
            "route": example.route,
            "score": f"{score:.4f}",
        } for score, example in ranked[:limit])


def read_route_examples(path: str | Path, *, split: str | None = None) -> list[RouteExample]:
    """Load a bounded, schema-shaped route-example JSONL file."""
    result: list[RouteExample] = []
    source = Path(path)
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if split is not None and payload.get("split", "train") != split:
            continue
        result.append(RouteExample(
            example_id=str(payload["example_id"]),
            language=str(payload["language"]),
            text=str(payload["text"]),
            route=str(payload["route"]),
            split=str(payload.get("split", "train")),
            metadata={key: value for key, value in payload.items()
                      if key not in {"example_id", "language", "text", "route", "split"}},
        ))
    return result


__all__ = ["RouteExample", "SemanticRouteDecision", "SemanticRouter", "read_route_examples"]
