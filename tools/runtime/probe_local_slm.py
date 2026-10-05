"""Read-only check that the separately provisioned loopback Ollama model is listed.

This is a configuration/availability probe, not an inference quality benchmark.
It does not download weights, send hotel data, or permit remote model endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def model_status(base_url: str, model: str, *, opener=urlopen) -> dict:
    parsed = urlsplit(base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('Only an HTTP loopback Ollama endpoint is allowed')
    if not model or any(char in model for char in '\r\n\x00') or len(model) > 128:
        raise ValueError('Provide a local model name')
    req = Request(base_url.rstrip('/') + '/api/tags', headers={'Accept': 'application/json'})
    with opener(req, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError('Ollama model listing unavailable')
        payload = response.read(1_000_001)
    if len(payload) > 1_000_000:
        raise RuntimeError('Ollama model listing too large')
    data = json.loads(payload)
    models = data.get('models') if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise RuntimeError('Unexpected Ollama model listing')
    names = {entry.get('name') for entry in models if isinstance(entry, dict)
             and isinstance(entry.get('name'), str)}
    available = model in names or (':' not in model and model + ':latest' in names)
    return {'configured_model': model, 'model_present': available,
            'note': 'Model listing only; no inference, latency, multilingual or quality measurement.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=os.getenv('CONCIERGE_LLM_BASE_URL', 'http://127.0.0.1:11434'))
    parser.add_argument('--model', default=os.getenv('CONCIERGE_LLM_MODEL', ''))
    args = parser.parse_args()
    try:
        result = model_status(args.base_url, args.model)
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({'model_present': False, 'error': type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['model_present'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
