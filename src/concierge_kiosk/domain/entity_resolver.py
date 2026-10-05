"""Property-agnostic alias matching for entities and approved activity records.

Aliases are data.  This module owns only normalization and conservative phrase
matching mechanics; it never embeds a property, venue, service, or activity name.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any


def normalize_alias_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _latin_fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value)
                   if not unicodedata.combining(char))


def alias_present(query: str, alias: str) -> bool:
    """Match an alias without letting short ASCII words bleed into larger words."""
    query_text = normalize_alias_text(query)
    alias_text = normalize_alias_text(alias)
    if not alias_text:
        return False
    candidates = (query_text,)
    if any(unicodedata.name(char, "").startswith("LATIN") for char in alias_text):
        candidates += (_latin_fold(query_text),)
    if all(ord(char) < 128 for char in alias_text):
        pattern = r"(?<![a-z0-9_])" + re.escape(alias_text) + r"(?![a-z0-9_])"
        return any(re.search(pattern, candidate) for candidate in candidates)
    return any(alias_text in candidate or _latin_fold(alias_text) in candidate for candidate in candidates)


def record_alias_matches(query: str, records: Mapping[str, Mapping[str, Any]], *,
                         category: str | None = None, category_field: str = "topic",
                         aliases_field: str = "aliases") -> tuple[str, ...]:
    """Return record IDs whose data-owned aliases are explicitly mentioned."""
    matches: list[str] = []
    for record_id, record in records.items():
        if category is not None and record.get(category_field) != category:
            continue
        aliases = record.get(aliases_field) or ()
        if isinstance(aliases, (list, tuple)) and any(
                isinstance(alias, str) and alias_present(query, alias) for alias in aliases):
            matches.append(record_id)
    return tuple(matches)


def property_entity_matches(query: str, language: str,
                            aliases_by_entity: Mapping[str, Any]) -> tuple[str, ...]:
    """Resolve property entities exclusively from ``aliases.json``-style data.

    The alias payload may map an entity either to a language->aliases object or
    directly to an alias list for legacy datasets. Ambiguity is preserved by
    returning every explicit match; callers must not silently pick one.
    """
    matches: list[tuple[int, str]] = []
    for entity_id, configured in aliases_by_entity.items():
        aliases = configured.get(language, ()) if isinstance(configured, Mapping) else configured
        if not isinstance(aliases, (list, tuple)):
            continue
        lengths = [len(normalize_alias_text(alias)) for alias in aliases
                   if isinstance(alias, str) and alias_present(query, alias)]
        if lengths:
            matches.append((max(lengths), entity_id))
    matches.sort(key=lambda item: (-item[0], item[1]))
    return tuple(entity_id for _length, entity_id in matches)


__all__ = ["alias_present", "normalize_alias_text", "property_entity_matches", "record_alias_matches"]
