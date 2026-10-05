"""Shared helpers for agent domain profile validation."""
from __future__ import annotations

import re
from typing import Any
from typing import Mapping


def compile_regex(pattern: str, *, label: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex in {label}") from exc


def validate_language_keys(value: Mapping[str, Any], languages: set[str], *, label: str,
                            require_all: bool = False) -> None:
    unknown = set(value) - languages
    if unknown:
        raise ValueError(f"{label} references unsupported language(s): " + ", ".join(sorted(unknown)))
    if require_all and set(value) != languages:
        raise ValueError(f"{label} must cover every configured language")
