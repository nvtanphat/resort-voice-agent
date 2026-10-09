"""Aggregate service-request workflow composed from focused mixins."""
from __future__ import annotations
from dataclasses import dataclass, field
import os
from typing import Callable
from .base import StorePort, KINDS, LANGUAGES
from .sessions import SessionWorkflowMixin
from .submissions import SubmissionWorkflowMixin
from .guest_queries import GuestRequestQueryMixin
from .staff_ops import StaffWorkflowMixin

@dataclass
class Workflows(SessionWorkflowMixin, SubmissionWorkflowMixin, GuestRequestQueryMixin, StaffWorkflowMixin):
    store: StorePort
    property_id: str
    proposal_ttl: int = 300
    emergency_detector: Callable[[str, str], bool] | None = None
    enabled_languages: frozenset[str] | None = None
    enabled_kinds: frozenset[str] | None = None
    guest_verifier: object | None = None
    service_dispatcher: object | None = None
    cfg: object | None = None
    emergency_escalation_seconds: int = 60
    default_kiosk_location: str = ''
    # Optional PMS/property-inventory boundary.  ``None`` means the inventory
    # is unavailable, never that an arbitrary room number is valid.
    room_validator: Callable[[str], bool | None] | None = None
    observability: object | None = field(default=None, repr=False, compare=False)

    def _is_emergency(self, details: str, language: str) -> bool:
        return bool(self.emergency_detector and self.emergency_detector(details, language))

    def configure_property_policy(self, *, languages: set[str] | frozenset[str],
                                  request_kinds: set[str] | frozenset[str]) -> None:
        """Bind the signed property profile to the authoritative write boundary.

        UI catalog filtering is not authorization. Every proposal is checked
        again here so a direct HTTP client cannot enable a service/language that
        the operator disabled for this property.
        """
        language_set = frozenset(languages)
        kind_set = frozenset(request_kinds)
        if not language_set or not language_set.issubset(LANGUAGES):
            raise ValueError("Invalid property language policy")
        if not kind_set.issubset(KINDS):
            raise ValueError("Invalid property service policy")
        self.enabled_languages = language_set
        self.enabled_kinds = kind_set

    def _enforce_property_policy(self, kind: str, language: str) -> None:
        if self.enabled_languages is not None and language not in self.enabled_languages:
            raise PermissionError("Language is not enabled for this property")
        if self.enabled_kinds is not None and kind not in self.enabled_kinds:
            raise PermissionError("Service is not enabled for this property")

    def _validate_room_inventory(self, room_number: str) -> None:
        room_number = str(room_number or '').strip()
        if not room_number:
            return
        environment = str(getattr(self.cfg, 'environment', '') or
                          os.getenv('CONCIERGE_ENV', 'production')).strip().lower()
        if self.room_validator is None:
            if environment == 'production':
                raise PermissionError('Room inventory verification is unavailable')
            return
        try:
            result = self.room_validator(room_number)
        except TypeError:
            # The test/property adapter boundary historically accepted a plain
            # room string.  Keep that small callable contract while allowing
            # the production HTTP adapter to expose named arguments.
            try:
                result = self.room_validator(room_number=room_number)  # type: ignore[call-arg]
            except Exception as exc:
                if environment == 'production':
                    raise PermissionError('Room inventory verification failed') from exc
                raise
        except Exception as exc:
            if environment == 'production':
                raise PermissionError('Room inventory verification failed') from exc
            raise
        if result is False or (result is None and environment == 'production'):
            raise PermissionError('Room could not be verified against the property inventory')
