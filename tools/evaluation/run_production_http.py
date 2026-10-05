"""Run a small release-backed HTTP smoke benchmark for Production evaluation.

This runner uses the real pinned Furama release/profile and a temporary copy of
SQLite.  It does not call Ollama for the selected deterministic cases and does
not mutate the checked-in database.  Results are environment measurements, not
Furama operational KPIs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
import sys
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from concierge_kiosk.core.dataset_layout import dataset_path

SUITE = dataset_path('evaluation/end_to_end/scenarios/production.jsonl')
CASE_KEYS = ('amenity_delivery', 'staff_review', 'knowledge_abstain', 'emergency')
LANGS = ('vi', 'en', 'ko', 'zh')


def _load_cases() -> list[dict]:
    rows = [json.loads(line) for line in SUITE.read_text(encoding='utf-8').splitlines() if line.strip()]
    selected=[]
    for lang in LANGS:
        for key in CASE_KEYS:
            if key == 'amenity_delivery':
                row = next((item for item in rows if item['language'] == lang
                            and item.get('service_code') == key
                            and item['expected_route'] == 'service'), None)
            elif key == 'staff_review':
                # Vietnamese does not currently have a late-checkout row in
                # the pinned scenario release. Select any staff-gated service
                # instead of making the evaluator depend on one business verb.
                row = next((item for item in rows if item['language'] == lang
                            and item['expected_route'] == 'service'
                            and item.get('approval_path') == 'staff'), None)
            elif key == 'knowledge_abstain':
                row = next((item for item in rows if item['language'] == lang
                            and item['expected_route'] == key), None)
            else:
                row = next((item for item in rows if item['language'] == lang
                            and item['expected_route'] == key), None)
            if row is None:
                raise KeyError(f'Missing production HTTP smoke case for {lang}/{key}')
            selected.append({**row, '_probe_key': key})
    return selected


def _configure(tmp_db: Path) -> None:
    os.environ.update({
        'PYTHON_DOTENV_DISABLED': '1', 'CONCIERGE_ENV': 'test',
        'CONCIERGE_RUNTIME_PROFILE': 'test', 'CONCIERGE_DB_PATH': str(tmp_db),
        'CONCIERGE_ORCHESTRATOR': 'direct',
    })
    for line in (ROOT / 'config' / 'local-runtime.env.example').read_text(encoding='utf-8').splitlines():
        if '=' not in line or line.startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key.startswith(('CONCIERGE_PROPERTY_PROFILE_', 'CONCIERGE_MAP_RELEASE_', 'CONCIERGE_PLANNING_RELEASE_')):
            if key.endswith('_PATH'):
                value = str((ROOT / value).resolve())
            os.environ[key] = value


def _observed_pass(case: dict, payload: dict, status: int) -> tuple[bool, str]:
    if status != 200:
        return False, f'http_{status}'
    expected = case['expected_route']
    if expected == 'emergency':
        ok = payload.get('tool_route') == 'emergency' and payload.get('requires_staff_review') is False
        return ok, 'emergency_route'
    if case.get('_probe_key') == 'amenity_delivery':
        aa = payload.get('autonomous_action') or {}
        slots = (payload.get('agent_action') or {}).get('collected_slots') or {}
        ok = (payload.get('tool_route') == 'service' and payload.get('requires_staff_review') is False
              and aa.get('status') == 'approved' and slots.get('room_number') == '305'
              and int(slots.get('quantity', 0)) == 2)
        return ok, 'autonomous_dispatch_with_slots'
    if case.get('_probe_key') == 'staff_review':
        action = payload.get('agent_action') or {}
        slots = action.get('collected_slots') or {}
        ok = (payload.get('tool_route') == 'service' and payload.get('requires_staff_review') is True
              and action.get('status') == 'confirmation_required'
              and action.get('business_writes') == 0)
        return ok, 'staff_gate_preserved'
    if case.get('_probe_key') == 'knowledge_abstain':
        ok = (payload.get('tool_route') == 'knowledge' and payload.get('evidence_status') == 'UNSUPPORTED'
              and not payload.get('sources') and not payload.get('citations'))
        return ok, 'unsupported_fact_abstained'
    return False, 'unknown_oracle'


def run(output: Path | None = None) -> dict:
    tmp_dir = Path(tempfile.mkdtemp(prefix='furama-production-http-'))
    tmp_db = tmp_dir / 'concierge.sqlite3'
    shutil.copy2(ROOT / 'data' / 'concierge.sqlite3', tmp_db)
    _configure(tmp_db)
    import sys
    sys.path.insert(0, str(ROOT / 'src'))
    from fastapi.testclient import TestClient
    from concierge_kiosk.main import create_app

    results=[]
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        for idx, case in enumerate(_load_cases(), 1):
            session = client.post('/api/session')
            if session.status_code != 200:
                raise RuntimeError(f'Could not create session: {session.status_code} {session.text[:200]}')
            csrf = session.json()['csrf_token']
            started=time.perf_counter()
            response=client.post('/api/ask', headers={'X-CSRF-Token': csrf}, json={
                'query': case['utterance'], 'language': case['language'],
                'turn_nonce': f'production-http-{idx:03d}',
            })
            latency_ms=(time.perf_counter()-started)*1000
            try:
                payload=response.json()
            except Exception:
                payload={'raw_text': response.text[:500]}
            passed, oracle = _observed_pass(case, payload, response.status_code)
            results.append({
                'scenario_id': case['scenario_id'], 'probe_key': case['_probe_key'], 'language': case['language'],
                'utterance': case['utterance'], 'oracle': oracle, 'passed': passed,
                'http_status': response.status_code, 'latency_ms': round(latency_ms, 2),
                'tool_route': payload.get('tool_route'), 'evidence_status': payload.get('evidence_status'),
                'requires_staff_review': payload.get('requires_staff_review'),
                'service_mode': (payload.get('agent_action') or {}).get('service_mode'),
                'action_status': (payload.get('agent_action') or {}).get('status'),
                'business_writes': (payload.get('agent_action') or {}).get('business_writes'),
                'collected_slots': (payload.get('agent_action') or {}).get('collected_slots'),
            })
            # Session creation is intentionally rate-limited in production. This
            # benchmark uses independent synthetic sessions, so clear only the
            # temp DB limiter between cases.
            with sqlite3.connect(tmp_db) as con:
                con.execute('DELETE FROM rate_limits')
                con.commit()
    lats=sorted(r['latency_ms'] for r in results)
    def pct(p: float) -> float:
        if not lats: return 0.0
        i=(len(lats)-1)*p; lo=int(i); hi=min(len(lats)-1, lo+1); frac=i-lo
        return round(lats[lo]*(1-frac)+lats[hi]*frac, 2)
    summary={
        'classification': 'local_release_http_measurement',
        'environment_guard': 'Local TestClient + test profile + temp SQLite; not a measured Furama production KPI.',
        'cases': len(results), 'passed': sum(r['passed'] for r in results),
        'pass_rate': round(sum(r['passed'] for r in results)/len(results),4),
        'latency_ms': {'p50': pct(.5), 'p95': pct(.95), 'max': round(max(lats),2)},
        'by_language': {lang: {
            'cases': sum(r['language']==lang for r in results),
            'passed': sum(r['language']==lang and r['passed'] for r in results),
        } for lang in LANGS},
        'results': results,
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return summary


if __name__ == '__main__':
    import sys
    # PowerShell on some Windows installations exposes cp1252 stdout. The
    # pinned multilingual scenarios must still be reportable without a
    # UnicodeEncodeError.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args=parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, sort_keys=True))
