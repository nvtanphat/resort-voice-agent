"""Every route label in the training data must have a defined path to a command.

A bare ``expected_route`` row is converted to commands by
``service_selector._legacy_commands``.  A route with no conversion is dropped
silently, so new labels fail here instead of vanishing from the example set.
Rows that carry explicit ``commands`` do not depend on the conversion.
"""
from __future__ import annotations

import json

import pytest

from concierge_kiosk.agent.understanding.service_selector import (
    EXCLUDED_ROUTES, legacy_route_supported, load_command_examples,
)
from concierge_kiosk.core.dataset_layout import dataset_root, training_agent_paths


def _train_rows():
    for path in training_agent_paths():
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("split") == "train" and row.get("utterance"):
                yield path.name, row


@pytest.mark.skipif(not dataset_root().is_dir(), reason="datasets/ not present")
def test_every_bare_training_route_has_a_conversion():
    unmapped: dict[str, int] = {}
    for _, row in _train_rows():
        route = row.get("expected_route")
        if row.get("commands") or legacy_route_supported(route):
            continue
        unmapped[str(route)] = unmapped.get(str(route), 0) + 1
    assert not unmapped, f"training routes with no command conversion: {unmapped}"


def test_excluded_routes_are_not_converted_to_commands():
    assert EXCLUDED_ROUTES and all(legacy_route_supported(route) for route in EXCLUDED_ROUTES)


@pytest.mark.skipif(not dataset_root().is_dir(), reason="datasets/ not present")
def test_status_filter_keeps_only_reviewed_rows():
    paths = training_agent_paths()
    everything = load_command_examples(paths)
    reviewed = load_command_examples(paths, ("GOLD",))
    assert len(reviewed) < len(everything)
    assert load_command_examples(paths, ()) == ()
