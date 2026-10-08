"""Read-only access to the pinned property vocabulary release."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from concierge_kiosk.core.domain_profile import get_domain_profile


def _terms(item: dict[str, Any], language: str) -> tuple[str, ...]:
    values: list[str] = []
    for field in ("names", "aliases"):
        entries = item.get(field, {}).get(language, ())
        values.extend(value for value in entries if isinstance(value, str) and value.strip())
    return tuple(dict.fromkeys(values))


@lru_cache(maxsize=16)
def entity_terms(language: str, category: str | None = None) -> tuple[str, ...]:
    vocab = get_domain_profile().domain_vocab
    return tuple(dict.fromkeys(
        term for item in vocab.get("entities", ())
        if isinstance(item, dict) and (category is None or item.get("category") == category)
        for term in _terms(item, language)))


@lru_cache(maxsize=16)
def service_terms(language: str) -> tuple[str, ...]:
    vocab = get_domain_profile().domain_vocab
    return tuple(dict.fromkeys(
        term for item in vocab.get("services", ()) if isinstance(item, dict)
        for term in _terms(item, language)))


