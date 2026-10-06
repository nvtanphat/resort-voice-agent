"""Typed accessors over the cached agent domain profile."""
from __future__ import annotations

from typing import Any
from typing import Mapping

from .loader import get_domain_profile
from .models import MemoryPolicy, NluPolicy, PlanningPolicy, PreferencePolicy, RagPolicy, SecurityPolicy, ToolDocumentation, UiPolicy


def supported_languages() -> frozenset[str]:
    return get_domain_profile().languages


def request_kinds() -> frozenset[str]:
    return get_domain_profile().request_kinds


def preference_policy() -> PreferencePolicy:
    return get_domain_profile().preferences


def nlu_policy() -> NluPolicy:
    return get_domain_profile().nlu


def memory_policy() -> MemoryPolicy:
    return get_domain_profile().memory_policy


def planning_policy() -> PlanningPolicy:
    return get_domain_profile().planning


def security_policy() -> SecurityPolicy:
    return get_domain_profile().security


def rag_policy() -> RagPolicy:
    return get_domain_profile().rag


def voice_policy() -> Mapping[str, Any]:
    return get_domain_profile().voice


def ui_policy() -> UiPolicy:
    return get_domain_profile().ui
