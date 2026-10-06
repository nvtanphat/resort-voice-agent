"""Guest-safe request references.

The internal request id is a database capability and must not be the value a
guest copies into a message or scans from a QR code.  The public reference is
deliberately a short projection of the random request id; it contains no room,
name, transcript or service payload.  The request table remains the authority
for uniqueness and lookup.
"""
from __future__ import annotations

import re


PUBLIC_REFERENCE_RE = re.compile(r"^STAY-[0-9A-F]{12}$")


def public_reference(request_id: str) -> str:
    """Return a stable, non-semantic guest confirmation code."""
    value = str(request_id or "").strip().lower()
    if len(value) != 32 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("Invalid request id")
    return "STAY-" + value[:12].upper()
