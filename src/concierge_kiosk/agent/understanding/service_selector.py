"""Semantic candidate selection for service understanding.

The property catalog is the source of guest-facing service identity.  This
module turns catalog names and descriptions into a bounded candidate set for
the understanding model; it never authorizes a request and never returns a
service code that is not present in the signed registry.
"""
from __future__ import annotations
from concierge_kiosk.runtime.observability import observed

from dataclasses import dataclass, replace
from collections import OrderedDict
import hashlib
import json
import math
import re
from pathlib import Path
import threading
from typing import Any, Iterable, Protocol, Sequence

from concierge_kiosk.agent.understanding.commands import commands_from_items, validate_commands
from concierge_kiosk.core.dataset_layout import row_has_status, training_agent_paths
from concierge_kiosk.domain.service_registry import (
    ACTION_REQUEST_KINDS,
    SERVICE_DEFINITIONS,
    accepted_slots,
    default_service_for,
    route_branch_for_request_kind,
    service_code_for_catalog_id,
    service_definition,
)
from concierge_kiosk.rag.embedding.base import cosine, valid_vector


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



def _identity_text(code: str, definition) -> str:
    """What a service does, for its embedding: the first sentence of its description.

    Later sentences tell the model where the service ends ("delivering supplies is
    amenity_delivery"). An embedding cannot read that negation and would move the
    service towards its neighbour, so only the affirmative identity is embedded.
    """
    description = (getattr(definition, 'description', '') or '').strip()
    first = re.split(r'(?<=[.!?])\s+', description, maxsplit=1)[0].strip() if description else ''
    return first or code

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
    # The server question this turn answers (slot replies only).
    pending_field: str | None = None
    # The verified topic the guest had just been told about (follow-up examples only).
    context_topic: str | None = None

    @property
    def label(self) -> str:
        """Coarse intent label used by the embedding router and calibration."""
        starts = [command for command in self.commands if command.get("type") == "StartGoal"]
        if len(self.commands) > 1:
            return "multi"
        command = self.commands[0]
        kind = command.get("type")
        if kind == "StartGoal":
            return f"service:{starts[0].get('goal')}"
        if kind == "ChitChat":
            return f"chitchat:{command.get('kind') or 'smalltalk'}"
        if kind in {"SetSlot", "CorrectSlot"}:
            return f"slot:{command.get('field')}"
        return str(kind).casefold()


# Legacy rows carry a route but no explicit commands.  Only routes with an
# unambiguous command meaning are used as examples; safety routes (emergency,
# policy/privacy guards) are owned by deterministic layers and never learned.
_LEGACY_ROUTE_COMMANDS: dict[str, dict[str, Any]] = {
    "status": {"type": "AskStatus"},
    "clarification": {"type": "Clarify"},
    "escalation": {"type": "Handoff", "reason": "escalation"},
    "reopen": {"type": "Handoff", "reason": "reopen earlier request"},
    "safety_escalation": {"type": "Handoff", "reason": "safety concern"},
}

# Routes owned by deterministic layers: a bare-route row teaches no command, and
# the omission is deliberate.  Any other bare route must appear above or the
# row would be dropped silently (tests/agent/test_training_route_coverage.py).
EXCLUDED_ROUTES: frozenset[str] = frozenset({"policy_guard", "privacy_guard"})


def legacy_route_supported(route: object) -> bool:
    """True when a row with only ``expected_route`` can become an example (or is excluded on purpose)."""
    return route in {"service", "knowledge", "knowledge_abstain", "availability", "emergency"}         or route in _LEGACY_ROUTE_COMMANDS or route in EXCLUDED_ROUTES


def _legacy_commands(row: dict[str, Any], route: object, utterance: str) -> list[dict[str, Any]] | None:
    if route == "service":
        goal = row.get("service_code")
        if not isinstance(goal, str) or service_definition(goal) is None:
            return None
        slots = row.get("expected_slots") if isinstance(row.get("expected_slots"), dict) else {}
        # Labels hold normalized values; a few-shot must only show slot text
        # that is literally present, exactly as the runtime validator demands.
        allowed = set(accepted_slots(goal))
        shown = [{"name": name, "text": str(value)} for name, value in slots.items()
                 if name in allowed and str(value) and str(value) in utterance]
        return [{"type": "StartGoal", "goal": goal, "slots": shown}]
    if route in {"knowledge", "knowledge_abstain"}:
        return [{"type": "AskInfo", "query": utterance}]
    if route == "availability":
        goal = row.get("service_code")
        if not isinstance(goal, str) or service_definition(goal) is None:
            return None
        return [{"type": "CheckAvailability", "goal": goal}]
    if route == "emergency":
        return [{"type": "Emergency"}]
    template = _LEGACY_ROUTE_COMMANDS.get(str(route))
    return [dict(template)] if template is not None else None


