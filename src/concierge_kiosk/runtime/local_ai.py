"""Offline-only local generation readiness; required by production.

A configured model name is not a loaded model. Strict profiles require the
operator-pinned EXACT Ollama model digest and a SHA-pinned independently loaded
NLI classifier. This check is run at app composition (not on guest requests).
Neither successful loading nor NLI labels establish factual accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from enum import Enum
from threading import Lock
import http.client
import json
import re
from urllib.parse import urlsplit

from ..core.model_manifest import check_model_manifest
from ..agent.understanding.nli import local_model_fingerprint, _load_local_nli
from .local_http import LOOPBACK_HOSTS

_HEX = re.compile(r'(?:sha256:)?([0-9a-f]{64})\Z')


class ModelState(str, Enum):
    NOT_LOADED = 'MODEL_NOT_LOADED'
    LOADING = 'MODEL_LOADING'
    READY = 'MODEL_READY'
    UNAVAILABLE = 'MODEL_UNAVAILABLE'


_loads: set[tuple[str, str]] = set()
_load_lock = Lock()


def _ollama_models(base_url: str, endpoint: str, timeout: float) -> list[dict] | None:
    url = urlsplit(base_url)
    if (url.scheme != 'http' or url.hostname not in LOOPBACK_HOSTS
            or url.path not in {'', '/'} or url.username or url.password or url.query or url.fragment):
        return None
    connection = None
    try:
        connection = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
        connection.request('GET', endpoint)
        response = connection.getresponse()
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            return None
        data = json.loads(raw)
        models = data.get('models') if isinstance(data, dict) else None
        return models if isinstance(models, list) and all(isinstance(m, dict) for m in models) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        if connection is not None:
            connection.close()


def model_residency(base_url: str, model: str, expected_digest: str, *, timeout: float = 1.5) -> dict:
    """Installed identity and actual residency; GET only, never loads a model.

    LOADING denotes this process's registered preload, not a guess from tags.
    External in-progress loads cannot be identified from an empty /api/ps.
    """
    pin = expected_digest.removeprefix('sha256:')
    installed = _ollama_models(base_url, '/api/tags', timeout)
    resident = _ollama_models(base_url, '/api/ps', timeout)
    result = {'state': ModelState.UNAVAILABLE.value, 'installed': installed, 'resident': resident}
    if installed is None or resident is None or not _HEX.fullmatch(pin):
        return result
    matches = [m for m in installed if m.get('name') == model]
    def matches_pin(item):
        digest = item.get('digest')
        return isinstance(digest, str) and digest.removeprefix('sha256:') == pin

    if len(matches) != 1 or not matches_pin(matches[0]):
        return result
    with _load_lock:
        loading = (base_url.rstrip('/'), model) in _loads
    loaded = [m for m in resident if m.get('name') == model]
    if loaded and (len(loaded) != 1 or not matches_pin(loaded[0])):
        return result
    result['state'] = (ModelState.LOADING if loading else
                       ModelState.READY if loaded else ModelState.NOT_LOADED).value
    return result


@contextmanager
def _model_loading(base_url: str, model: str):
    key = (base_url.rstrip('/'), model)
    with _load_lock:
        if key in _loads:
            raise RuntimeError('Model preload already active')
        _loads.add(key)
    try:
        yield
    finally:
        with _load_lock:
            _loads.discard(key)


def preload_local_slm(base_url: str, model: str, *, timeout: float = 30.0,
                      num_gpu: int = 0, should_cancel=None) -> dict:
    """One finite empty-message Ollama load. Caller owns admission and accounting."""
    from urllib.request import Request
    from ..core.settings import SLM_NUM_CTX
    from .local_http import local_chat_open
    if not 0 < timeout <= 90:
        raise ValueError('Invalid preload deadline')
    if should_cancel and should_cancel():
        raise InterruptedError('Model preload cancelled before HTTP')
    body = json.dumps({'model': model, 'stream': False, 'keep_alive': '5m',
        'messages': [], 'options': {'num_predict': 1, 'num_ctx': SLM_NUM_CTX,
                                    'num_gpu': num_gpu}}).encode('utf-8')
    request = Request(base_url.rstrip('/') + '/api/chat', data=body,
                      headers={'Content-Type': 'application/json'}, method='POST')
    with _model_loading(base_url, model), local_chat_open(request, timeout=timeout) as response:
        raw = response.read(65537)
        if should_cancel and should_cancel():
            raise InterruptedError('Model preload cancelled after HTTP')
        if int(response.status) != 200 or len(raw) > 65536:
            raise ValueError('Invalid preload response')
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('done') is not True or result.get('error'):
            raise ValueError('Model preload did not finish')
        return result


@dataclass(frozen=True)
class LocalAIReadiness:
    generator_installed: bool = False
    generator_digest_pinned: bool = False
    nli_manifest_valid: bool = False
    nli_loaded: bool = False

    @property
    def strict_ready(self) -> bool:
        return all((self.generator_installed, self.generator_digest_pinned,
                    self.nli_manifest_valid, self.nli_loaded))


def exact_ollama_digest(base_url: str, model: str, *, timeout: float = 1.5) -> str | None:
    """Query loopback only, no redirect, no download and bounded JSON payload."""
    if not isinstance(model, str) or not model or len(model) > 128:
        return None
    url = urlsplit(base_url)
    if (url.scheme != 'http' or url.hostname not in LOOPBACK_HOSTS
            or url.path not in {'', '/'} or url.username or url.password or url.query or url.fragment):
        return None
    connection = None
    try:
        connection = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
        connection.request('GET', '/api/tags', headers={'Accept': 'application/json'})
        response = connection.getresponse()
        length = response.getheader('Content-Length')
        if response.status != 200 or (length and int(length) > 65536):
            return None
        raw = response.read(65537)
        if len(raw) > 65536:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('models'), list):
            return None
        matches = [item for item in data['models'] if isinstance(item, dict) and item.get('name') == model]
        if len(matches) != 1:
            return None
        digest = matches[0].get('digest')
        match = _HEX.fullmatch(digest) if isinstance(digest, str) else None
        return match.group(1) if match else None
    except (OSError, ValueError, TypeError, OverflowError, http.client.HTTPException):
        return None
    finally:
        if connection is not None:
            connection.close()


def inspect_local_ai(settings, *, check_runtime: bool = True) -> LocalAIReadiness:
    """No remote fallback. Runtime checks are explicit and opt-in."""
    if not check_runtime:
        return LocalAIReadiness()
    installed_digest = exact_ollama_digest(settings.llm_base_url, settings.llm_model, timeout=settings.slm_probe_timeout_seconds)
    pin = settings.llm_model_digest.removeprefix('sha256:')
    nli_pin = bool(settings.nli_model_path and settings.nli_manifest_path and
                   check_model_manifest(settings.nli_model_path, settings.nli_manifest_path))
    loaded = False
    if nli_pin:
        fingerprint = local_model_fingerprint(settings.nli_model_path)
        if fingerprint is not None:
            try:
                _load_local_nli(settings.nli_model_path, fingerprint)
                loaded = True
            except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
                loaded = False
    return LocalAIReadiness(
        generator_installed=installed_digest is not None,
        generator_digest_pinned=bool(installed_digest and pin and installed_digest == pin),
        nli_manifest_valid=nli_pin, nli_loaded=loaded)


def warm_local_slm(base_url: str, model: str, *, timeout: float = 90.0,
                   num_gpu: int = -1, admission=None) -> bool:
    """Startup-only empty-message preload; no dummy guest generation."""
    if admission is not None and not admission.try_enter_slm():
        return False
    try:
        return preload_local_slm(base_url, model, timeout=timeout, num_gpu=num_gpu).get('done') is True
    finally:
        # This runs in the warm-up worker, even if its asyncio waiter is
        # cancelled. Native/socket work retains its CPU permit until it ends.
        if admission is not None:
            admission.leave_slm()
