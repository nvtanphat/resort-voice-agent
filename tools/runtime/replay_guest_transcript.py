"""Bounded authenticated guest API replay. Explicit model budget, temporary SQLite, no benchmark.

Input JSON: {"scenarios": [{"id": "...", "language": "en", "turns": [{"query": "..."}]}]}.
Recorded output is append-protected; raw synthetic utterances stay in local artifacts only.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Timer
import time
from unittest.mock import patch

from tools.runtime.agent_stabilization_smoke import CallBoundary, _memory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transcript', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--expected-digest', required=True)
    parser.add_argument('--max-qwen-calls', type=int, default=2, choices=range(0, 4))
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise SystemExit('Refusing to overwrite an existing real replay')
    scenarios = json.loads(Path(args.transcript).read_text(encoding='utf-8'))['scenarios']
    if not 1 <= len(scenarios) <= 15 or sum(len(s['turns']) for s in scenarios) > 60:
        raise SystemExit('Replay must be finite: at most 15 scenarios / 60 turns')
    os.environ.update(CONCIERGE_ENV='test', CONCIERGE_RUNTIME_PROFILE='test', LANGFUSE_ENABLED='false')
    source = Path('data/concierge.sqlite3')
    source_hash = lambda: hashlib.sha256(source.read_bytes()).hexdigest()
    profile = json.loads(Path('config/runtime-profiles/development.json').read_text(encoding='utf-8'))
    base, model = (profile['models']['slm'][key] for key in ('base_url', 'primary_model'))
    expected_digest = args.expected_digest
    head = subprocess.run(['git','rev-parse','HEAD'], capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    record = {'head': head, 'mode':'REAL_API_REAL_MODEL_WHEN_AVAILABLE', 'turns':[], 'stop_reason':None,
              'source_sha256_before':source_hash(), 'max_qwen_calls':args.max_qwen_calls,
              'preload':{'status':'NOT_RUN'}, 'original_transcript':str(args.transcript)}
    def persist():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    from concierge_kiosk.runtime import local_ai, local_http
    boundary = CallBoundary(local_http._OPENER.open, model, persist, max_calls=args.max_qwen_calls,
                            allowed_phases={'PRELOAD', *(f'T{n}' for n in range(60))}, stop_on_failure=True)
    record['calls'], record['protocol_errors'] = boundary.calls, boundary.errors
    active_layers = []
    def timed(owner, name, label):
        original = getattr(owner, name)
        def wrapped(*a, **kw):
            begun = time.monotonic()
            try:
                return original(*a, **kw)
            finally:
                active_layers.append({'layer':label,'elapsed_ms':round((time.monotonic()-begun)*1000,3)})
        return patch.object(owner, name, wrapped)
    model_allowed = False
    with tempfile.TemporaryDirectory(prefix='guest-transcript-') as directory, ExitStack() as stack:
        os.environ['CONCIERGE_DB_PATH'] = str(Path(directory)/'bootstrap.sqlite3')
        stack.enter_context(patch.object(local_ai, 'warm_local_slm', lambda *a, **k:False))
        stack.enter_context(patch.object(local_http._OPENER, 'open', boundary.open))
        from concierge_kiosk.main import create_app
        from concierge_kiosk.core.settings import Settings
        from concierge_kiosk.application.conversation import engine, answers
        from concierge_kiosk.agent.understanding import commands
        from concierge_kiosk.agent.understanding.fast_router import FastRouter
        from concierge_kiosk.agent.understanding.grounded_service import GroundedServiceResolver
        from concierge_kiosk.agent.understanding.service_selector import ServiceSelector
        from concierge_kiosk.agent.runtime.runtime import AutonomousConciergeRuntime
        from concierge_kiosk.voice.runtime import adapters
        from fastapi.testclient import TestClient
        os.environ.pop('CONCIERGE_DB_PATH')
        db = Path(directory)/'replay.sqlite3'
        shutil.copyfile(source, db)
        cfg = Settings(db_path=db, environment='test', property_id='FURAMA_DANANG',
            property_name='Furama Resort Danang', property_timezone='Asia/Ho_Chi_Minh',
            property_profile_path='releases/property-profile.json',
            property_profile_sha256=Path('releases/property-profile.sha256').read_text().strip(),
            map_release_path='releases/map-release.json',
            map_release_sha256=Path('releases/map-release.sha256').read_text().strip(),
            llm_base_url=base, llm_model=model, llm_model_digest=expected_digest, slm_num_gpu=0,
            llm_fallback_model='', embedding_model_path='', embedding_manifest_path='',
            rerank_model_path='', rerank_manifest_path='', semantic_understanding_enabled=False,
            semantic_generation_enabled=False, agent_planner_enabled=False)
        if Path(cfg.db_path).resolve() != db.resolve():
            raise RuntimeError('Temporary SQLite isolation failed')
        record['config'] = {'intent_timeout_seconds':cfg.intent_parser_timeout_seconds,
                            'turn_timeout_seconds':cfg.slm_generation_timeout_seconds,
                            'embedding':'NOT_LOADED', 'similarity_fast_path':True,'evidence_fast_path':False,
                            'cloud':'disabled','device':'CPU', 'model':model,'digest':expected_digest}
        ready = local_ai.model_residency(base, model, expected_digest)
        record['preflight'], record['memory_before'] = ready, _memory()
        installed = next((m for m in ready.get('installed') or [] if m.get('name') == model), {})
        other = any(m.get('name') != model for m in ready.get('resident') or [])
        threshold = 1024**3 + (installed.get('size', 0) if ready['state'] == 'MODEL_NOT_LOADED' else 0)
        if args.max_qwen_calls and not other and ready['state'] in {'MODEL_READY','MODEL_NOT_LOADED'} and _memory()['available_bytes'] >= threshold:
            try:
                if ready['state'] == 'MODEL_NOT_LOADED':
                    boundary.phase = 'PRELOAD'
                    value = local_ai.preload_local_slm(base, model, timeout=30, num_gpu=0)
                    record['preload'] = {'status':'PASS','response':value}
                ready = local_ai.model_residency(base, model, expected_digest)
                record['readiness_after_preload'] = ready
                resident = (ready.get('resident') or [{}])[0]
                model_allowed = (ready['state'] == 'MODEL_READY' and len(ready['resident']) == 1
                                 and resident.get('size_vram') == 0 and resident.get('context_length') == 4096
                                 and _memory()['available_bytes'] >= 1024**3)
                if not model_allowed:
                    record['stop_reason'] = 'RESIDENCY_OR_RAM_BLOCKED'
            except Exception as exc:
                record['preload'] = {'status':'FAIL','error_type':type(exc).__name__,'error':str(exc)}
                record['stop_reason'] = 'PRELOAD_FAILED'
        else:
            record['stop_reason'] = 'RESOURCE_OR_MODEL_UNAVAILABLE' if args.max_qwen_calls else 'ZERO_MODEL_BUDGET'
        record['memory_after_preload'] = _memory()
        original_command = engine._TurnRuntimeSupport.command_for_session
        original_chat = commands._chat
        raw = []
        def guarded_command(*a, **kw):
            if not model_allowed or len(boundary.calls) >= args.max_qwen_calls:
                engine._COMMAND_OUTCOME.set('unavailable')
                return None
            return original_command(*a, **kw)
        def capture_chat(*a, **kw):
            result = original_chat(*a, **kw)
            raw.append(result)
            return result
        stack.enter_context(patch.object(engine._TurnRuntimeSupport,'command_for_session',guarded_command))
        stack.enter_context(patch.object(commands,'_chat',capture_chat))
        stack.enter_context(patch.object(adapters,'warm_voice_models',lambda *_:[]))
        for owner, name, label in ((FastRouter,'route','FastRouter'),(GroundedServiceResolver,'resolve','GroundedService'),
                                  (ServiceSelector,'understand','ServiceSelector'),(engine._TurnRuntimeSupport,'live_context_anchors','Memory'),
                                  (engine._TurnRuntimeSupport,'_apply_commands','SemanticGateAndProjection'),
                                  (engine,'model_commands','QwenNLUAndValidation'),(AutonomousConciergeRuntime,'run','LangGraph'),
                                  (answers,'retrieve','Retrieval')):
            if hasattr(owner, name):
                stack.enter_context(timed(owner,name,label))
        app = create_app(cfg)
        def counts():
            with app.state.store.connection() as con:
                return {name:con.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                        for name in ('service_requests','proposals','emergency_alerts')}
        for scenario in scenarios:
            with TestClient(app) as client:
                session = client.post('/api/session').json()
                headers = {'X-CSRF-Token':session['csrf_token']}
                for turn in scenario['turns']:
                    memory = _memory()
                    if memory['available_bytes'] < 1024**3:
                        record['stop_reason'] = 'RAM_STOP';persist();break
                    boundary.phase = f'T{len(record["turns"])}'
                    before, call_count = counts(), len(boundary.calls)
                    active_layers.clear();raw.clear()
                    begun = time.monotonic()
                    response = client.post('/api/ask', headers=headers, json={
                        'query':turn['query'],'language':scenario['language']})
                    body = response.json()
                    # Never persist cookies, authentication headers or raw session keys.
                    def sanitized(value):
                        if isinstance(value, dict):
                            return {k:'<redacted>' if 'token' in k or k in {'session','session_id','status_url'}
                                    else sanitized(v) for k,v in value.items()}
                        if isinstance(value,list):return [sanitized(v) for v in value]
                        return value
                    record['turns'].append({'scenario':scenario['id'],'input':turn['query'],'language':scenario['language'],
                        'http_status':response.status_code,'api_ms':round((time.monotonic()-begun)*1000,3),
                        'layers_inclusive':list(active_layers),'response':sanitized(body),
                        'raw_model_response':list(raw),'qwen_calls':len(boundary.calls)-call_count,
                        'db_delta':{k:counts()[k]-v for k,v in before.items()},'memory':memory})
                    if boundary.errors or body.get('failure_class') == 'NLU_TIMEOUT':
                        model_allowed = False
                        record['stop_reason'] = 'MODEL_FAILURE_STOP_NO_RETRY'
                    persist()
                if record['stop_reason'] == 'RAM_STOP':break
        boundary.phase = 'CLOSED'
    record['source_sha256_after'] = source_hash()
    record['new_qwen_calls'] = len(boundary.calls)
    record['source_db_unchanged'] = record['source_sha256_before'] == record['source_sha256_after']
    persist()
    print(json.dumps({'turns':len(record['turns']),'calls':len(boundary.calls),'stop_reason':record['stop_reason'],
                      'source_db_unchanged':record['source_db_unchanged']}))
    return 0


if __name__ == '__main__':
    # CLI-only finite wall limit; no watchdog or model work at module import.
    watchdog = Timer(180, lambda: os._exit(2))
    watchdog.daemon = True
    watchdog.start()
    try:
        raise SystemExit(main())
    finally:
        watchdog.cancel()
