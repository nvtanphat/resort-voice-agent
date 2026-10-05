"""Fail-closed, end-to-end site acceptance check for an operator-provisioned appliance.

This does not generate acceptance evidence. It combines human-reviewed on-device
acceptance with actual HTTPS ingress, readiness, and public/staff isolation.
Run from the configured production container (or an equivalent trusted node).
Never send bearer tokens, guest data, audio or secrets to this tool.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from concierge_kiosk.core.settings import Settings, load_settings
from tools.operations.edge_acceptance import evaluate

# Small HTTP response contract, intentionally only non-sensitive status/config.
Probe = Callable[[str], tuple[int, dict]]


def https_probe(url: str) -> tuple[int, dict]:
    """Use the default system trust store; never allow insecure TLS fallback."""
    request = urllib.request.Request(url, headers={"User-Agent": "concierge-site-acceptance/1"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310  # scheme/host validated by caller
            status = response.status
            body = response.read(4096)
    except urllib.error.HTTPError as exc:
        status, body = exc.code, b""
    data: dict = {}
    if body:
        try:
            decoded = json.loads(body)
            if isinstance(decoded, dict):
                data = decoded
        except (ValueError, UnicodeDecodeError):
            pass
    return status, data


def evaluate_site_acceptance(cfg: Settings, acceptance: dict, probe: Probe = https_probe) -> dict:
    checks: dict[str, str] = {}
    blockers: list[str] = []

    def require(name: str, condition: bool, reason: str) -> None:
        checks[name] = 'PASS' if condition else 'BLOCKED'
        if not condition:
            blockers.append(reason)

    try:
        require('production_mode', cfg.environment == 'production', 'CONCIERGE_ENV must be production')
        cfg.validate()
        require('configuration', cfg.environment == 'production', 'Production-only configuration required')
    except (ValueError, OSError) as exc:
        checks['configuration'] = 'BLOCKED'
        blockers.append('Production configuration invalid: ' + str(exc))

    try:
        record = evaluate(acceptance)
        require('site_acceptance', bool(record['ready_for_site_acceptance']),
                'Actual hardware, voice, RAG, security and hotel-policy acceptance is incomplete')
    except (ValueError, TypeError, KeyError) as exc:
        checks['site_acceptance'] = 'BLOCKED'
        blockers.append('Site acceptance invalid: ' + str(exc))

    # No HTTP calls to unknown, insecure or development origins.
    if checks.get('configuration') != 'PASS' or cfg.environment != 'production':
        checks['https_end_to_end'] = 'BLOCKED'
        return {'status': 'BLOCKED', 'checks': checks, 'blockers': blockers}

    targets = (
        ('health', cfg.public_origin + '/healthz', 200, lambda b: b.get('status') == 'ok'),
        ('readiness', cfg.public_origin + '/readyz', 200, lambda b: b.get('status') == 'ready'
         and set(b.get('knowledge_languages', [])) == {'vi', 'en', 'zh', 'ko'}),
        ('public_configuration', cfg.public_origin + '/api/config', 200,
         lambda b: b.get('property_name') == cfg.property_name and b.get('voice_available') is True
         and set(b.get('tts_languages', [])) == {'vi', 'en', 'zh', 'ko'}
         and b.get('orchestrator') == 'langgraph'),
        ('guest_denies_staff_page', cfg.public_origin + '/ops', (403, 404), None),
        ('guest_denies_staff_html', cfg.public_origin + '/static/ops.html', (403, 404), None),
        ('guest_denies_staff_js', cfg.public_origin + '/static/ops.js', (403, 404), None),
        ('guest_denies_staff_api', cfg.public_origin + '/staff/requests', (403, 404), None),
        ('guest_denies_internal_api', cfg.public_origin + '/internal/agent/session', (403, 404), None),
        ('guest_denies_schema', cfg.public_origin + '/openapi.json', (403, 404), None),
        ('staff_page', cfg.staff_origin + '/ops', 200, None),
        ('staff_requires_bearer', cfg.staff_origin + '/staff/requests', (401, 403), None),
        ('staff_does_not_expose_guest', cfg.staff_origin + '/api/config', (403, 404), None),
    )
    for name, url, expected, predicate in targets:
        try:
            status, body = probe(url)
            acceptable = ((status in expected) if isinstance(expected, tuple) else status == expected)
            require(name, acceptable and (predicate(body) if predicate else True),
                    name + ': actual HTTPS response/contract did not match')
        except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
            checks[name] = 'BLOCKED'
            blockers.append(name + ': HTTPS/TLS/network probe unavailable (' + type(exc).__name__ + ')')

    return {'status': 'PASS' if not blockers else 'BLOCKED', 'checks': checks, 'blockers': blockers}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acceptance', required=True, help='Reviewed JSON file, or - to read stdin')
    parser.add_argument('--report', type=Path, help='Write a non-sensitive status report outside repo')
    args = parser.parse_args()
    try:
        data = sys.stdin.read() if args.acceptance == '-' else Path(args.acceptance).read_text(encoding='utf-8')
        report = evaluate_site_acceptance(load_settings(), json.loads(data))
    except (OSError, ValueError, TypeError) as exc:
        report = {'status': 'BLOCKED', 'checks': {}, 'blockers': ['Unable to evaluate: ' + str(exc)]}
    result = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(result + '\n', encoding='utf-8')
    print(result)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
