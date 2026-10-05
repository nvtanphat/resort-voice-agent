"""Shared types, constants and validation primitives for request workflows."""
from __future__ import annotations
import hashlib
import re
from typing import Any, Protocol
from concierge_kiosk.domain.service_registry import REQUEST_KINDS, LANGUAGES as REGISTRY_LANGUAGES
from concierge_kiosk.core.domain_profile import security_policy

KINDS = set(REQUEST_KINDS)
LANGUAGES = set(REGISTRY_LANGUAGES)
QUEUE_RANK = ("CASE WHEN status='pending_staff' THEN 0 "
              "WHEN status='approved' THEN 1 WHEN status='in_progress' THEN 2 "
              "WHEN status='paused' THEN 3 ELSE 4 END")
QUEUE_TIME = ("CASE WHEN status IN ('pending_staff','approved','in_progress','paused') THEN created_at "
              "ELSE -updated_at END")
SENSITIVE = re.compile('|'.join(f'(?:{pattern})' for pattern in security_policy().sensitive_patterns), re.IGNORECASE)

class StorePort(Protocol):
    def connection(self, write: bool = False) -> Any: ...

class InvalidTransition(ValueError):
    pass

def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
