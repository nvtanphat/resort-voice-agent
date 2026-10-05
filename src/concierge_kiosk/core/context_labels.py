"""Data-owned fact-context labels and locale aliases."""
from __future__ import annotations

import json
import unicodedata
from functools import lru_cache

from concierge_kiosk.core.dataset_layout import CONTEXT_LABELS, dataset_path
from concierge_kiosk.core.domain_profile import supported_languages


@lru_cache(maxsize=1)
def context_terms() -> dict[str, dict[str, tuple[str, ...]]]:
    """Return canonical context labels plus approved locale aliases."""
    path = dataset_path(CONTEXT_LABELS)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    labels = payload.get("labels") if isinstance(payload, dict) else None
    if not isinstance(labels, dict):
        return {}
    attribute_terms = _fact_type_terms(payload.get("fact_types"))
    result: dict[str, dict[str, tuple[str, ...]]] = {}
    for context, spec in labels.items():
        if not isinstance(spec, dict):
            continue
        by_language: dict[str, tuple[str, ...]] = {}
        aliases = spec.get("aliases") if isinstance(spec.get("aliases"), dict) else {}
        for language in supported_languages():
            values = [spec.get(language)]
            values.extend(aliases.get(language, ()) if isinstance(aliases.get(language, ()), list) else ())
            # An attribute word ("opening hours", "giờ mở cửa", "연락처") names
            # what is asked, not which variant: treating it as a context would
            # filter out every other variant (lifeguard vs. daily hours).
            clean = tuple(dict.fromkeys(
                value.strip() for value in values
                if isinstance(value, str) and value.strip()
                and _key(value) not in attribute_terms.get(language, frozenset())))
            if clean:
                by_language[language] = clean
        if by_language:
            result[str(context)] = by_language
    return result


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _fact_type_terms(fact_types: object) -> dict[str, frozenset[str]]:
    """Locale labels and aliases of attributes (``fact_types``)."""
    terms: dict[str, set[str]] = {}
    if not isinstance(fact_types, dict):
        return {}
    for spec in fact_types.values():
        if not isinstance(spec, dict):
            continue
        aliases = spec.get("aliases") if isinstance(spec.get("aliases"), dict) else {}
        for language in supported_languages():
            values = [spec.get(language), *(aliases.get(language) or ())]
            terms.setdefault(language, set()).update(
                _key(value) for value in values if isinstance(value, str) and value.strip())
    return {language: frozenset(values) for language, values in terms.items()}
