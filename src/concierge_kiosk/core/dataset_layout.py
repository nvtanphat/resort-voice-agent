"""Canonical paths for the structured dataset and its derived inputs.

The application deliberately treats ``datasets/`` as a layout, not as a
property-specific directory.  Keeping the paths here prevents readers and
build tools from drifting away from the canonical layout.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
DATASET_ENV: Final[str] = "CONCIERGE_STRUCTURED_DATASET_DIR"
DEFAULT_DATASET_ROOT: Final[Path] = PROJECT_ROOT / "datasets"

# Canonical, verified property data.
FACTS: Final[str] = "knowledge/canonical/facts.jsonl"
ENTITIES: Final[str] = "knowledge/canonical/entities.jsonl"
RELATIONS: Final[str] = "knowledge/canonical/entity_relations.jsonl"
ALIASES: Final[str] = "knowledge/canonical/aliases.json"
SERVICE_CATALOG: Final[str] = "knowledge/canonical/service_catalog.json"
PROPERTY: Final[str] = "knowledge/canonical/property_profile.json"
MAP: Final[str] = "knowledge/canonical/map.json"
MAP_PATHS: Final[str] = "knowledge/canonical/map_paths.json"
PLANNING: Final[str] = "knowledge/canonical/planning.json"
CONTACTS: Final[str] = "knowledge/canonical/contacts.json"
DEPARTMENTS: Final[str] = "knowledge/canonical/departments.json"
KNOWLEDGE_MANIFEST: Final[str] = "knowledge/manifest.json"
SOURCES: Final[str] = "knowledge/sources/verified_sources.jsonl"
SNAPSHOTS: Final[str] = "knowledge/sources/verification_snapshots.jsonl"
FRESHNESS: Final[str] = "knowledge/canonical/freshness.jsonl"
CONTEXT_LABELS: Final[str] = "knowledge/canonical/context_labels.json"

# Operational data is intentionally separated from canonical RAG truth.
QUARANTINE_FACTS: Final[str] = "quarantine/facts.jsonl"
WORKFLOWS: Final[str] = "synthetic/operations/workflows/guest_service_workflows.jsonl"
CHANNEL_DISPATCH: Final[str] = "synthetic/operations/workflows/channel_dispatch_rules.json"
SERVICE_POLICIES: Final[str] = "synthetic/operations/policies/service_policies.json"
ESCALATION_MATRIX: Final[str] = "synthetic/operations/policies/escalation_matrix.json"
STAFF_DEPARTMENTS: Final[str] = "synthetic/operations/staffing/departments.json"
OPS_COVERAGE: Final[str] = "synthetic/operations/metadata/coverage.json"
RESTAURANTS: Final[str] = "synthetic/operations/food_beverage/restaurants.json"
ROOM_SERVICE_MENU: Final[str] = "synthetic/operations/food_beverage/room_service_menu.json"
MINIBAR: Final[str] = "synthetic/operations/food_beverage/minibar.json"
SPA_OPERATIONS: Final[str] = "synthetic/operations/spa/operations.json"
TOUR_PRODUCTS: Final[str] = "synthetic/operations/tours/products.json"
TOUR_OPERATIONS: Final[str] = "synthetic/operations/tours/operations.json"
TRANSPORT_PRODUCTS: Final[str] = "synthetic/operations/transport/products.json"
SHUTTLE_SCHEDULE: Final[str] = "synthetic/operations/transport/shuttle_schedule.json"
COMMERCIAL_SNAPSHOT: Final[str] = "synthetic/operations/inventory/commercial_snapshot.json"
ROOM_INVENTORY: Final[str] = "synthetic/operations/rooms/inventory.jsonl"
PMS_STAYS: Final[str] = "synthetic/operations/pms/stays.jsonl"
CHARGE_RULES: Final[str] = "synthetic/operations/billing/charge_rules.json"

# Evaluation/training inputs are dataset paths too, but are never RAG inputs.
EVAL_GOLD: Final[str] = "evaluation/gold"
EVAL_SERVICE_ACTIONS: Final[str] = "evaluation/end_to_end/service_actions.jsonl"
EVAL_VOICE_ASR: Final[str] = "evaluation/voice_text/vi_asr_robustness.jsonl"
EVAL_MULTI_TURN_VI: Final[str] = "evaluation/end_to_end/journeys/vi_multi_turn.jsonl"
TRAIN_AGENT_VI_GOLD: Final[str] = "training/agent/vi_gold.jsonl"
TRAIN_AGENT_MULTILINGUAL: Final[str] = "training/agent/multilingual_support.jsonl"


def dataset_root(root: str | Path | None = None) -> Path:
    """Return the configured dataset root as an absolute path.

    ``root`` is used by tests and isolated tools.  When omitted, the same
    environment variable used by the runtime is honoured, with ``datasets``
    as the repository-local default.
    """
    value = root if root is not None else os.getenv(DATASET_ENV, "datasets")
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def dataset_path(relative: str | Path, root: str | Path | None = None) -> Path:
    """Resolve one layout-relative dataset path below ``dataset_root``."""
    return dataset_root(root) / Path(relative)


def canonical_text_bytes(path: Path) -> bytes:
    """Read a dataset text artifact using platform-independent LF bytes."""
    return path.read_bytes().replace(b"\r\n", b"\n")
