"""Shared property terminology normalization.

Aliases are release data, not tokenizer exceptions.  This small adapter turns
equivalent surface forms (for example ``Wi-Fi`` and ``wifi``) into one search
form for lexical NLU/RAG and speech rendering.  It is deliberately bounded to
the checked-in aliases file and fails closed when a property has no file.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from concierge_kiosk.core.dataset_layout import ALIASES, dataset_path, dataset_root


def _dataset_dir() -> Path:
    configured = os.getenv('CONCIERGE_STRUCTURED_DATASET_DIR', '').strip()
    return dataset_root(configured or None)


def _compact(value: str) -> str:
    return ''.join(char for char in unicodedata.normalize('NFKC', value).casefold()
                   if char.isalnum())


def _pattern(value: str) -> str:
    escaped = re.escape(value)
    if all(not char.isascii() or not char.isalnum() for char in value):
        return escaped
    return rf'(?<!\w){escaped}(?!\w)'


@lru_cache(maxsize=4)
def _replacement_rules(dataset: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    path = dataset_path(ALIASES, dataset)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ()
    entities = payload.get('aliases_by_entity', payload) if isinstance(payload, dict) else {}
    groups: dict[str, set[str]] = {}
    if isinstance(entities, dict):
        for localized in entities.values():
            if not isinstance(localized, dict):
                continue
            for values in localized.values():
                if not isinstance(values, list):
                    continue
                aliases = [value.strip() for value in values
                           if isinstance(value, str) and value.strip()]
                for alias in aliases:
                    key = _compact(alias)
                    if key:
                        groups.setdefault(key, set()).add(alias)
    rules: list[tuple[re.Pattern[str], str]] = []
    for aliases in groups.values():
        if len(aliases) < 2:
            continue
        canonical = min(aliases, key=lambda value: (len(value), value.casefold()))
        for alias in aliases:
            if alias.casefold() == canonical.casefold():
                continue
            rules.append((re.compile(_pattern(alias), re.IGNORECASE), canonical))
    rules.sort(key=lambda item: len(item[0].pattern), reverse=True)
    return tuple(rules)


def normalize_terminology(value: str, language: str | None = None) -> str:
    """Canonicalize only equivalent checked-in alias spellings."""
    del language  # aliases are already partitioned by property, not by parser branch
    result = str(value)
    for matcher, replacement in _replacement_rules(str(_dataset_dir())):
        result = matcher.sub(replacement, result)
    return result


__all__ = ['normalize_terminology']