def normalized_situation_group(*values: object) -> str | None:
    """Unify historical concept and frame identifiers for leave-group-out scoring."""
    for value in values:
        text = _text(value, 128)
        if text:
            return text.casefold().removeprefix('frame-')
    return None


def _example(row: object) -> CommandExample | None:
    if not isinstance(row, dict) or row.get("split") != "train":
        return None
    utterance = _text(row.get("utterance"), 300)
    language = _text(row.get("language"), 16)
    if not utterance or not language:
        return None
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    pending_field = _text(context.get("pending_field"), 64) or None
    context_topic = _text(context.get("last_verified_topic"), 160) or None
    raw_commands = row.get("commands")
    if not (isinstance(raw_commands, list) and raw_commands):
        raw_commands = _legacy_commands(row, row.get("expected_route"), utterance)
    commands = commands_from_items(raw_commands) if raw_commands else None
    validated = validate_commands(
        commands, query=utterance, enabled_request_kinds=ACTION_REQUEST_KINDS,
        pending_reply=pending_field, language=language,
        require_evidence=False) if commands else None
    if not validated or len(validated) != len(commands):
        # A row whose commands do not survive the runtime validator would
        # teach the model an output the server rejects.
        return None
    public = tuple(command.public() for command in validated)
    goal = next((command.goal for command in validated if command.type == "StartGoal"), None)
    group = normalized_situation_group(row.get("frame_id"), row.get("concept_key"))
    return CommandExample(language, utterance, public, goal, group, pending_field, context_topic)


def load_command_examples(paths: Sequence[str | Path],
                          statuses: Iterable[str] | None = None) -> tuple[CommandExample, ...]:
    """Load train-split service/knowledge examples; evaluation data is never read here.

    ``statuses`` limits rows to those whose ``gold_status`` is listed
    (``nlu.service_selector.example_statuses``); ``None`` keeps every row.
    """
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
            if row_has_status(row, statuses) and (example := _example(row)) is not None:
                examples.append(example)
    return tuple(examples)


def load_configured_examples(root: str | Path | None = None,
                             statuses: Iterable[str] | None = None) -> tuple[CommandExample, ...]:
    """Load examples exactly as the runtime does (shared training files + configured statuses).

    Measurement and calibration tools use this so they never score a different
    example set than the server.  ``statuses`` overrides the configured list
    for controlled comparisons.
    """
    from concierge_kiosk.core.domain_profile import nlu_policy
    allowed = tuple(statuses) if statuses is not None else tuple(
        nlu_policy().service_selector["example_statuses"])
    return load_command_examples(training_agent_paths(root), allowed)


def nearest_label(scored: Sequence[tuple[float, CommandExample]]
                  ) -> tuple[str | None, float, float]:
    """Return (label, best score, best score of any other label) from ranked examples.

    Labels are :attr:`CommandExample.label` (``service:<code>``, ``askinfo``,
    ``chitchat:<kind>``, ``slot:<field>``, ...).  Shared by the runtime router,
    the model-free fallback and their calibration tool.
    """
    if not scored:
        return None, -1.0, -1.0
    best_score, best_example = scored[0]
    label = best_example.label
    runner_up = next((score for score, example in scored[1:] if example.label != label), -1.0)
    return label, best_score, runner_up


def example_eligible(example: CommandExample, pending_field: str | None,
                     context_topic: str | None = None) -> bool:
    """Slot-reply examples only count while the server is asking for that field, and
    follow-up examples only while the server holds a verified topic to point back at."""
    return ((example.pending_field is None or example.pending_field == pending_field)
            and (example.context_topic is None or bool(context_topic)))


