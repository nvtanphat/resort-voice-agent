"""Authentication/authorization dependency factory for the kiosk HTTP boundary.

Guest session+CSRF, scoped staff bearer and internal-agent bearer are separate
trust boundaries. Do not merge them or accept the public kiosk origin as staff.
"""
from __future__ import annotations
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Annotated, Callable
from fastapi import Cookie, Depends, Header, HTTPException
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.domain.service_requests import Workflows


@dataclass(frozen=True)
class AuthDependencies:
    guest_session: Callable
    staff_read: Callable
    staff_write: Callable
    require_agent: Callable


def build_auth_dependencies(cfg: Settings, workflows: Workflows) -> AuthDependencies:
    def guest_session(token: Annotated[str | None, Cookie(alias="ck_session")] = None,
                      csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None) -> str:
        return workflows.session_for(token or "", csrf or "")

    def staff_identity(authorization: Annotated[str | None, Header()] = None) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Staff authorization required")
        token = authorization.removeprefix("Bearer ")
        if cfg.staff_credentials_json:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            for account in json.loads(cfg.staff_credentials_json):
                if secrets.compare_digest(account["token_hash"], token_hash):
                    return {"name": account["name"], "scopes": set(account["scopes"])}
        elif cfg.environment != "production" and cfg.staff_token and secrets.compare_digest(token, cfg.staff_token):
            return {"name": "development-staff", "scopes": {"requests:read", "requests:write"}}
        raise HTTPException(status_code=401, detail="Staff authorization required")

    def staff_read(account: dict = Depends(staff_identity)) -> dict:
        if "requests:read" not in account["scopes"]:
            raise HTTPException(status_code=403, detail="Missing read permission")
        return account

    def staff_write(account: dict = Depends(staff_identity)) -> dict:
        if "requests:write" not in account["scopes"]:
            raise HTTPException(status_code=403, detail="Missing write permission")
        return account

    def require_agent(x_agent_token: Annotated[str | None, Header()] = None) -> None:
        if not cfg.agent_token or not x_agent_token or not secrets.compare_digest(x_agent_token, cfg.agent_token):
            raise HTTPException(status_code=401, detail="Agent authorization required")

    return AuthDependencies(guest_session=guest_session, staff_read=staff_read,
                            staff_write=staff_write, require_agent=require_agent)
