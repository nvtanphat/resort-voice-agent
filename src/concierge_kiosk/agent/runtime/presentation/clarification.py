"""Fixed, privacy-safe clarification prompts for agent interrupts."""
from __future__ import annotations

from concierge_kiosk.i18n import text as i18n_text


def clarification_text(language: str, field: str) -> str:
    try:
        return i18n_text(f'clarification.{field}', language)
    except KeyError:
        return i18n_text('clarification.preference', language)
