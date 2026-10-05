"""Shipped env templates must pin the artifacts that are actually in the repo."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAIRS = {
    "CONCIERGE_DOMAIN_PROFILE": "CONCIERGE_DOMAIN_PROFILE_PATH",
    "CONCIERGE_PROPERTY_PROFILE": "CONCIERGE_PROPERTY_PROFILE_PATH",
    "CONCIERGE_MAP_RELEASE": "CONCIERGE_MAP_RELEASE_PATH",
    "CONCIERGE_PLANNING_RELEASE": "CONCIERGE_PLANNING_RELEASE_PATH",
}
DEFAULT_PATHS = {"CONCIERGE_DOMAIN_PROFILE_PATH": "config/agent-domain.json"}


def _env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"\s*([A-Z0-9_]+)=(.*)", line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


@pytest.mark.parametrize("template", [".env.example", "config/local-runtime.env.example"])
def test_env_template_sha256_pins_match_repository_artifacts(template: str):
    values = _env(ROOT / template)
    checked = 0
    for prefix, path_key in PAIRS.items():
        digest = values.get(prefix + "_SHA256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        relative = values.get(path_key) or DEFAULT_PATHS.get(path_key)
        assert relative, f"{template}: {prefix}_SHA256 is pinned without a path"
        raw = (ROOT / relative).read_bytes()
        if Path(relative).suffix == ".json":
            raw = raw.replace(b"\r\n", b"\n")
        actual = hashlib.sha256(raw).hexdigest()
        assert digest == actual, f"{template}: stale {prefix}_SHA256 for {relative}"
        checked += 1
    assert checked >= 3
