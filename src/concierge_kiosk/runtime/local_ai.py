"""Offline-only local generation readiness; required by production.

A configured model name is not a loaded model. Strict profiles require the
operator-pinned EXACT Ollama model digest and a SHA-pinned independently loaded
NLI classifier. This check is run at app composition (not on guest requests).
Neither successful loading nor NLI labels establish factual accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import re
from urllib.parse import urlsplit

from ..agent.models.model_manifest import check_model_manifest
from ..agent.understanding.nli import local_model_fingerprint, _load_local_nli
from .local_http import LOOPBACK_HOSTS

_HEX = re.compile(r'(?:sha256:)?([0-9a-f]{64})\Z')


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


def warm_local_slm(base_url: str, model: str, *, timeout: float = 90.0) -> bool:
    """Load the local model into memory with a 1-token request (startup only)."""
    from urllib.request import Request
    from .local_http import local_chat_open
    body = json.dumps({
        'model': model, 'stream': False, 'keep_alive': '30m',
        'messages': [{'role': 'user', 'content': 'ok'}],
        'options': {'num_predict': 1},
    }).encode('utf-8')
    request = Request(base_url.rstrip('/') + '/api/chat', data=body,
                      headers={'Content-Type': 'application/json'}, method='POST')
    with local_chat_open(request, timeout=timeout) as response:
        response.read(65_536)
        return 200 <= int(getattr(response, 'status', 200)) < 300
