"""Every service a dataset row names must exist in the runtime service registry.

The labels in training/evaluation data and the registry the agent validates against are
maintained separately; this keeps them from drifting apart silently.
"""
from __future__ import annotations

import json

import pytest

from concierge_kiosk.core.dataset_layout import dataset_root
from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS

# Label used by the simulation event log for "no service"; it is not a registry service.
NON_SERVICE_LABELS = frozenset({"knowledge_only"})
SCANNED = ("training", "evaluation")
SKIPPED_PARTS = frozenset({"quarantine"})


def _rows():
    root = dataset_root()
    for folder in SCANNED:
        for path in sorted((root / folder).rglob("*.jsonl")):
            if SKIPPED_PARTS & set(path.parts):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield path.relative_to(root).as_posix(), number, row


@pytest.mark.skipif(not dataset_root().is_dir(), reason="datasets/ not present")
def test_dataset_service_codes_and_command_goals_exist_in_the_registry():
    unknown: dict[tuple[str, str], int] = {}
    for name, _, row in _rows():
        codes = [row.get("service_code")]
        codes += [command.get("goal") for command in row.get("commands") or ()
                  if isinstance(command, dict) and command.get("type") in {"StartGoal", "CheckAvailability"}]
        for code in codes:
            if isinstance(code, str) and code and code not in SERVICE_DEFINITIONS \
                    and code not in NON_SERVICE_LABELS:
                unknown[(name, code)] = unknown.get((name, code), 0) + 1
    assert not unknown, f"service codes missing from the registry: {unknown}"
