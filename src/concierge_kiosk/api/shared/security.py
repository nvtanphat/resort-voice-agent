"""HTTP ingress policy for a shared public/staff process.

The proxy gateway is a separate trust boundary: staff bearer alone never grants
staff access from the public kiosk. This module owns headers and origin/CIDR checks;
it must not make business or graph decisions.
"""
from __future__ import annotations
import ipaddress
import secrets
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from concierge_kiosk.core.settings import Settings


def install_http_security(app: FastAPI, cfg: Settings) -> None:
    @app.middleware("http")
    async def guard_origin(request: Request, call_next):
        # Public kiosk and staff/API share a process, NOT a trust boundary.
        # A dedicated staff reverse-proxy route strips incoming gateway headers
        # and injects the secret. Even a valid staff bearer is not enough on the
        # public kiosk ingress. /ops must not be served to kiosk guests.
        if cfg.environment == "production" and (
            request.url.path == "/ops" or request.url.path.startswith("/staff/")
            or request.url.path in {"/static/ops.html", "/static/ops.js"}
        ):
            gateway = request.headers.get("X-Concierge-Staff-Gateway", "")
            if not gateway or not secrets.compare_digest(gateway, cfg.staff_gateway_token):
                return JSONResponse({"detail": "Staff ingress not permitted"}, status_code=403)
        if cfg.allowed_client_cidrs and request.url.path.startswith(("/api/", "/staff/", "/internal/", "/ops")):
            try:
                peer = ipaddress.ip_address(request.client.host if request.client else "")
                networks = [ipaddress.ip_network(x.strip(), strict=False) for x in cfg.allowed_client_cidrs.split(",")]
                if not any(peer in network for network in networks):
                    return JSONResponse({"detail": "Device ingress not permitted"}, status_code=403)
            except ValueError:
                return JSONResponse({"detail": "Device ingress misconfigured"}, status_code=503)
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            # Fetch Metadata hardens missing-Origin cross-site form submissions.
            # Non-browser agents with no Sec-Fetch-Site remain token-authenticated.
            fetch_site = request.headers.get("Sec-Fetch-Site", "")
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                return JSONResponse({"detail": "Cross-site request denied"}, status_code=403)
            origin = request.headers.get("origin")
            expected_origin = (cfg.staff_origin if cfg.environment == "production" and
                               request.url.path.startswith("/staff/") else cfg.public_origin)
            if origin and origin != expected_origin:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith(("/api/", "/internal/", "/staff/")) or request.url.path == "/ops":
            response.headers["Cache-Control"] = "no-store"
        if cfg.public_origin.startswith("https://"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=(self)"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; media-src 'self' blob:; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        return response

    @app.middleware('http')
    async def assign_request_trace(request: Request, call_next):
        """Fresh server IDs for one HTTP operation, including rejected ingress.

        A public client must not select a trace ID or impersonate an earlier
        request. Raw audio, guest text, session cookies and credentials are not
        logged or embedded in the response headers.
        """
        request.state.request_id = secrets.token_hex(16)
        request.state.trace_id = secrets.token_hex(16)
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['X-Trace-ID'] = request.state.trace_id
        return response

