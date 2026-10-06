"""Signed, expiring bearer tokens for the guest status page."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time


class InvalidStatusToken(ValueError):
    """Raised when a public status bearer token is invalid or expired."""


_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    if not value or len(value) > 2048:
        raise InvalidStatusToken("Malformed status token")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise InvalidStatusToken("Malformed status token") from exc


class StatusTokenService:
    """Issue and verify tokens that carry no guest PII.

    The token payload contains only property scope, the random internal
    request id and an expiry.  It is still treated as a bearer secret and is
    never logged by the application.
    """

    version = 1

    def __init__(self, secret: str, *, ttl_seconds: int = 48 * 60 * 60):
        secret_bytes = str(secret or "").encode("utf-8")
        if len(secret_bytes) < 32:
            raise ValueError("Status token secret must be at least 32 bytes")
        if not 300 <= int(ttl_seconds) <= 7 * 24 * 60 * 60:
            raise ValueError("Invalid status token TTL")
        self._secret = secret_bytes
        self.ttl_seconds = int(ttl_seconds)

    def issue(self, *, property_id: str, request_id: str, now: int | None = None) -> tuple[str, int]:
        if not property_id or len(property_id) > 64 or ":" in property_id:
            raise ValueError("Invalid property id")
        if not _REQUEST_ID_RE.fullmatch(str(request_id or "")):
            raise ValueError("Invalid request id")
        issued_at = int(time.time()) if now is None else int(now)
        expires_at = issued_at + self.ttl_seconds
        payload = {
            "v": self.version,
            "p": property_id,
            "r": request_id,
            "e": expires_at,
            "j": secrets.token_hex(8),
        }
        encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = _b64(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}", expires_at

    def verify(self, token: str, *, property_id: str, now: int | None = None) -> dict:
        raw = str(token or "")
        if len(raw) > 4096 or raw.count(".") != 1:
            raise InvalidStatusToken("Malformed status token")
        encoded, supplied_signature = raw.split(".", 1)
        expected_signature = _b64(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise InvalidStatusToken("Invalid status token")
        try:
            payload = json.loads(_unb64(encoded).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidStatusToken("Malformed status token") from exc
        if (not isinstance(payload, dict) or payload.get("v") != self.version
                or payload.get("p") != property_id
                or not _REQUEST_ID_RE.fullmatch(str(payload.get("r") or ""))
                or not isinstance(payload.get("e"), int)
                or not isinstance(payload.get("j"), str)):
            raise InvalidStatusToken("Invalid status token claims")
        timestamp = int(time.time()) if now is None else int(now)
        if payload["e"] < timestamp:
            raise InvalidStatusToken("Status token expired")
        return {"property_id": payload["p"], "request_id": payload["r"], "expires_at": payload["e"]}
