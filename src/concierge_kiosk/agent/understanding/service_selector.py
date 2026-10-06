"""Semantic candidate selection for service understanding.

The property catalog is the source of guest-facing service identity.  This
module turns catalog names and descriptions into a bounded candidate set for
the understanding model; it never authorizes a request and never returns a
service code that is not present in the signed registry.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import threading
from typing import Any, Protocol, Sequence

from concierge_kiosk.domain.service_registry import (
    SERVICE_DEFINITIONS,
    accepted_slots,
    default_service_for,
    route_branch_for_request_kind,
    service_code_for_catalog_id,
    service_definition,
)
from concierge_kiosk.rag.embedding.base import cosine


class _Embedder(Protocol):
    def encode_query(self, text: str) -> list[float]: ...

    def encode_passage(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class ServiceCatalogEntry:
    catalog_service_id: str
    name: str
    description: str
    names_by_locale: tuple[tuple[str, str], ...]
    embedding_text: str


@dataclass(frozen=True)
class ServiceCandidate:
    """A server-shaped candidate safe to place in an understanding prompt."""

    service_mode: str
    request_kind: str
    catalog_service_id: str | None
    name: str
    description: str
    score: float

    def public(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "service_mode": self.service_mode,
            "request_kind": self.request_kind,
            "accepted_slots": list(accepted_slots(self.service_mode)),
        }
        if self.catalog_service_id:
            value["catalog_service_id"] = self.catalog_service_id
        if self.name:
            value["name"] = self.name[:180]
        if self.description:
            value["description"] = self.description[:320]
        return value


def _text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:maximum]


def _catalog_entry(row: object) -> ServiceCatalogEntry | None:
    if not isinstance(row, dict):
        return None
    service_id = _text(row.get("service_id"), 128)
    name = _text(row.get("name"), 180)
    description = _text(row.get("description"), 640)
    localized = row.get("names_by_locale")
    if not service_id or not isinstance(localized, dict):
        return None
    names: list[tuple[str, str]] = []
    for language, value in localized.items():
        language_text = _text(language, 16)
        name_text = _text(value, 180)
        if language_text and name_text:
            names.append((language_text, name_text))
    if not names and not name:
        return None
    labels = " | ".join(dict.fromkeys(value for _, value in names))
    embedding_text = ". ".join(
        value for value in (labels, description) if value).strip()
    if not embedding_text:
        return None
    return ServiceCatalogEntry(
        catalog_service_id=service_id,
        name=name or names[0][1],
        description=description,
        names_by_locale=tuple(names),
        embedding_text=embedding_text,
    )


@dataclass(frozen=True)
class CommandExample:
    """A reviewed training utterance shown to the command model as a few-shot."""

    language: str
    utterance: str
    commands: tuple[dict[str, Any], ...]
    goal: str | None
    # Paraphrases of one reviewed situation share a group (frame/concept);
    # calibration holds a whole group out so it measures unseen phrasing.
    group: str | None = None


def _example(row: object) -> CommandExample | None:
    if not isinstance(row, dict) or row.get("split") != "train":
        return None
    utterance = _text(row.get("utterance"), 300)
    language = _text(row.get("language"), 16)
    if not utterance or not language:
        return None
    route = row.get("expected_route")
    group = _text(row.get("frame_id") or row.get("concept_key"), 128) or None
    if route == "service":
        goal = row.get("service_code")
        if not isinstance(goal, str) or service_definition(goal) is None:
            return None
        allowed = set(accepted_slots(goal))
        slots = row.get("expected_slots") if isinstance(row.get("expected_slots"), dict) else {}
        # Labels hold normalized values; a few-shot must only show slot text
        # that is literally present, exactly as the runtime validator demands.
        shown = [{"name": name, "text": str(value)} for name, value in slots.items()
                 if name in allowed and str(value) and str(value) in utterance]
        command: dict[str, Any] = {"type": "StartGoal", "goal": goal, "slots": shown}
        return CommandExample(language, utterance, (command,), goal, group)
    if route in {"knowledge", "knowledge_abstain"}:
        return CommandExample(language, utterance, ({"type": "AskInfo", "query": utterance},), None,
                              group)
    return None


def load_command_examples(paths: Sequence[str | Path]) -> tuple[CommandExample, ...]:
    """Load train-split service/knowledge examples; evaluation data is never read here."""
    examples: list[CommandExample] = []
    for path in paths:
        source = Path(path)
        if not source.is_file() or source.is_symlink():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (example := _example(row)) is not None:
                examples.append(example)
    return tuple(examples)


def nearest_label(scored: Sequence[tuple[float, CommandExample]]
                  ) -> tuple[str | None, float, float]:
    """Return (label, best score, best score of any other label) from ranked examples.

    The label is the example's service goal, or ``None`` for a factual
    question. Shared by the runtime fallback and its calibration tool.
    """
    if not scored:
        return None, -1.0, -1.0
    best_score, best_example = scored[0]
    label = best_example.goal
    runner_up = next((score for score, example in scored[1:] if example.goal != label), -1.0)
    return label, best_score, runner_up


class ServiceSelector:
    """Lazy, in-memory semantic index over the service catalog and reviewed examples."""

    def __init__(self, catalog_path: str | Path, embedder: _Embedder, *, top_k: int = 8,
                 examples: Sequence[CommandExample] = (), example_k: int = 4) -> None:
        if not 1 <= top_k <= 16:
            raise ValueError("service selector top_k must be between 1 and 16")
        if not 0 <= example_k <= 8:
            raise ValueError("service selector example_k must be between 0 and 8")
        self.catalog_path = Path(catalog_path)
        if not self.catalog_path.is_file() or self.catalog_path.is_symlink():
            raise ValueError("service catalog is unavailable")
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("service catalog must be a list")
        entries = tuple(entry for row in payload
                        if (entry := _catalog_entry(row)) is not None)
        if not entries:
            raise ValueError("service catalog has no usable entries")
        self.embedder = embedder
        self.top_k = top_k
        self.example_k = example_k
        self.entries = entries
        self.examples = tuple(examples)
        self._vectors: tuple[list[float], ...] | None = None
        self._example_vectors: tuple[list[float], ...] | None = None
        self._warming = False
        self._lock = threading.Lock()

    def _ready_or_build(self) -> bool:
        """Return whether both indexes may be used now.

        Outside a guest turn (startup warm-up, tools, tests) the indexes are
        built synchronously. Inside a guest turn they are never built: the
        turn proceeds with what is ready and a background thread builds the
        rest, so a cold index can never consume the turn's time budget.
        """
        if self._vectors is not None and (self._example_vectors is not None or not self.examples):
            return True
        from concierge_kiosk.runtime.local_http import in_guest_turn
        if not in_guest_turn():
            self.warm()
            return True
        with self._lock:
            if not self._warming:
                self._warming = True
                threading.Thread(target=self._background_warm, name='service-selector-warm',
                                 daemon=True).start()
        return False

    def _background_warm(self) -> None:
        try:
            self.warm()
        except (OSError, RuntimeError, TypeError, ValueError, TimeoutError):
            pass
        finally:
            with self._lock:
                self._warming = False

    def _ensure_index(self) -> tuple[list[float], ...]:
        vectors = self._vectors
        if vectors is not None:
            return vectors
        with self._lock:
            if self._vectors is None:
                encoder = getattr(self.embedder, "encode_passage", None)
                if encoder is None:
                    encoder = self.embedder.encode_query
                texts = [entry.embedding_text for entry in self.entries]
                batch = getattr(self.embedder, "encode_many", None)
                self._vectors = (tuple(batch(texts)) if batch is not None
                                 else tuple(encoder(text) for text in texts))
            return self._vectors

    def _ensure_example_index(self) -> tuple[list[float], ...]:
        vectors = self._example_vectors
        if vectors is not None:
            return vectors
        with self._lock:
            if self._example_vectors is None:
                # Examples are guest utterances, so they share the query side
                # of the embedding space with the turn being understood.
                texts = [example.utterance for example in self.examples]
                batch = getattr(self.embedder, "encode_many", None)
                self._example_vectors = (tuple(batch(texts)) if batch is not None
                                         else tuple(self.embedder.encode_query(text) for text in texts))
            return self._example_vectors

    def warm(self) -> None:
        """Build both indexes ahead of the first guest turn."""
        self._ensure_index()
        self._ensure_example_index()

    @staticmethod
    def _mode_for_entry(entry: ServiceCatalogEntry, enabled_request_kinds: frozenset[str]) -> str | None:
        mode = service_code_for_catalog_id(entry.catalog_service_id)
        if mode:
            definition = service_definition(mode)
            if definition and definition.request_kind in enabled_request_kinds:
                return mode
            return None
        # Catalog rows for staff-only services intentionally share the bounded
        # human-assistance workflow when there is no one-to-one registry mode.
        if "human" in enabled_request_kinds:
            fallback = default_service_for("human")
            if fallback and service_definition(fallback):
                return fallback
        return None

    def _fallback_candidates(self, enabled_request_kinds: frozenset[str]) -> list[ServiceCandidate]:
        """Keep uncatalogued governed capabilities available for the model.

        The catalog owns concrete property identity. Generic workflows such as
        maintenance have no canonical service row, so they are supplied as
        bounded registry fallbacks rather than selected by a keyword list.
        """
        catalog_modes = {
            mode for entry in self.entries
            if (mode := self._mode_for_entry(entry, enabled_request_kinds)) is not None
        }
        result: list[ServiceCandidate] = []
        for mode, definition in SERVICE_DEFINITIONS.items():
            if (mode in catalog_modes or definition.request_kind not in enabled_request_kinds
                    or route_branch_for_request_kind(definition.request_kind) not in {"service", "handoff"}):
                continue
            result.append(ServiceCandidate(
                service_mode=mode,
                request_kind=definition.request_kind,
                catalog_service_id=None,
                name="",
                description="Generic governed capability; use only when no catalog service fits.",
                score=-1.0,
            ))
        return result

    def _example_scores(self, query_vector: list[float]) -> list[tuple[float, CommandExample]]:
        if not self.examples:
            return []
        vectors = self._ensure_example_index()
        return sorted(((cosine(query_vector, vector), example)
                       for example, vector in zip(self.examples, vectors)),
                      key=lambda item: -item[0])

    def understand(self, query: str, *, language: str,
                   enabled_request_kinds: frozenset[str]
                   ) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        """Return bounded prompt candidates and the nearest reviewed few-shots.

        A service's rank is its best similarity to either its catalog text or a
        reviewed training utterance, so services whose catalog row is terse or
        absent are still retrieved by meaning rather than by a keyword list.
        """
        if not isinstance(query, str) or not query.strip() or not self._ready_or_build():
            # An empty candidate set lets the model see the full registry.
            return (), ()
        vectors = self._ensure_index()
        query_vector = self.embedder.encode_query(query[:500])
        scored_examples = self._example_scores(query_vector)
        example_best: dict[str, float] = {}
        for score, example in scored_examples:
            if example.goal is not None and example.goal not in example_best:
                example_best[example.goal] = score

        catalog_best: dict[str, tuple[float, ServiceCatalogEntry]] = {}
        for entry, vector in zip(self.entries, vectors):
            mode = self._mode_for_entry(entry, enabled_request_kinds)
            if mode is None or service_definition(mode) is None:
                continue
            score = cosine(query_vector, vector)
            if mode not in catalog_best or score > catalog_best[mode][0]:
                catalog_best[mode] = (score, entry)

        def combined(mode: str, base: float) -> float:
            return max(base, example_best.get(mode, base))

        catalog = sorted(
            (ServiceCandidate(
                service_mode=mode, request_kind=service_definition(mode).request_kind,
                catalog_service_id=entry.catalog_service_id, name=entry.name,
                description=entry.description, score=combined(mode, score))
             for mode, (score, entry) in catalog_best.items()),
            key=lambda item: (-item.score, item.service_mode))
        fallback = sorted(
            (replace(item, score=combined(item.service_mode, item.score))
             for item in self._fallback_candidates(enabled_request_kinds)),
            key=lambda item: (-item.score, item.service_mode))
        # Keep the complete prompt bounded by top_k while retaining a small
        # escape hatch for governed generic workflows that have no catalog row
        # (maintenance, generic staff help, and similar capabilities).
        fallback_budget = min(len(fallback), self.top_k // 3)
        selected = catalog[:self.top_k - fallback_budget] + fallback[:fallback_budget]
        selected.sort(key=lambda item: (-item.score, item.service_mode))
        offered = {item.service_mode for item in selected}
        shots = tuple(
            {"guest_turn": example.utterance, "commands": list(example.commands)}
            for _, example in scored_examples
            if example.goal is None or example.goal in offered)[:self.example_k]
        return tuple(item.public() for item in selected), shots

    def fallback_goal(self, query: str, *, language: str, enabled_request_kinds: frozenset[str],
                      min_score: float, min_margin: float) -> str | None:
        """Pick a service by nearest reviewed example when the SLM is unavailable.

        This is the model-free understanding fallback: no keyword lists, only
        similarity to reviewed training turns. It answers only when the best
        label is both close enough and clearly ahead of every other label
        (thresholds are calibrated on the training split); otherwise the turn
        stays a knowledge question. A label that is a factual question also
        yields ``None``, and so does any turn shaped as an information question
        by the profile's generic question grammar: without the model, a
        question that merely names a service must not start a goal.
        """
        if not isinstance(query, str) or not query.strip() or not self.examples:
            return None
        from concierge_kiosk.agent.understanding.intent import is_information_question
        if is_information_question(query, language) or not self._ready_or_build():
            return None
        scored = self._example_scores(self.embedder.encode_query(query[:500]))
        label, best, runner_up = nearest_label(scored)
        if label is None or best < min_score or best - runner_up < min_margin:
            return None
        definition = service_definition(label)
        if definition is None or definition.request_kind not in enabled_request_kinds:
            return None
        return definition.code

    def select(self, query: str, *, language: str,
               enabled_request_kinds: frozenset[str]) -> tuple[dict[str, Any], ...]:
        """Return a bounded prompt candidate set ordered by semantic score."""
        return self.understand(query, language=language,
                               enabled_request_kinds=enabled_request_kinds)[0]

    def select_availability_mode(self, query: str, *, language: str,
                                 enabled_request_kinds: frozenset[str]) -> str | None:
        """Resolve a read-only availability mode from the best semantic match.

        Only the top-ranked service is considered: walking down the list would
        answer an availability question about an unrelated service merely
        because it happens to have a synthetic availability source.
        """
        candidates = self.select(query, language=language,
                                 enabled_request_kinds=enabled_request_kinds)
        if not candidates:
            return None
        definition = service_definition(str(candidates[0].get("service_mode") or ""))
        if definition is not None and definition.availability_source is not None:
            return definition.code
        return None


__all__ = ["CommandExample", "ServiceCandidate", "ServiceSelector", "load_command_examples",
           "nearest_label"]
