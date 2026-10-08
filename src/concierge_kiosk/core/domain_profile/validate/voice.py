"""Validation of the ``voice`` section of the agent domain profile."""
from __future__ import annotations

from typing import Any

from .common import compile_regex, validate_language_keys


def validate_voice(payload: dict[str, Any], languages: set[str]) -> None:
    voice = payload["voice"]
    hotwords = voice["stt_hotwords"]
    if (not isinstance(hotwords.get("max_terms"), int)
            or isinstance(hotwords["max_terms"], bool)
            or not 0 <= hotwords["max_terms"] <= 64):
        raise ValueError("voice.stt_hotwords.max_terms must be an integer in range")
    allowed_sources = {"map_labels", "service_names", "aliases"}
    if (not isinstance(hotwords.get("sources"), list)
            or not set(hotwords["sources"]).issubset(allowed_sources)):
        raise ValueError("voice.stt_hotwords.sources contains an unknown source")
    validate_language_keys(hotwords["extra"], languages, label="voice.stt_hotwords.extra", require_all=False)
    for language, values in hotwords["extra"].items():
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"voice.stt_hotwords.extra.{language} contains an invalid term")
    for key in ("continuation_cues", "clause_delimiters", "sentence_endings", "normalization"):
        validate_language_keys(voice[key], languages, label=f"voice.{key}", require_all=False)
        if any(not isinstance(values, list) or any(not isinstance(value, str) or not value.strip()
                                                   for value in values)
               for values in voice[key].values()):
            raise ValueError(f"voice.{key} contains an invalid language list")
    if not isinstance(voice.get("word_separator"), dict):
        raise ValueError("voice.word_separator must be an object")
    validate_language_keys(voice["word_separator"], languages,
                            label="voice.word_separator", require_all=False)
    if any(not isinstance(value, str) or len(value) > 8
           for value in voice["word_separator"].values()):
        raise ValueError("voice.word_separator contains an invalid separator")
    speech_plan = voice.get("speech_plan")
    if not isinstance(speech_plan, dict):
        raise ValueError("voice.speech_plan must be an object")
    limits = {"max_chars": (1, 2000), "first_chunk_max_chars": (1, 1000),
              "clause_split_min_chars": (1, 2000)}
    for key, (minimum, maximum) in limits.items():
        value = speech_plan.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"voice.speech_plan.{key} is invalid")
    for key in ("filler_terms", "self_correction_markers"):
        validate_language_keys(voice[key], languages, label=f"voice.{key}", require_all=False)
        if any(not isinstance(values, list) or any(not isinstance(value, str) or not value.strip()
                                                   for value in values)
               for values in voice[key].values()):
            raise ValueError(f"voice.{key} contains an invalid language list")
    units = voice["quantity_units"]
    validate_language_keys(units, languages, label="voice.quantity_units", require_all=False)
    if any(not isinstance(value, str) or not value.strip() for value in units.values()):
        raise ValueError("voice.quantity_units contains an invalid unit")
