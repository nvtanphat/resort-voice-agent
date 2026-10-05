"""Validation of the ``security`` section of the agent domain profile."""
from __future__ import annotations

from typing import Any

from .common import compile_regex


def validate_security(payload: dict[str, Any]) -> None:
    security = payload["security"]
    for key in ("blocked_clarification_patterns", "sensitive_patterns"):
        for index, pattern in enumerate(security[key]):
            compile_regex(pattern, label=f"security.{key}.{index}")
