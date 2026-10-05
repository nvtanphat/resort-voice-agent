"""Central guest-facing copy loaded from the shared locale JSON files."""
from __future__ import annotations

import json
from pathlib import Path

from concierge_kiosk.core.domain_profile import supported_languages

SUPPORTED_LANGUAGES = tuple(supported_languages())
_LOCALE_ROOT = Path(__file__).resolve().parents[3] / "locales"
_MESSAGES: dict[str, dict[str, str]] = {}


def _load_messages() -> None:
    for language in SUPPORTED_LANGUAGES:
        path = _LOCALE_ROOT / f"{language}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Locale {language} must be an object")
        for full_key, value in payload.items():
            if not full_key.startswith("backend.") or not isinstance(value, str):
                continue
            key = full_key.removeprefix("backend.")
            _MESSAGES.setdefault(key, {})[language] = value
    expected = set(SUPPORTED_LANGUAGES)
    if not _MESSAGES or any(set(variants) != expected for variants in _MESSAGES.values()):
        raise ValueError("Shared backend locales are incomplete")


_load_messages()


def text(key: str, language: str, **values: object) -> str:
    variants = _MESSAGES.get(key)
    if variants is None:
        raise KeyError(f"Unknown i18n key: {key}")
    if language not in variants:
        raise KeyError(f"Unsupported language {language!r} for {key}")
    return variants[language].format(**values)
