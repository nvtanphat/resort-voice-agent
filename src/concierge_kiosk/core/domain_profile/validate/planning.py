"""Validation of the ``planning`` section of the agent domain profile."""
from __future__ import annotations

from typing import Any

from .common import compile_regex, validate_language_keys


def validate_planning(payload: dict[str, Any], languages: set[str]) -> None:
    planning = payload["planning"]
    validate_language_keys(planning["intent_cues"], languages, label="planning.intent_cues", require_all=True)
    categories = planning["categories"]
    if planning["minimum_categories"] > len(categories):
        raise ValueError("Planning minimum_categories exceeds configured categories")
    preference_specs = payload["preferences"]["fields"]
    for category, spec in categories.items():
        validate_language_keys(spec["match_terms"], languages, label=f"planning.categories.{category}.match_terms", require_all=True)
        validate_language_keys(spec["search_query"], languages, label=f"planning.categories.{category}.search_query", require_all=True)
        for index, expansion in enumerate(spec["preference_expansions"]):
            preference = expansion["preference"]
            if preference not in preference_specs:
                raise ValueError(f"Planning category {category} references unknown preference {preference}")
            validate_language_keys(expansion["terms"], languages,
                                    label=f"planning.categories.{category}.preference_expansions.{index}.terms",
                                    require_all=True)
            if expansion["operator"] == "enum_in":
                values = expansion.get("values")
                if not values:
                    raise ValueError(f"Planning enum expansion {category}.{preference} requires values")
                configured = set(preference_specs[preference].get("values", ()))
                if not set(values) <= configured:
                    raise ValueError(f"Planning enum expansion {category}.{preference} references unknown value")
            elif expansion["operator"] == "positive_integer":
                if preference_specs[preference]["type"] != "integer" or "values" in expansion:
                    raise ValueError(f"Planning positive-integer expansion {category}.{preference} has invalid preference type")

    constraints = planning["constraints"]
    preference_fields = set(preference_specs)
    unknown_preferences = set(constraints["session_preference_fields"]) - preference_fields
    if unknown_preferences:
        raise ValueError("Planning references unknown preference field(s): " + ", ".join(sorted(unknown_preferences)))
    terms = constraints["constraint_terms"]
    for key in ("minimal_travel", "quiet", "navigation_goal_cues"):
        validate_language_keys(terms[key], languages,
                                label=f"planning.constraints.constraint_terms.{key}",
                                require_all=True)
        if any(not isinstance(item, str) or not item.strip()
               for values in terms[key].values() for item in values):
            raise ValueError(f"planning.constraints.constraint_terms.{key} contains an invalid term")
    for key in ("requested_days_patterns", "requested_guests_patterns", "daily_limit_patterns"):
        validate_language_keys(constraints[key], languages, label=f"planning.constraints.{key}", require_all=True)
        for language, pattern in constraints[key].items():
            compile_regex(pattern, label=f"planning.constraints.{key}.{language}")
    compile_regex(constraints["arrival_date_pattern"], label="planning.constraints.arrival_date_pattern")

    preferred = constraints["preferred_window"]
    validate_language_keys(preferred["range_patterns"], languages, label="planning.constraints.preferred_window.range_patterns")
    validate_language_keys(preferred["marker_rules"], languages, label="planning.constraints.preferred_window.marker_rules")
    validate_language_keys(preferred["dayparts"], languages, label="planning.constraints.preferred_window.dayparts", require_all=True)
    for language, pattern in preferred["range_patterns"].items():
        compile_regex(pattern, label=f"planning.constraints.preferred_window.range_patterns.{language}")
    for language, dayparts in preferred["dayparts"].items():
        for cue, window in dayparts.items():
            if not window[0] < window[1]:
                raise ValueError(f"Invalid planning daypart window {language}.{cue}")

    budget = constraints["budget"]
    validate_language_keys(budget["prefix_patterns"], languages, label="planning.constraints.budget.prefix_patterns", require_all=True)
    validate_language_keys(budget["magnitude_units"], languages, label="planning.constraints.budget.magnitude_units")
    for language, pattern in budget["prefix_patterns"].items():
        compile_regex(pattern, label=f"planning.constraints.budget.prefix_patterns.{language}")
    compile_regex(budget["multi_value_separator_pattern"], label="planning.constraints.budget.multi_value_separator_pattern")
    validate_language_keys(constraints["activity_priority_cues"], languages, label="planning.constraints.activity_priority_cues", require_all=True)
