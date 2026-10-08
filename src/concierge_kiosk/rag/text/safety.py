"""Conservative screening of untrusted knowledge text."""
from __future__ import annotations

import re

from concierge_kiosk.core.domain_profile import security_policy


def _prompt_injection_pattern() -> re.Pattern[str]:
    configured = security_policy().prompt_injection_patterns
    patterns = [pattern for values in configured.values() for pattern in values]
    if not patterns:
        # The signed profile validator requires at least one pattern. This
        # fallback keeps import-time failure explicit if a legacy profile is
        # loaded by an isolated caller.
        raise RuntimeError('Prompt-injection patterns are not configured')
    return re.compile('|'.join(f'(?:{pattern})' for pattern in patterns), re.IGNORECASE)


PROMPT_INJECTION_PATTERN = _prompt_injection_pattern()


def unsafe_knowledge_text(text: str) -> bool:
    """Conservative screening of instruction-style payloads, including legacy data.

    This is defense in depth, not a guarantee against arbitrary prompt injection.
    """
    return bool(PROMPT_INJECTION_PATTERN.search(text))


__all__ = ["PROMPT_INJECTION_PATTERN", "unsafe_knowledge_text"]
