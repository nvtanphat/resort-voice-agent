"""Validation of the ``planning`` section of the agent domain profile."""
from __future__ import annotations

from typing import Any

from .common import compile_regex, validate_language_keys


def validate_planning(payload: dict[str, Any], languages: set[str]) -> None:
    planning = payload["planning"]

    constraints = planning["constraints"]
    preference_fields = set(payload['preferences']['fields'])
    unknown_preferences = set(constraints["session_preference_fields"]) - preference_fields
    if unknown_preferences:
        raise ValueError("Planning references unknown preference field(s): " + ", ".join(sorted(unknown_preferences)))
    fields = payload['preferences']['fields']
    for name, mapping in payload['preferences'].get('constraints', {}).items():
        if fields.get(name, {}).get('type') != 'enum':
            raise ValueError(f"Preference constraints name a field that is not an enum preference: {name}")
        undeclared = set(mapping) - set(fields[name]['values'])
        if undeclared:
            raise ValueError(f"Preference '{name}' maps undeclared value(s) to constraints: "
                             + ", ".join(sorted(undeclared)))
    for key in ("requested_days_patterns", "requested_guests_patterns", "daily_limit_patterns"):
        validate_language_keys(constraints[key], languages, label=f"planning.constraints.{key}", require_all=True)
        for language, pattern in constraints[key].items():
            compile_regex(pattern, label=f"planning.constraints.{key}.{language}")
    compile_regex(constraints["arrival_date_pattern"], label="planning.constraints.arrival_date_pattern")

