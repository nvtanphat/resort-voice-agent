"""Optional PMS / hotel-operations integration boundaries.

No adapter is treated as proof by default. Guest identity input is used only for
verification and is never returned for persistence by this module. External
service dispatch is explicit and idempotency is owned by the caller/request id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from concierge_kiosk.runtime.local_http import LOOPBACK_HOSTS


@dataclass(frozen=True)
class GuestVerificationResult:
    state: str  # verified | staff_required | rejected
    provider: str
    reference: str = ""
    reason: str = ""


@dataclass(frozen=True)
class DispatchResult:
    state: str  # accepted | queued | pending_sync | failed | not_configured
    provider: str
    external_reference: str = ""
    eta_minutes: int | None = None
    error_code: str = ""


class GuestVerifier(Protocol):
    def verify(self, *, property_id: str, room_number: str, last_name: str = "",
               room_qr_token: str = "") -> GuestVerificationResult: ...


class ServiceDispatcher(Protocol):
    def dispatch(self, *, request_id: str, property_id: str, kind: str,
                 details: str, payload: dict) -> DispatchResult: ...


class RoomInventoryValidator(Protocol):
    def __call__(self, *, property_id: str, room_number: str) -> bool | None: ...


class NoopGuestVerifier:
    def verify(self, **_: object) -> GuestVerificationResult:
        return GuestVerificationResult("staff_required", "none", reason="verification_provider_not_configured")


class NoopServiceDispatcher:
    def dispatch(self, **_: object) -> DispatchResult:
        return DispatchResult("not_configured", "none", error_code="dispatch_provider_not_configured")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would forward the bearer token and guest data."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Scheme/host are validated by ``_safe_endpoint`` at construction time and the
# opener never follows redirects, so the final destination is the configured one.
_INTEGRATION_OPENER = urllib.request.build_opener(_NoRedirect())


def _post_json(request: urllib.request.Request, *, timeout: float, limit: int) -> dict:
    with _INTEGRATION_OPENER.open(request, timeout=timeout) as response:  # nosec B310
        if not 200 <= int(getattr(response, "status", 200)) < 300:
            raise ValueError("Integration returned a non-success status")
        data = json.loads(response.read(limit).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Integration response must be a JSON object")
    return data


def _safe_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.username or parsed.fragment or parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Invalid hotel integration endpoint")
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("Hotel integration HTTP is allowed only on loopback; use HTTPS otherwise")
    return url


class SignedRoomQrVerifier:
    """Validate hotel-issued, short-lived room QR tokens locally.

    Token format: base64url(JSON).hex_hmac_sha256. Claims: property_id,
    room_number, exp. The kiosk validates only; it never issues guest tokens.
    """
    def __init__(self, secret: str):
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("Room QR secret must be at least 32 bytes")
        self._secret = secret.encode("utf-8")

    def verify(self, *, property_id: str, room_number: str, last_name: str = "",
               room_qr_token: str = "") -> GuestVerificationResult:
        if not room_qr_token or "." not in room_qr_token:
            return GuestVerificationResult("staff_required", "signed_room_qr", reason="room_qr_missing")
        encoded, supplied = room_qr_token.rsplit(".", 1)
        expected = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            return GuestVerificationResult("rejected", "signed_room_qr", reason="room_qr_signature_invalid")
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            claims = json.loads(raw.decode("utf-8"))
            exp = int(claims["exp"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            return GuestVerificationResult("rejected", "signed_room_qr", reason="room_qr_malformed")
        if exp < int(time.time()):
            return GuestVerificationResult("rejected", "signed_room_qr", reason="room_qr_expired")
        if claims.get("property_id") != property_id or str(claims.get("room_number", "")) != room_number:
            return GuestVerificationResult("rejected", "signed_room_qr", reason="room_qr_scope_mismatch")
        ref = hashlib.sha256(room_qr_token.encode("utf-8")).hexdigest()[:16]
        return GuestVerificationResult("verified", "signed_room_qr", reference=ref)


class HttpGuestVerifier:
    """Generic PMS verification adapter. Response must include {\"verified\": bool}."""
    def __init__(self, endpoint: str, token: str, timeout_seconds: float = 2.0):
        self.endpoint = _safe_endpoint(endpoint)
        if len(token) < 16:
            raise ValueError("PMS verification token is too short")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def verify(self, *, property_id: str, room_number: str, last_name: str = "",
               room_qr_token: str = "") -> GuestVerificationResult:
        if not room_number or not last_name:
            return GuestVerificationResult("staff_required", "pms_http", reason="room_and_last_name_required")
        body = json.dumps({"property_id": property_id, "room_number": room_number,
                           "last_name": last_name}, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        try:
            payload = _post_json(request, timeout=self.timeout_seconds, limit=16_384)
        except Exception:
            return GuestVerificationResult("staff_required", "pms_http", reason="pms_unavailable")
        if payload.get("verified") is True:
            return GuestVerificationResult("verified", "pms_http",
                                           reference=str(payload.get("reference", ""))[:80])
        return GuestVerificationResult("rejected", "pms_http", reason="guest_identity_not_verified")


class HttpRoomInventoryValidator:
    """Ask the property inventory/PMS whether a room exists and is active.

    This is deliberately separate from guest identity verification: a valid
    room number is necessary for routing a service, while a guest credential is
    a different authorization decision.  ``None`` means the external source
    was unavailable and is handled fail-closed by the workflow boundary.
    """
    def __init__(self, endpoint: str, token: str, timeout_seconds: float = 2.0):
        self.endpoint = _safe_endpoint(endpoint)
        if len(token) < 16:
            raise ValueError("Room inventory token is too short")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def __call__(self, *, property_id: str, room_number: str) -> bool | None:
        body = json.dumps({"property_id": property_id, "room_number": room_number},
                          separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        try:
            payload = _post_json(request, timeout=self.timeout_seconds, limit=16_384)
        except Exception:
            return None
        if payload.get("valid") is True or payload.get("verified") is True:
            return True
        if payload.get("valid") is False or payload.get("verified") is False:
            return False
        return None


class CompositeGuestVerifier:
    def __init__(self, *verifiers: GuestVerifier):
        self.verifiers = tuple(verifiers)

    def verify(self, **kwargs) -> GuestVerificationResult:
        if kwargs.get("room_qr_token"):
            for verifier in self.verifiers:
                if isinstance(verifier, SignedRoomQrVerifier):
                    result = verifier.verify(**kwargs)
                    if result.state != "staff_required":
                        return result
        if kwargs.get("last_name"):
            for verifier in self.verifiers:
                if isinstance(verifier, HttpGuestVerifier):
                    result = verifier.verify(**kwargs)
                    if result.state != "staff_required":
                        return result
        return GuestVerificationResult("staff_required", "composite", reason="no_verifiable_credential")


class HttpServiceDispatcher:
    """Generic Opera/HotSOS/POS bridge adapter owned by deployment configuration."""
    def __init__(self, endpoint: str, token: str, timeout_seconds: float = 3.0):
        self.endpoint = _safe_endpoint(endpoint)
        if len(token) < 16:
            raise ValueError("Service dispatch token is too short")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def dispatch(self, *, request_id: str, property_id: str, kind: str,
                 details: str, payload: dict) -> DispatchResult:
        body = json.dumps({"request_id": request_id, "property_id": property_id,
                           "kind": kind, "details": details, "payload": payload},
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}",
                     "Idempotency-Key": request_id})
        try:
            data = _post_json(request, timeout=self.timeout_seconds, limit=32_768)
        except Exception:
            # The local ticket remains authoritative.  A network failure is
            # explicitly pending_sync, never a successful external booking.
            return DispatchResult("pending_sync", "hotel_ops_http", error_code="external_dispatch_unavailable")
        state = str(data.get("state", "accepted"))
        if state not in {"accepted", "queued"}:
            return DispatchResult("failed", "hotel_ops_http", error_code="external_dispatch_rejected")
        eta = data.get("eta_minutes")
        eta = (int(eta) if isinstance(eta, (int, float)) and not isinstance(eta, bool)
               and math.isfinite(eta) and 1 <= eta <= 720 else None)
        return DispatchResult(state, "hotel_ops_http",
                              external_reference=str(data.get("reference", ""))[:120], eta_minutes=eta)


def integrations_from_env() -> tuple[GuestVerifier, ServiceDispatcher]:
    verifiers: list[GuestVerifier] = []
    qr_secret = os.getenv("CONCIERGE_ROOM_QR_SECRET", "")
    if qr_secret:
        verifiers.append(SignedRoomQrVerifier(qr_secret))
    pms_url = os.getenv("CONCIERGE_GUEST_VERIFY_URL", "").strip()
    pms_token = os.getenv("CONCIERGE_GUEST_VERIFY_TOKEN", "")
    if pms_url:
        verifiers.append(HttpGuestVerifier(pms_url, pms_token))
    verifier: GuestVerifier = CompositeGuestVerifier(*verifiers) if verifiers else NoopGuestVerifier()

    dispatch_url = os.getenv("CONCIERGE_SERVICE_DISPATCH_URL", "").strip()
    dispatch_token = os.getenv("CONCIERGE_SERVICE_DISPATCH_TOKEN", "")
    dispatcher: ServiceDispatcher = (HttpServiceDispatcher(dispatch_url, dispatch_token)
                                     if dispatch_url else NoopServiceDispatcher())
    return verifier, dispatcher


def room_inventory_from_env() -> RoomInventoryValidator | None:
    """Build the optional room inventory boundary from deployment settings."""
    endpoint = os.getenv("CONCIERGE_ROOM_INVENTORY_URL", "").strip()
    token = os.getenv("CONCIERGE_ROOM_INVENTORY_TOKEN", "")
    return HttpRoomInventoryValidator(endpoint, token) if endpoint else None