class ServiceSelector:
    """Lazy, in-memory semantic index over the service catalog and reviewed examples."""

    def __init__(self, catalog_path: str | Path, embedder: _Embedder, *, top_k: int = 5,
                 examples: Sequence[CommandExample] = (), example_k: int = 2,
                 cache_dir: str | Path | None = None) -> None:
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
        # A composed turn is not a labelled example of its first goal. Only
        # independently owned, validated training spans teach service identity.
        self._goal_sources = tuple(dict.fromkeys(
            (command['goal'], command['source']) for example in self.examples
            if example.pending_field is None and example.context_topic is None
            for command in example.commands
            if command.get('type') == 'StartGoal' and command.get('goal')
            and isinstance(command.get('source'), str) and command['source']))
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._cache_path = self._build_cache_path()
        self._vectors: tuple[list[float], ...] | None = None
        self._example_vectors: tuple[list[float], ...] | None = None
        self._definition_vectors: tuple[list[float], ...] | None = None
        self._goal_vectors: tuple[list[float], ...] | None = None
        self._embedding_records: dict[str, list[float]] | None = None
        self._warming = False
        self._lock = threading.Lock()
        # `_lock` is held for a whole embedding pass; a guest turn must never wait for it, so the
        # warming flag has its own short-lived lock.
        self._state_lock = threading.Lock()
        # One guest turn may pass the same wording through layer B, layer C
        # candidate shaping, and the model-free fallback.  Query embeddings
        # are expensive, while the vectors are immutable; retain only a small
        # recency window so repeated turns cannot grow memory without bound.
        self._query_vectors: OrderedDict[str, list[float]] = OrderedDict()
        self._query_lock = threading.Lock()
        # Unit-normalised example matrix (built once per example index) and the
        # scored ranking of the last few query vectors: one turn asks for the same
        # ranking from the fast router, the grounded fast path and the candidate set.
        self._example_matrix: tuple[object, Any, Any] | None = None
        self._recent_scores: list[tuple[list[float], list[tuple[float, CommandExample]]]] = []

    def _query_vector(self, query: str) -> list[float]:
        """Return a cached query embedding keyed by stripped guest text."""
        key = query.strip()
        if not key:
            raise ValueError("query embedding requires non-empty text")
        with self._query_lock:
            vector = self._query_vectors.pop(key, None)
            if vector is None:
                vector = self.embedder.encode_query(key[:500])
            self._query_vectors[key] = vector
            while len(self._query_vectors) > 64:
                self._query_vectors.popitem(last=False)
            return vector

    def _build_cache_path(self) -> Path | None:
        """Build a cache key from the model identity and source content."""
        if self.cache_dir is None:
            return None
        model = str(getattr(self.embedder, 'model_name', '') or
                    getattr(self.embedder, 'model', '') or type(self.embedder).__name__)
        catalog_hash = hashlib.sha256(
            self.catalog_path.read_bytes()).hexdigest()
        examples_hash = hashlib.sha256(json.dumps(
            [{'language': item.language, 'utterance': item.utterance,
              'commands': item.commands, 'goal': item.goal, 'group': item.group}
             for item in self.examples],
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()
        key = hashlib.sha256(json.dumps({
            # top_k/example_k only bound the prompt; they never change a vector,
            # so they stay out of the key and sweeps reuse one embedding pass.
            'model': model, 'catalog': catalog_hash, 'examples': examples_hash,
            'criteria': {code: _identity_text(code, definition) for code, definition in SERVICE_DEFINITIONS.items()},
            'goal_sources': self._goal_sources,
            'manifest': getattr(self.embedder, 'manifest', None),
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        return self.cache_dir / f'{key}.json'

    def _embedding_identity(self):
        return {'model': str(getattr(self.embedder, 'model_name', type(self.embedder).__name__)),
                'manifest': getattr(self.embedder, 'manifest', None)}

    def _embed_texts(self, texts, *, query):
        """Reuse unchanged text vectors across corpus/criteria edits, with pinned identity.

        The complete index key still changes with commands and service criteria.
        This cache stores only index texts (training/catalog), never guest turns.
        """
        if self._embedding_records is None:
            self._embedding_records = {}
            path = self.cache_dir / 'embeddings.json' if self.cache_dir else None
            if path is not None and path.is_file() and not path.is_symlink():
                try:
                    payload = json.loads(path.read_text(encoding='utf-8'))
                    if payload.get('identity') == self._embedding_identity():
                        self._embedding_records = {key: vector for key, vector in payload.get('records', {}).items()
                                                   if isinstance(key, str) and valid_vector(vector)}
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
        role = 'query' if query else 'passage'
        keys = [role + '\0' + text for text in texts]
        missing = list(dict.fromkeys(text for key, text in zip(keys, texts) if key not in self._embedding_records))
        if missing:
            batch = getattr(self.embedder, 'encode_many', None)
            encoder = self.embedder.encode_query if query else getattr(self.embedder, 'encode_passage', self.embedder.encode_query)
            vectors = batch(missing) if batch is not None else [encoder(text) for text in missing]
            self._embedding_records.update((role + '\0' + text, vector) for text, vector in zip(missing, vectors))
        return tuple(self._embedding_records[key] for key in keys)

    def _ensure_definition_index(self):
        if self._definition_vectors is None:
            self._definition_vectors = self._embed_texts(
                [_identity_text(code, definition) for code, definition in SERVICE_DEFINITIONS.items()], query=False)
        return self._definition_vectors

    def _ensure_goal_index(self):
        if self._goal_vectors is None:
            self._goal_vectors = self._embed_texts([source for _, source in self._goal_sources], query=True)
        return self._goal_vectors

    @staticmethod
    def _valid_vectors(value: object, expected: int) -> tuple[list[float], ...] | None:
        if not isinstance(value, list) or len(value) != expected:
            return None
        vectors: list[list[float]] = []
        for vector in value:
            if (not isinstance(vector, list) or not vector or
                    any(type(item) not in {int, float} or not math.isfinite(float(item))
                        for item in vector)):
                return None
            vectors.append([float(item) for item in vector])
        return tuple(vectors)

    def _load_cache(self) -> bool:
        path = self._cache_path
        if path is None or not path.is_file() or path.is_symlink():
            return False
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            vectors = self._valid_vectors(payload.get('vectors'), len(self.entries))
            examples = self._valid_vectors(payload.get('example_vectors'), len(self.examples))
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            return False
        if vectors is None or (self.examples and examples is None):
            return False
        self._vectors = vectors
        self._example_vectors = examples or ()
        self._definition_vectors = self._valid_vectors(payload.get('definition_vectors'), len(SERVICE_DEFINITIONS))
        self._goal_vectors = self._valid_vectors(payload.get('goal_vectors'), len(self._goal_sources))
        return True

    def _save_cache(self) -> None:
        path = self._cache_path
        if path is None or self._vectors is None or (self.examples and self._example_vectors is None):
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({
                'version': 1, 'vectors': self._vectors,
                'example_vectors': self._example_vectors or (),
                'definition_vectors': self._definition_vectors,
                'goal_vectors': self._goal_vectors,
            }, separators=(',', ':')), encoding='utf-8')
            temporary.replace(path)
            if self._embedding_records:
                records = self.cache_dir / 'embeddings.json'
                temporary = records.with_suffix('.tmp')
                temporary.write_text(json.dumps({'identity': self._embedding_identity(),
                    'records': self._embedding_records}, separators=(',', ':')), encoding='utf-8')
                temporary.replace(records)
        except (OSError, TypeError, ValueError):
            # Cache writes are an optimization; a read-only or full data volume
            # must never prevent understanding from warming in memory.
            return

    def _ready_or_build(self) -> bool:
        """Return whether both indexes may be used now.

        Outside a guest turn (startup warm-up, tools, tests) the indexes are
        built synchronously. Inside a guest turn they are never built: the
        turn proceeds with what is ready and a background thread builds the
        rest, so a cold index can never consume the turn's time budget.
        """
        if (self._vectors is not None and (self._example_vectors is not None or not self.examples)
                and (self._goal_vectors is not None or not self._goal_sources)):
            return True
        from concierge_kiosk.runtime.local_http import in_guest_turn
        if not in_guest_turn():
            self.warm()
            return True
        with self._state_lock:
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
            with self._state_lock:
                self._warming = False

    def _ensure_index(self) -> tuple[list[float], ...]:
        vectors = self._vectors
        if vectors is not None:
            return vectors
        with self._lock:
            if self._vectors is None:
                if self._load_cache():
                    return self._vectors
                texts = [entry.embedding_text for entry in self.entries]
                self._vectors = self._embed_texts(texts, query=False)
                if not self.examples:
                    self._save_cache()
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
                self._example_vectors = self._embed_texts(texts, query=True)
                self._save_cache()
            return self._example_vectors

    def warm(self) -> None:
        """Build both indexes (and the example score matrix) ahead of the first guest turn."""
        self._ensure_index()
        if self.examples:
            self._matrix(self._ensure_example_index())
        self._ensure_goal_index()
        self._ensure_definition_index()
        self._save_cache()

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
                description=definition.description,
                score=-1.0,
            ))
        return result

    def _matrix(self, vectors: tuple[list[float], ...]):
        """Unit-normalised example matrix; rows whose vector is invalid score -1."""
        import numpy as np

        cached = self._example_matrix
        if cached is not None and cached[0] is vectors:
            return cached[1], cached[2]
        valid = np.array([valid_vector(vector) for vector in vectors], dtype=bool)
        width = max((len(vector) for vector, ok in zip(vectors, valid) if ok), default=0)
        matrix = np.zeros((len(vectors), width), dtype=np.float64)
        for row, (vector, ok) in enumerate(zip(vectors, valid)):
            if ok and len(vector) == width:
                matrix[row] = vector
            else:
                valid[row] = False
        norms = np.linalg.norm(matrix, axis=1)
        matrix[valid] /= norms[valid, None]
        self._example_matrix = (vectors, matrix, valid)
        return matrix, valid

    def _example_scores(self, query_vector: list[float]) -> list[tuple[float, CommandExample]]:
        if not self.examples:
            return []
        for vector, scored in self._recent_scores:
            if vector is query_vector:
                return scored
        import numpy as np

        matrix, valid = self._matrix(self._ensure_example_index())
        if not valid_vector(query_vector) or len(query_vector) != matrix.shape[1]:
            similarities = np.full(len(self.examples), -1.0)
        else:
            query = np.asarray(query_vector, dtype=np.float64)
            similarities = np.where(valid, matrix @ (query / np.linalg.norm(query)), -1.0)
        order = np.argsort(-similarities, kind='stable')
        scored = [(float(similarities[index]), self.examples[index]) for index in order]
        self._recent_scores = [(query_vector, scored), *self._recent_scores[:7]]
        return scored

    @observed('candidate_selection', project=lambda result: {'candidate_count': len(result[0])})
    def nonrequest_similarity(self, query: str) -> float | None:
        """Best similarity to a reviewed turn that requests no service (a question, thanks, a vague ask).

        The semantic gate requires a proposed service to be closer to the guest's words
        than any such turn, so what counts as "not a request" is taught by reviewed
        examples rather than by phrase lists. ``None`` while the index is not ready.
        """
        if not isinstance(query, str) or not query.strip() or not self._ready_or_build():
            return None
        for score, example in self._example_scores(self._query_vector(query)):
            if (example.pending_field is None and example.context_topic is None
                    and example.label != 'emergency'
                    and not any(command.get('type') == 'StartGoal' for command in example.commands)):
                return score
        return None

    def goal_ranking(self, query: str, *, enabled_request_kinds: frozenset[str]
                     ) -> tuple[tuple[str, float], ...]:
        """Every enabled goal with its best similarity to catalog text or a reviewed turn.

        Best first. Empty while the indexes are not ready (a guest turn never
        builds them). Used as an independent signal by the semantic gate.
        """
        if not isinstance(query, str) or not query.strip() or not self._ready_or_build():
            return ()
        query_vector = self._query_vector(query)
        best: dict[str, float] = {}
        for score, example in self._example_scores(query_vector):
            if (example.goal is not None and len(example.commands) == 1 and example.pending_field is None
                    and example.context_topic is None and example.goal not in best):
                best[example.goal] = score
        for (goal, _), vector in zip(self._goal_sources, self._goal_vectors or ()):
            best[goal] = max(best.get(goal, -1.0), cosine(query_vector, vector))
        for entry, vector in zip(self.entries, self._ensure_index()):
            mode = self._mode_for_entry(entry, enabled_request_kinds)
            if mode is not None and service_definition(mode) is not None:
                best[mode] = max(best.get(mode, -1.0), cosine(query_vector, vector))
        for (mode, definition), vector in zip(SERVICE_DEFINITIONS.items(), self._definition_vectors or ()):
            if definition.request_kind in enabled_request_kinds:
                best[mode] = max(best.get(mode, -1.0), cosine(query_vector, vector))
        enabled = {mode for mode in best
                   if (definition := service_definition(mode)) is not None
                   and definition.request_kind in enabled_request_kinds}
        return tuple(sorted(((mode, score) for mode, score in best.items() if mode in enabled),
                            key=lambda item: (-item[1], item[0])))

    def understand(self, query: str, *, language: str,
                   enabled_request_kinds: frozenset[str],
                   pending_field: str | None = None,
                   context_topic: str | None = None
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
        query_vector = self._query_vector(query)
        scored_examples = self._example_scores(query_vector)
        example_best: dict[str, float] = {}
        for score, example in scored_examples:
            if example.goal is not None and len(example.commands) == 1 and example.goal not in example_best:
                example_best[example.goal] = score
        for mode, score in self.goal_ranking(query, enabled_request_kinds=enabled_request_kinds):
            example_best[mode] = max(example_best.get(mode, -1.0), score)

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
                description=service_definition(mode).description, score=combined(mode, score))
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
        # The command model sees the complete enabled registry. Its shortlist
        # ranks services; it must not suppress compositional demonstrations.
        offered = {code for code, definition in SERVICE_DEFINITIONS.items()
                   if definition.request_kind in enabled_request_kinds}
        usable = [example for _, example in scored_examples
                  if example_eligible(example, pending_field, context_topic)
                  and example.label != "emergency"
                  and all(command.get("goal") in offered for command in example.commands
                          if command.get("type") == "StartGoal")]
        chosen = usable[:self.example_k]
        if self.example_k > 1:
            composed = [example for example in usable if len(example.commands) > 1]
            demo = next((example for example in composed if example.language == language),
                        composed[0] if composed else None)
            if demo is not None and not any(len(example.commands) > 1 for example in chosen):
                chosen = chosen[:self.example_k - 1] + [demo]
        if context_topic and self.example_k and not any(e.context_topic for e in chosen):
            # With a verified topic in play, one slot shows how a turn that points back at
            # it is expressed (and one that does not), so the model sees the mechanism.
            demo = next((e for e in usable if e.context_topic), None)
            if demo is not None:
                chosen = chosen[:self.example_k - 1] + [demo]

        def shot(example: CommandExample) -> dict[str, Any]:
            item: dict[str, Any] = {"guest_turn": example.utterance, "commands": list(example.commands)}
            if example.context_topic:
                item["context"] = {"last_verified_topic": example.context_topic}
            return item

        return tuple(item.public() for item in selected), tuple(shot(e) for e in chosen)

    def nearest(self, query: str, *, min_score: float, min_margin: float,
                pending_field: str | None = None) -> CommandExample | None:
        """Return the nearest reviewed example when it is confident enough.

        Model-free understanding: similarity to reviewed training turns only,
        never a keyword list.  It answers only when the best label is close
        enough and clearly ahead of every other label (thresholds calibrated
        on the training split, leave-one-situation-out); otherwise ``None``.
        """
        if (not isinstance(query, str) or not query.strip() or not self.examples
                or not self._ready_or_build()):
            return None
        scored = [(score, example) for score, example
                  in self._example_scores(self._query_vector(query))
                  if example_eligible(example, pending_field)]
        label, best, runner_up = nearest_label(scored)
        if label is None or best < min_score or best - runner_up < min_margin:
            return None
        return scored[0][1]

    def fallback_commands(self, query: str, *, language: str, enabled_request_kinds: frozenset[str],
                          min_score: float, min_margin: float,
                          pending_field: str | None = None) -> tuple[dict[str, Any], ...] | None:
        """Commands for ``query`` taken from its nearest confident example.

        Only the example's *intent* transfers.  Slot text, preferences and a
        language target belong to the example's own words, so those are never
        copied; the runtime asks for missing slots instead.
        """
        example = self.nearest(query, min_score=min_score, min_margin=min_margin,
                               pending_field=pending_field)
        if example is None or len(example.commands) != 1:
            return None
        command = example.commands[0]
        kind = command.get("type")
        if kind == "StartGoal":
            definition = service_definition(str(command.get("goal") or ""))
            if definition is None or definition.request_kind not in enabled_request_kinds:
                return None
            return ({"type": "StartGoal", "goal": definition.code, "slots": []},)
        if kind == "CheckAvailability":
            definition = service_definition(str(command.get("goal") or ""))
            if definition is None or definition.request_kind not in enabled_request_kinds:
                return None
            return ({"type": "CheckAvailability", "goal": definition.code},)
        if kind in {"AskInfo", "Navigate", "Plan"}:
            return ({"type": kind, "query": query[:300]},)
        if kind in {"SetSlot", "CorrectSlot"} and pending_field:
            return ({"type": kind, "field": pending_field, "value": query.strip()[:120]},)
        if kind in {"Cancel", "Modify", "AskStatus", "Clarify", "ChitChat", "Handoff"}:
            return (dict(command),)
        return None

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


__all__ = ["CommandExample", "ServiceCandidate", "ServiceSelector", "example_eligible",
           "load_command_examples", "load_configured_examples", "nearest_label"]
