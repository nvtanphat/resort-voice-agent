"""Guardrails for moving language and property-specific data out of Python."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src" / "concierge_kiosk"
ALLOWLIST_PATH = ROOT / "tests" / "hardcode_allowlist.txt"
ALLOWLIST_MAX_LINES = 106
SUPPORTED_LANGUAGE_CODES = frozenset({"vi", "en", "zh", "ko"})
UNICODE_LITERAL = re.compile(r"[\u00c0-\u1ef9\u3040-\u9fff\uac00-\ud7af]")


def _allowlist() -> dict[tuple[str, int], str]:
    entries: dict[tuple[str, int], str] = {}
    for line_number, raw_line in enumerate(
        ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[0] or not parts[1].isdigit() or not parts[2].strip():
            raise AssertionError(
                f"{ALLOWLIST_PATH}:{line_number}: expected file:line:reason"
            )
        key = (parts[0], int(parts[1]))
        if key in entries:
            raise AssertionError(f"duplicate allowlist entry: {parts[0]}:{parts[1]}")
        entries[key] = parts[2].strip()
    return entries


def _violations() -> dict[tuple[str, int], set[str]]:
    violations: dict[tuple[str, int], set[str]] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative_parts = path.relative_to(SRC_ROOT).parts
        if "i18n" in relative_parts:
            continue
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        for node in ast.walk(tree):
            kinds: set[str] = set()
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if UNICODE_LITERAL.search(node.value):
                    kinds.add("unicode literal")
                if "FURAMA_DANANG" in node.value or "furama" in node.value.lower():
                    kinds.add("Furama literal")
            elif isinstance(node, (ast.Set, ast.Tuple, ast.List)):
                values = {
                    item.value
                    for item in node.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
                if SUPPORTED_LANGUAGE_CODES.issubset(values):
                    kinds.add("hard-coded language set")
            if kinds:
                violations.setdefault((relative_path, node.lineno), set()).update(kinds)
    return violations


def test_hardcoded_values_are_allowlisted() -> None:
    allowlist = _allowlist()
    violations = _violations()
    unexpected = sorted(set(violations) - set(allowlist))
    stale = sorted(set(allowlist) - set(violations))
    messages = []
    if unexpected:
        messages.append("unexpected hard-coded values:\n" + "\n".join(
            f"  {path}:{line}: {', '.join(sorted(violations[(path, line)]))}"
            for path, line in unexpected
        ))
    if stale:
        messages.append("stale allowlist entries (remove them):\n" + "\n".join(
            f"  {path}:{line}" for path, line in stale
        ))
    if messages:
        pytest.fail("\n".join(messages))


def test_allowlist_does_not_grow() -> None:
    assert len(_allowlist()) <= ALLOWLIST_MAX_LINES


def test_service_action_metadata_is_data_driven() -> None:
    service_actions = (SRC_ROOT / "application" / "service_actions.py").read_text(encoding="utf-8")
    state = (SRC_ROOT / "agent" / "runtime" / "state.py").read_text(encoding="utf-8")
    assert "mode == 'dining_reservation'" not in service_actions
    assert "entity_type') != 'restaurant'" not in service_actions
    assert "mode == 'dining_reservation'" not in state
    assert "applies_to_slot" in (ROOT / "config" / "agent-domain.json").read_text(encoding="utf-8")
    assert "venue_slot" in (ROOT / "config" / "agent-domain.json").read_text(encoding="utf-8")


def test_service_goal_is_not_selected_from_an_embedded_alias() -> None:
    selector = (SRC_ROOT / "agent" / "understanding" / "service_selector.py").read_text(encoding="utf-8")
    engine = (SRC_ROOT / "application" / "conversation" / "engine.py").read_text(encoding="utf-8")
    assert "catalog_service_goal_in_text" not in selector
    assert "catalog_service_goal_in_text" not in engine
    assert "exact_catalog_service_goal" not in selector
    assert "exact_catalog_service_goal" not in engine
