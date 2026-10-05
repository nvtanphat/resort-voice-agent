"""Checksum-pinned runtime/deployment profile.

Runtime choices (models, feature flags, budgets and timeouts) are operator data,
not agent-domain data. Security/validation bounds stay in Python and JSON Schema.
Environment variables may override individual profile values at process startup.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

SUPPORTED_RUNTIME_SCHEMA_VERSION = 1
_MAX_PROFILE_BYTES = 64_000
_MAX_SCHEMA_BYTES = 64_000
_ALLOWED_PROFILE_NAMES = frozenset({"test", "development", "edge", "production"})


@dataclass(frozen=True)
class RuntimeProfile:
    schema_version: int
    profile_id: str
    models: Mapping[str, Any]
    features: Mapping[str, bool]
    budgets: Mapping[str, Mapping[str, Any]]
    timeouts: Mapping[str, float]
    sha256: str
    source_path: Path

    def embedding_assets(self) -> tuple[str, str]:
        cfg = self.models["embedding"]
        if cfg["selection"] == "disabled":
            return "", ""
        for item in cfg["candidates"]:
            if str(item["model_path"]).startswith("ollama://"):
                # Ollama keeps the weights; only a pin manifest can be on disk.
                if not item["manifest_path"]:
                    return str(item["model_path"]), ""
                manifest = Path(item["manifest_path"])
                if manifest.is_file() and not manifest.is_symlink():
                    return str(item["model_path"]), str(item["manifest_path"])
                continue
            model = Path(item["model_path"])
            manifest = Path(item["manifest_path"])
            if ((model.is_dir() or model.is_file()) and not model.is_symlink()
                    and manifest.is_file() and not manifest.is_symlink()):
                return str(item["model_path"]), str(item["manifest_path"])
        return "", ""


def _project_root_candidate() -> Path:
    return Path(__file__).resolve().parents[3]


def _profile_candidates(name: str) -> tuple[Path, ...]:
    return (
        _project_root_candidate() / "config" / "runtime-profiles" / f"{name}.json",
        Path(sys.prefix) / "share" / "concierge-kiosk" / "config" / "runtime-profiles" / f"{name}.json",
    )


def _schema_candidates() -> tuple[Path, ...]:
    return (
        _project_root_candidate() / "config" / "runtime-profile.schema.json",
        Path(sys.prefix) / "share" / "concierge-kiosk" / "config" / "runtime-profile.schema.json",
    )


def _first_existing(candidates: tuple[Path, ...], *, label: str) -> Path:
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise ValueError(f"{label} unavailable")


def default_runtime_profile_binding(environment: str) -> tuple[str, str]:
    configured_path = os.getenv("CONCIERGE_RUNTIME_PROFILE_PATH", "").strip()
    if configured_path:
        path = Path(configured_path)
    else:
        selected = os.getenv("CONCIERGE_RUNTIME_PROFILE", "").strip().lower() or environment
        if selected not in _ALLOWED_PROFILE_NAMES:
            raise ValueError("Unknown runtime profile")
        path = _first_existing(_profile_candidates(selected), label="Runtime profile")
    if not path.is_file() or path.is_symlink():
        raise ValueError("Runtime profile unavailable")
    checksum = os.getenv("CONCIERGE_RUNTIME_PROFILE_SHA256", "").strip().lower()
    if not checksum:
        sidecar = path.with_suffix(".sha256")
        if not sidecar.is_file() or sidecar.is_symlink() or sidecar.stat().st_size > 256:
            raise ValueError("Pinned runtime profile SHA-256 unavailable")
        checksum = sidecar.read_text(encoding="ascii").strip().lower()
    if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        raise ValueError("Invalid pinned runtime profile SHA-256")
    return str(path), checksum


def _schema_path() -> Path:
    return _first_existing(_schema_candidates(), label="Runtime profile schema")


def _read_json(path: Path, max_bytes: int, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError(f"Invalid {label}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid UTF-8 {label} JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value, raw


def _semantic_validate(payload: dict[str, Any]) -> None:
    if payload["schema_version"] != SUPPORTED_RUNTIME_SCHEMA_VERSION:
        raise ValueError("Unsupported runtime profile schema version")
    agent = payload["budgets"]["agent"]
    if agent["max_planner_calls"] > agent["max_steps"]:
        raise ValueError("Runtime planner-call budget exceeds step budget")
    if agent["max_read_calls"] > agent["max_steps"]:
        raise ValueError("Runtime read-call budget exceeds step budget")
    features = payload["features"]
    if features["voice_preview_stability"] and not features["voice_windowed_preview"]:
        raise ValueError("Voice preview stability requires windowed preview")
    slm = payload["models"]["slm"]
    if bool(slm["base_url"]) != bool(slm["primary_model"]):
        raise ValueError("Runtime SLM endpoint and primary model must be configured together")
    if slm["fallback_model"] and slm["fallback_model"] == slm["primary_model"]:
        raise ValueError("Runtime SLM fallback must differ from primary")
    if slm["strict_mode"] and slm["fallback_model"]:
        raise ValueError("Strict runtime profile must not configure an SLM fallback")
    embedding = payload["models"]["embedding"]
    if embedding["selection"] == "disabled" and embedding["candidates"]:
        raise ValueError("Disabled embedding profile cannot declare candidates")


@lru_cache(maxsize=16)
def load_runtime_profile(path_value: str, expected_sha256: str) -> RuntimeProfile:
    path = Path(path_value)
    payload, raw = _read_json(path, _MAX_PROFILE_BYTES, "runtime profile")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256.lower():
        raise ValueError("Runtime profile SHA-256 mismatch")
    schema, _ = _read_json(_schema_path(), _MAX_SCHEMA_BYTES, "runtime profile schema")
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(p) for p in first.absolute_path) or "<root>"
        raise ValueError(f"Runtime profile schema validation failed at {location}: {first.message}")
    _semantic_validate(payload)
    return RuntimeProfile(
        schema_version=payload["schema_version"],
        profile_id=payload["profile_id"],
        models=payload["models"],
        features=payload["features"],
        budgets=payload["budgets"],
        timeouts=payload["timeouts"],
        sha256=actual,
        source_path=path,
    )
