"""WP12 isolated diagnostic acceptance. At most four POSTs; never retry."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import Event, Thread, Timer
import time
import unicodedata
from unittest.mock import patch

from tools.runtime.agent_stabilization_smoke import CallBoundary, _memory, prompt_diagnostics


def plain(value):
    return ''.join(c for c in unicodedata.normalize('NFD', str(value)).lower()
                   if not unicodedata.combining(c))


def water_matches(body, commands):
    payload = body.get('service_payload') or {}
    action = body.get('suggested_action') or {}
    return (any(c.get('type') == 'StartGoal' and c.get('goal') == 'amenity_delivery'
                for c in commands) and action.get('service') == 'amenity_delivery'
            and payload.get('quantity') == 3 and payload.get('room_number') == '502'
            and plain(payload.get('unit')) == 'chai'
            and plain(payload.get('requested_item')) == 'nuoc suoi')


def allow_reference_call(phase, payload, calls):
    """One actual reference selection may consume the remaining fourth call."""
    return (phase == 'LIVE-03'
            and sum(c['purpose'] == phase for c in calls) == 1
            and 'anchor_index' in payload.get('format', {}).get('properties', {}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-digest', required=True)
    parser.add_argument('--output', default='reports/wp12-real-nlu.json')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise SystemExit('Refusing another run under the recorded WP12 budget')
    os.environ['CONCIERGE_ENV'] = 'test'
    os.environ['CONCIERGE_RUNTIME_PROFILE'] = 'test'
    from concierge_kiosk.runtime import local_ai, local_http
    from concierge_kiosk.core.settings import Settings
    from concierge_kiosk.agent.understanding import commands
    from concierge_kiosk.application.conversation import engine
    from fastapi.testclient import TestClient

    source = Path('data/concierge.sqlite3')
    digest = lambda: hashlib.sha256(source.read_bytes()).hexdigest()
    record = {'historical_calls_closed': 5, 'wp12_budget': 4,
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'source_sha256_before': digest(), 'preload': {'result': 'NOT_RUN'},
              'cases': {k: {'result': 'NOT_RUN'} for k in ('LIVE-01', 'LIVE-02', 'LIVE-03')}}
    def persist():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    base, model = 'http://127.0.0.1:11434', 'qwen2.5:3b'
    boundary = CallBoundary(local_http._OPENER.open, model, persist, max_calls=4,
                           allowed_phases={'PRELOAD', 'LIVE-01', 'LIVE-02', 'LIVE-03'},
                           allow_repeat=allow_reference_call)
    record['calls'], record['protocol_errors'] = boundary.calls, boundary.errors
    log = Path(os.environ['LOCALAPPDATA']) / 'Ollama/server.log'
    offset = log.stat().st_size if log.exists() else None
    stop = Event()
    samples = []
    import ctypes
    def cpu_times():
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise RuntimeError('CPU sampling unavailable')
        return idle.value, kernel.value + user.value
    def sample():
        previous = cpu_times()
        while not stop.is_set():
            current = cpu_times()
            total = current[1] - previous[1]
            cpu = 100 * (1 - (current[0] - previous[0]) / total) if total else None
            samples.append({'elapsed': time.monotonic(), 'system_cpu_percent': cpu, **_memory()})
            previous = current
            stop.wait(0.5)
    monitor = Thread(target=sample, daemon=True)
    monitor.start()
    def finish():
        stop.set(); monitor.join(2)
        record['memory_samples'] = samples
        record['minimum_available_ram_bytes'] = min((s['available_bytes'] for s in samples), default=None)
        record['source_sha256_after'] = digest()
        record['new_qwen_http_calls'] = len(boundary.calls)
        record['finished_utc'] = datetime.now(timezone.utc).isoformat()
        if offset is not None:
            with log.open('rb') as f:
                f.seek(offset)
                record['ollama_log_since_start'] = f.read().decode('utf-8', errors='replace')
        persist()
        print(json.dumps({'calls': len(boundary.calls), 'decision': record.get('decision'),
                          'cases': {k: v['result'] for k, v in record['cases'].items()}}, ensure_ascii=True))
    def watchdog_expired():
        record['decision'] = 'BLOCKED'
        record['watchdog'] = 'Expired; process stopped without retries'
        finish()
        os._exit(2)
    watchdog = Timer(150, watchdog_expired)
    watchdog.daemon = True
    watchdog.start()
    try:
        with tempfile.TemporaryDirectory(prefix='wp12-agent-') as directory:
            db = Path(directory) / 'isolated.sqlite3'
            shutil.copyfile(source, db)
            os.environ['CONCIERGE_DB_PATH'] = str(Path(directory) / 'composition.sqlite3')
            with patch.object(local_http._OPENER, 'open', boundary.open), \
                    patch.object(local_ai, 'warm_local_slm', lambda *a, **kw: False):
                from concierge_kiosk.main import create_app
                from concierge_kiosk.voice.runtime import adapters
                # Env has higher priority than explicit Settings kwargs.
                os.environ.pop('CONCIERGE_DB_PATH')
                cfg = Settings(db_path=db, environment='test', property_id='FURAMA_DANANG',
                    property_name='Furama Resort Danang', property_timezone='Asia/Ho_Chi_Minh',
                    property_profile_path='releases/property-profile.json',
                    property_profile_sha256=Path('releases/property-profile.sha256').read_text().strip(),
                    map_release_path='releases/map-release.json',
                    map_release_sha256=Path('releases/map-release.sha256').read_text().strip(),
                    llm_base_url=base, llm_model=model, llm_model_digest=args.expected_digest,
                    llm_fallback_model='', slm_num_gpu=0, embedding_model_path='',
                    embedding_manifest_path='', rerank_model_path='', rerank_manifest_path='',
                    semantic_understanding_enabled=False, semantic_generation_enabled=False,
                    agent_planner_enabled=False, intent_parser_timeout_seconds=10,
                    slm_generation_timeout_seconds=30)
                if Path(cfg.db_path).resolve() != db.resolve():
                    raise RuntimeError('Database isolation failed')
                record['config'] = {'diagnostic_socket_seconds': 20,
                    'validated_settings_intent_seconds': cfg.intent_parser_timeout_seconds,
                    'diagnostic_turn_seconds': cfg.slm_generation_timeout_seconds,
                    'production_command_cap_seconds': 10, 'historical_production_timeout_seconds': 3,
                    'preload_timeout_seconds': 30, 'keep_alive': '5m', 'num_gpu': 0,
                    'planner': False, 'semantic_generation': False, 'embedding': 'disabled',
                    'startup_warm': 'disabled', 'model': model, 'digest': args.expected_digest}
                if cfg.intent_parser_timeout_seconds != 10 or cfg.slm_generation_timeout_seconds != 30:
                    raise RuntimeError('Diagnostic override not isolated')
                ready = local_ai.model_residency(base, model, args.expected_digest)
                record['preflight'] = ready
                record['memory_before'] = memory = _memory()
                installed = next((m for m in ready.get('installed') or [] if m.get('name') == model), {})
                if (ready['state'] not in {'MODEL_READY', 'MODEL_NOT_LOADED'}
                        or any(m.get('name') != model for m in ready.get('resident') or [])
                        or memory['available_bytes'] < installed.get('size', 0) + 1024**3):
                    raise RuntimeError('Readiness/model/RAM preflight blocked')
                if ready['state'] == 'MODEL_NOT_LOADED':
                    boundary.phase = 'PRELOAD'
                    record['preload'] = {'result': 'PASS', 'response': local_ai.preload_local_slm(
                        base, model, timeout=30, num_gpu=0)}
                ready = local_ai.model_residency(base, model, args.expected_digest)
                record['readiness_after_preload'] = ready
                if ready['state'] != 'MODEL_READY' or len(ready['resident']) != 1:
                    raise RuntimeError('Preload readiness failed')
                resident = ready['resident'][0]
                if resident.get('size_vram') != 0 or resident.get('context_length') != 4096:
                    raise RuntimeError('CPU/context mismatch')
                if _memory()['available_bytes'] < 512 * 1024**2:
                    raise RuntimeError('Insufficient resident RAM headroom')
                raw, outcomes, captures = [], [], []
                original_chat, original_model = commands._chat, commands.model_commands
                original_understand = engine._TurnRuntimeSupport.understand_turn
                def traced_chat(b, payload, timeout, cancel):
                    # Actual adapter, real payload/output. Only this harness bypasses
                    # model_commands' ten-second socket cap; no production edit.
                    record.setdefault('adapter_timeouts', []).append({'caller_cap': timeout, 'diagnostic': 20})
                    answer = original_chat(b, payload, 20, cancel)
                    raw.append(answer)
                    return answer
                def traced_model(**kw):
                    callback = kw.get('on_outcome')
                    def outcome(value):
                        outcomes.append(value)
                        if callback: callback(value)
                    kw['on_outcome'] = outcome
                    return original_model(**kw)
                def traced_understand(self, *a, **kw):
                    before = [asdict(x) for x in self.live_context_anchors(a[2], a[1])]
                    result = original_understand(self, *a, **kw)
                    captures.append({'route': result[0].branch,
                        'commands': [c.public() for c in result[3] or ()], 'anchors_before': before})
                    return result
                with patch.object(adapters, 'warm_voice_models', lambda *_: []), \
                        patch.object(commands, '_chat', traced_chat), \
                        patch.object(engine, 'model_commands', traced_model), \
                        patch.object(engine._TurnRuntimeSupport, 'understand_turn', traced_understand):
                    app = create_app(cfg)
                    with TestClient(app) as client:
                        session_b = None
                        inputs = ('cho toi 3 chai nuoc suoi phong 502',
                                  'Hồ bơi mở cửa lúc mấy giờ?', 'Từ sảnh đi đến đó như thế nào?')
                        for i, query in enumerate(inputs, 1):
                            case = f'LIVE-{i:02d}'
                            if len(boundary.calls) >= 4:
                                record['cases'][case] = {'result': 'NOT_RUN', 'reason': 'BLOCKED_BY_BUDGET'}
                                break
                            if i != 3:
                                session = client.post('/api/session').json()
                                if i == 2: session_b = session
                            else: session = session_b
                            raw.clear(); outcomes.clear(); captures.clear()
                            with app.state.store.connection() as con:
                                before = con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0]
                            boundary.phase = case
                            started = time.monotonic()
                            response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']},
                                json={'query': query, 'language': 'vi', 'turn_nonce': f'wp12-live-{i}-12345678'})
                            elapsed = time.monotonic() - started
                            body = response.json()
                            with app.state.store.connection() as con:
                                writes = con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] - before
                            validated = captures[-1]['commands'] if captures else []
                            anchors = [asdict(x) for x in app.state.conversation_engine.turn_support.live_context_anchors(
                                session['session_id'], 'vi')]
                            try: parsed = json.loads(raw[-1]) if raw and raw[-1] else None
                            except ValueError: parsed = None
                            if i == 1: passed = water_matches(body, validated)
                            elif i == 2:
                                passed = (any(c.get('type') == 'AskInfo' for c in validated)
                                    and bool(body.get('citations')) and bool(anchors)
                                    and body.get('grounding') not in {None, 'no_evidence'})
                            else:
                                passed = (any(c.get('type') == 'Navigate' for c in validated)
                                    and body.get('map_guidance', {}).get('status') == 'verified'
                                    and bool(captures[-1]['anchors_before']))
                            passed = passed and writes == 0 and response.status_code == 200 and not boundary.errors
                            failure = ('NLU_TIMEOUT' if any(e['failure_class'] == 'TIMEOUT' for e in boundary.errors)
                                else 'PROTOCOL_BLOCKED' if boundary.errors else
                                'INVALID_MODEL_OUTPUT' if parsed is None else
                                'COMMAND_VALIDATION_FAILURE' if not validated else
                                'SLOT_FIDELITY_FAILURE' if i == 1 and not passed else
                                'TOOL_EXECUTION_FAILURE' if not passed else 'SUCCESS')
                            record['cases'][case] = {'result': 'REAL_MODEL_PASS' if passed else
                                'TIMEOUT' if failure == 'NLU_TIMEOUT' else 'FAIL', 'failure_class': failure,
                                'session': 'A-anonymized' if i == 1 else 'B-anonymized', 'input': query,
                                'raw_model_response': list(raw), 'parsed_commands': parsed,
                                'validation_outcomes': list(outcomes), 'understanding': list(captures),
                                'anchors_after': anchors, 'response': body, 'http_status': response.status_code,
                                'api_elapsed_seconds': elapsed, 'business_write_delta': writes}
                            persist()
                            if not passed:
                                record['decision'] = 'REAL_NLU_TIMEOUT' if failure == 'NLU_TIMEOUT' else 'REAL_NLU_FAIL_SEMANTIC'
                                break
                            record['decision'] = 'REAL_NLU_PASS_DIAGNOSTIC_ONLY'
                boundary.phase = 'CLOSED'
    except Exception as exc:
        record['exception'] = {'type': type(exc).__name__, 'message': str(exc)}
        record['decision'] = 'REAL_NLU_TIMEOUT' if isinstance(exc, TimeoutError) else 'BLOCKED'
    finally:
        watchdog.cancel()
        finish()
    return 0 if record.get('decision') == 'REAL_NLU_PASS_DIAGNOSTIC_ONLY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
