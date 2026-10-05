"""Pinned profile/schema/vocabulary file resolution and byte-level integrity checks."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from typing import Mapping


MAX_PROFILE_BYTES = 256_000


MAX_SCHEMA_BYTES = 128_000


_MAX_DOMAIN_VOCAB_BYTES = 2_000_000


def _project_root_candidate() -> Path:
    # .../src/concierge_kiosk/core/domain_profile/files.py -> project root
    return Path(__file__).resolve().parents[4]


def _default_profile_candidates() -> tuple[Path, ...]:
    return (
        _project_root_candidate() / "config" / "agent-domain.json",
        Path(sys.prefix) / "share" / "concierge-kiosk" / "config" / "agent-domain.json",
    )


def _default_schema_candidates() -> tuple[Path, ...]:
    return (
        _project_root_candidate() / "config" / "agent-domain.schema.json",
        Path(sys.prefix) / "share" / "concierge-kiosk" / "config" / "agent-domain.schema.json",
    )


def _first_existing(candidates: tuple[Path, ...], *, label: str) -> Path:
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise ValueError(f"{label} unavailable")


def default_domain_profile_binding() -> tuple[str, str]:
    """Resolve the default pinned profile path and checksum without loading it."""
    configured = os.getenv("CONCIERGE_DOMAIN_PROFILE_PATH", "").strip()
    path = Path(configured) if configured else _first_existing(
        _default_profile_candidates(), label="Agent domain profile")
    checksum = os.getenv("CONCIERGE_DOMAIN_PROFILE_SHA256", "").strip().lower()
    if not checksum:
        sidecar = path.with_suffix(".sha256")
        if not sidecar.is_file() or sidecar.is_symlink() or sidecar.stat().st_size > 256:
            raise ValueError("Pinned agent domain SHA-256 unavailable")
        checksum = sidecar.read_text(encoding="ascii").strip().lower()
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise ValueError("Invalid pinned agent domain SHA-256")
    return str(path), checksum


def resolve_schema_path(explicit: str | Path | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file() or path.is_symlink():
            raise ValueError("Agent domain schema unavailable")
        return path
    return _first_existing(_default_schema_candidates(), label="Agent domain schema")


def read_json(path: Path, *, max_bytes: int, label: str) -> tuple[dict[str, Any], bytes]:
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


def json_digest_matches(raw: bytes, expected: str) -> bool:
    return expected in {
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
    }


def load_domain_vocab(profile_path: Path, spec: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Load and pin the property vocabulary release, if the profile declares one."""
    if spec is None:
        return {}
    relative = Path(str(spec.get("path", "")))
    candidates = ((profile_path.parent / relative,) if relative.is_absolute() else
                  (profile_path.parent / relative, _project_root_candidate() / relative))
    source = next((path for path in candidates if path.is_file() and not path.is_symlink()), None)
    if source is None:
        raise ValueError("Domain vocabulary release unavailable")
    payload, raw = read_json(source, max_bytes=_MAX_DOMAIN_VOCAB_BYTES,
                              label="domain vocabulary release")
    expected = str(spec.get("sha256", "")).lower()
    if not json_digest_matches(raw, expected):
        raise ValueError("Domain vocabulary release SHA-256 mismatch")
    if payload.get("schema_version") != 1 or not isinstance(payload.get("entities"), list) or not isinstance(payload.get("services"), list):
        raise ValueError("Invalid domain vocabulary release")
    return payload
