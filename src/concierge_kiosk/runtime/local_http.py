"""Local-only HTTP transport for untrusted evidence sent to a local LLM.

The ordinary urllib opener follows redirects. A compromised loopback model
server could redirect the POST (including hotel evidence) off device; this
opener denies all redirects and rejects any non-loopback destination.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


# Connection-level circuit breaker. When the local model server is down, every
# connect attempt costs the full connect timeout (about 2 s per call on Windows,
# where refused loopback connects are retried), and one guest turn may make
# several model calls. After a connect failure, skip the model for a short
# cool-down so turns fall straight back to deterministic/extractive answers.
_slm_circuit_cooldown_seconds = 15.0
_circuit_lock = Lock()
_circuit_open_until = 0.0


def configure_slm_circuit_cooldown(seconds: float) -> None:
    global _slm_circuit_cooldown_seconds
    value = float(seconds)
    if not 1 <= value <= 300:
        raise ValueError('Invalid SLM circuit cooldown')
    with _circuit_lock:
        _slm_circuit_cooldown_seconds = value


def slm_circuit_closed() -> bool:
    return time.monotonic() >= _circuit_open_until


def _trip_circuit() -> None:
    global _circuit_open_until
    with _circuit_lock:
        _circuit_open_until = time.monotonic() + _slm_circuit_cooldown_seconds


def _reset_circuit() -> None:
    global _circuit_open_until
    with _circuit_lock:
        _circuit_open_until = 0.0


@dataclass
class _TurnSLMBudget:
    seconds: float
    deadline: float | None = None


_SLM_TURN_BUDGET: ContextVar[_TurnSLMBudget | None] = ContextVar(
    'slm_turn_budget', default=None)


@contextmanager
def slm_turn_budget(seconds: float):
    """Bound all local-SLM HTTP work in one guest turn by one deadline.

    The clock starts lazily on the first SLM request so deterministic routing
    and retrieval do not consume model time. Nested/non-turn callers retain
    their ordinary per-request timeout when no budget is installed.
    """
    budget = _TurnSLMBudget(max(0.0, float(seconds)))
    token: Token = _SLM_TURN_BUDGET.set(budget)
    try:
        yield
    finally:
        _SLM_TURN_BUDGET.reset(token)


def _active_deadline() -> float | None:
    budget = _SLM_TURN_BUDGET.get()
    if budget is None:
        return None
    if budget.deadline is None:
        budget.deadline = time.monotonic() + budget.seconds
    return budget.deadline


def slm_turn_expired() -> bool:
    budget = _SLM_TURN_BUDGET.get()
    return bool(budget is not None and budget.deadline is not None
                and time.monotonic() >= budget.deadline)


def slm_remaining_timeout(requested: float) -> float:
    """Return a request timeout capped by the current turn's SLM deadline."""
    requested = max(0.0, float(requested))
    deadline = _active_deadline()
    if deadline is None:
        return requested
    budget = _SLM_TURN_BUDGET.get()
    # Clamp to the configured budget too: float rounding in
    # ``deadline - monotonic()`` can otherwise exceed it by a few ulps.
    ceiling = budget.seconds if budget is not None else requested
    return max(0.0, min(requested, ceiling, deadline - time.monotonic()))


def local_chat_open(request: Request, *, timeout: float):
    url = urlsplit(request.full_url)
    if (url.scheme != 'http' or url.hostname not in LOOPBACK_HOSTS
            or url.username or url.password or url.query or url.fragment
            or url.path != '/api/chat'):
        raise ValueError('Local SLM destination must be the loopback /api/chat endpoint')
    if not slm_circuit_closed():
        raise ConnectionError('Local SLM unreachable; circuit open')
    bounded_timeout = slm_remaining_timeout(timeout)
    if bounded_timeout <= 0:
        raise TimeoutError('SLM turn deadline exceeded')
    try:
        response = _OPENER.open(request, timeout=bounded_timeout)
    except HTTPError:
        raise  # the server answered; it is reachable
    except (URLError, ConnectionError) as exc:
        # Trip only on "nobody is listening". Timeouts are excluded: they are
        # usually a busy model or this turn's own budget clipping the request.
        reason = exc.reason if isinstance(exc, URLError) else exc
        if isinstance(reason, ConnectionError) and not isinstance(reason, TimeoutError):
            _trip_circuit()
        raise
    _reset_circuit()
    return response


def local_embedding_open(request: Request, *, timeout: float):
    """Open Ollama's local embedding endpoint without allowing redirects."""
    url = urlsplit(request.full_url)
    if (url.scheme != 'http' or url.hostname not in LOOPBACK_HOSTS
            or url.username or url.password or url.query or url.fragment
            or url.path != '/api/embed'):
        raise ValueError('Local embedding destination must be the loopback /api/embed endpoint')
    if not slm_circuit_closed():
        raise ConnectionError('Local embedding service unreachable; circuit open')
    bounded_timeout = slm_remaining_timeout(timeout)
    if bounded_timeout <= 0:
        raise TimeoutError('Embedding turn deadline exceeded')
    try:
        response = _OPENER.open(request, timeout=bounded_timeout)
    except HTTPError:
        raise
    except (URLError, ConnectionError) as exc:
        reason = exc.reason if isinstance(exc, URLError) else exc
        if isinstance(reason, ConnectionError) and not isinstance(reason, TimeoutError):
            _trip_circuit()
        raise
    _reset_circuit()
    return response
