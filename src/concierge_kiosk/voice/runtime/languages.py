"""Guest language to Whisper language-code mapping (profile-driven)."""
from __future__ import annotations

from concierge_kiosk.core.domain_profile import supported_languages, voice_policy


LANGUAGE_WHISPER = {language: language for language in supported_languages()}
