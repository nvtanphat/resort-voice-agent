"""Two-call readiness/preload + one real guest API smoke; no import-time work."""
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
from threading import Lock
import time
import unicodedata
from urllib.parse import urlsplit


def prompt_diagnostics(payload: dict) -> dict:
    messages = payload.get('messages', [])
    # Empty preload / non-JSON prompts are valid Ollama requests, not NLU payloads.
    try:
        user = json.loads(messages[-1]['content']) if messages else {}
    except (KeyError, TypeError, ValueError):
        user = {}
    if not isinstance(user, dict):
        user = {}
    schema = payload.get('format', {})
    variants = schema.get('properties', {}).get('commands', {}).get('items', {}).get('anyOf', [])
    goals = {}
    for variant in variants:
        properties = variant.get('properties', {})
        if properties.get('type', {}).get('const') == 'StartGoal':
            goal_schema = properties['goal']
            codes = goal_schema.get('enum', [goal_schema.get('const')])
            offered_slots = properties['slots']['items']['properties']['name'].get('enum', [])
            contracts = {s['service_mode']: s['accepted_slots'] for s in user.get('available_services', [])}
            for code in codes:
                goals[code] = sorted(set(contracts.get(code, offered_slots)) & set(offered_slots))
    return {'http_payload_bytes':len(json.dumps(payload,ensure_ascii=False).encode()),
        'message_bytes':sum(len(m['content'].encode()) for m in messages),
        'schema_bytes':len(json.dumps(schema,ensure_ascii=False).encode()),
        'command_variants':len(variants),'service_goal_count':len(goals),
        'slot_definition_count':sum(map(len,goals.values())), 'goal_slots':goals,
        'candidate_order':[s['service_mode'] for s in user.get('available_services',[])],
        'few_shots':len(user.get('examples',[])), 'structured_format':'JSON_SCHEMA',
        'options':payload.get('options')}


class CallBoundary:
    """Count actual model POSTs, holding a permit until the response closes."""
    def __init__(self, open_request, model, persist=lambda: None, *,
                 max_calls=2, allowed_phases=None, allow_repeat=None, stop_on_failure=False):
        self.open_request,self.model,self.persist=open_request,model,persist
        self.calls,self.errors=[],[]
        self.phase='AUDIT'
        self.lock=Lock()
        self.max_calls = max_calls
        self.allowed_phases = frozenset(allowed_phases or {'PRELOAD', 'NLU'})
        self.allow_repeat = allow_repeat
        self.stop_on_failure = stop_on_failure

    def open(self, request, *, timeout):
        path=urlsplit(request.full_url).path
        payload=json.loads(request.data or b'{}')
        if ((self.stop_on_failure and self.errors) or path!='/api/chat' or payload.get('model')!=self.model
                or payload.get('options',{}).get('num_gpu')!=0
                or self.phase not in self.allowed_phases or len(self.calls)>=self.max_calls
                or (any(c['purpose']==self.phase for c in self.calls)
                    and not (self.allow_repeat and self.allow_repeat(self.phase, payload, self.calls)))):
            self.errors.append({'failure_class':'PROTOCOL_BLOCKED','path':path,'phase':self.phase})
            self.persist()
            raise ValueError('Unexpected model call denied at HTTP boundary')
        if not self.lock.acquire(blocking=False):
            self.errors.append({'failure_class':'CONCURRENT_CALL_BLOCKED'})
            self.persist()
            raise ValueError('Concurrent model call denied')
        call={'purpose':self.phase,'start_utc':datetime.now(timezone.utc).isoformat(),
              'timeout_seconds':timeout,'payload':payload,'events':[]}
        self.calls.append(call);self.persist()
        started=time.monotonic();owner=self
        def failed(exc):
            call['error']={'type':type(exc).__name__,'message':str(exc)}
            owner.errors.append({'failure_class':'TIMEOUT' if isinstance(exc,TimeoutError) else 'HTTP_ERROR',**call['error']})
        def finished():
            call['http_elapsed_seconds']=round(time.monotonic()-started,6)
            call['end_utc']=datetime.now(timezone.utc).isoformat()
            owner.lock.release();owner.persist()
        try:
            response=self.open_request(request,timeout=timeout)
            call['http_status']=response.status
            call['headers_seconds']=time.monotonic()-started
        except Exception as exc:
            call['http_status']=getattr(exc,'code',None)
            failed(exc);finished();raise

        class Response:
            def __getattr__(self,name): return getattr(response,name)
            def __enter__(self): return self
            def __exit__(self,kind,exc,traceback):
                try:
                    if exc: failed(exc)
                    response.close()
                finally: finished()
            def observe(self,raw):
                try: event=json.loads(raw)
                except ValueError: return
                if not isinstance(event, dict):
                    return
                call['events'].append(event)
                if event.get('message',{}).get('content') and 'first_content_seconds' not in call:
                    call['first_content_seconds']=time.monotonic()-started
                if isinstance(event, dict) and event.get('done'):
                    call['timings']={k:event[k] for k in ('total_duration','load_duration','prompt_eval_count',
                        'prompt_eval_duration','eval_count','eval_duration','done_reason') if k in event}
                    call['provider_ms'] = {k: event[source] / 1e6 for k, source in (
                        ('model_loading', 'load_duration'), ('prompt_prefill', 'prompt_eval_duration'),
                        ('generation', 'eval_duration'), ('total', 'total_duration'))
                        if type(event.get(source)) is int and event[source] >= 0}
            def read(self,size=-1):
                raw=response.read(size);self.observe(raw);return raw
            def __iter__(self):
                for raw in response:
                    self.observe(raw);yield raw
        return Response()


def _memory():
    if os.name!='nt': raise RuntimeError('Windows memory preflight required')
    import ctypes
    class Status(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong)
            for n in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
    state=Status();state.length=ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        raise RuntimeError('Cannot establish memory capacity')
    return {'available_bytes':state.available,'total_bytes':state.total,'memory_load_percent':state.load}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-digest',required=True)
    parser.add_argument('--output',default='reports/e2e/model-smoke.json')
    parser.add_argument('--preload-timeout',type=float,default=30)
    parser.add_argument('--prompt-only',action='store_true')
    args=parser.parse_args();output=Path(args.output)
    if output.exists() and not args.prompt_only: raise SystemExit('Refusing to repeat recorded acceptance run')
    os.environ['CONCIERGE_ENV']='test';os.environ['CONCIERGE_RUNTIME_PROFILE']='test'
    from unittest.mock import patch
    from concierge_kiosk.runtime import local_ai,local_http
    from concierge_kiosk.core.settings import Settings
    from concierge_kiosk.agent.understanding import commands
    from concierge_kiosk.application.conversation.engine import _TurnRuntimeSupport
    from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS
    from fastapi.testclient import TestClient
    source=Path('data/concierge.sqlite3')
    digest=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    record={'source_sha256_before':digest(source),'historical_qwen_calls':3,
            'preload':{'result':'NOT_RUN'},'guest':{'result':'NOT_RUN'}}
    def persist():
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    profile=json.loads(Path('config/runtime-profiles/development.json').read_text(encoding='utf-8'))
    base,model=(profile['models']['slm'][k] for k in ('base_url','primary_model'))
    captured=[]
    with patch.object(commands,'_chat',lambda b,p,t,c: captured.append(p) or None):
        commands.model_commands(query='cho toi 3 chai nuoc suoi phong 502',language='vi',
            enabled_request_kinds=ACTION_REQUEST_KINDS,base_url=base,model=model,num_gpu=0)
    record['prompt_diagnostics']=prompt_diagnostics(captured[0])
    if args.prompt_only:
        print(json.dumps(record['prompt_diagnostics'],ensure_ascii=True));return 0
    boundary=CallBoundary(local_http._OPENER.open,model,persist)
    record['calls'],record['protocol_errors']=boundary.calls,boundary.errors
    log=Path(os.environ['LOCALAPPDATA'])/'Ollama/server.log'
    log_offset=log.stat().st_size if log.exists() else None
    def finish():
        record['source_sha256_after']=digest(source)
        record['new_qwen_http_calls']=len(boundary.calls)
        record['total_qwen_http_calls']=3+len(boundary.calls)
        if log_offset is not None:
            with log.open('rb') as f:
                f.seek(log_offset);record['ollama_log_since_start']=f.read().decode('utf-8',errors='replace')
        persist()
        print(json.dumps({k:record[k] for k in ('new_qwen_http_calls','total_qwen_http_calls','preload','guest')},ensure_ascii=True)[:1500])
    with tempfile.TemporaryDirectory(prefix='agent-warm-nlu-') as directory:
        db=Path(directory)/'smoke.sqlite3';shutil.copyfile(source,db)
        os.environ['CONCIERGE_DB_PATH']=str(Path(directory)/'composition.sqlite3')
        with patch.object(local_http._OPENER,'open',boundary.open),patch.object(local_ai,'warm_local_slm',lambda *a,**kw: False):
            from concierge_kiosk.main import create_app
            from concierge_kiosk.voice.runtime import adapters
            cfg=Settings(db_path=db,environment='test',property_id='FURAMA_DANANG',
                property_name='Furama Resort Danang',property_timezone='Asia/Ho_Chi_Minh',
                property_profile_path='releases/property-profile.json',
                property_profile_sha256=Path('releases/property-profile.sha256').read_text().strip(),
                map_release_path='releases/map-release.json',
                map_release_sha256=Path('releases/map-release.sha256').read_text().strip(),
                llm_base_url=base,llm_model=model,llm_model_digest=args.expected_digest,
                llm_fallback_model='',slm_num_gpu=0,embedding_model_path='',embedding_manifest_path='',
                rerank_model_path='',rerank_manifest_path='',semantic_understanding_enabled=False,
                semantic_generation_enabled=False,agent_planner_enabled=False)
            record['config']={'model':model,'digest':args.expected_digest,'num_gpu':0,
                'nlu_timeout':cfg.intent_parser_timeout_seconds,'turn_budget':cfg.slm_generation_timeout_seconds,
                'preload_timeout':args.preload_timeout,'keep_alive':'5m','embedding':'disabled',
                'planner':False,'semantic_generation':False,'warm_up':'disabled'}
            readiness=local_ai.model_residency(base,model,args.expected_digest)
            record['preflight']=readiness;record['memory_before']=memory=_memory()
            other=[m for m in readiness.get('resident') or [] if m.get('name')!=model]
            installed=next((m for m in readiness.get('installed') or [] if m.get('name')==model),{})
            if (readiness['state'] not in {'MODEL_NOT_LOADED','MODEL_READY'} or other
                    or memory['available_bytes']<installed.get('size',0)+1024**3):
                record['preload']={'result':'BLOCKED','reason':'Identity, competing model or RAM preflight'}
                finish();return 1
            try:
                if readiness['state']=='MODEL_NOT_LOADED':
                    boundary.phase='PRELOAD';started=time.monotonic()
                    result=local_ai.preload_local_slm(base,model,timeout=args.preload_timeout,num_gpu=0)
                    record['preload']={'result':'PASS','elapsed_seconds':time.monotonic()-started,'response':result}
                else: record['preload']={'result':'NOT_RUN','reason':'Already resident with matching digest'}
                readiness=local_ai.model_residency(base,model,args.expected_digest)
                record['readiness_after_preload']=readiness
                if readiness['state']!='MODEL_READY' or len(readiness['resident'])!=1:
                    raise RuntimeError('Model did not become exclusively ready')
                resident=readiness['resident'][0]
                if resident.get('size_vram')!=0 or resident.get('context_length')!=4096:
                    raise RuntimeError('Resident device/context differs from CPU contract')
                record['memory_after_preload']=_memory()
                if record['memory_after_preload']['available_bytes']<512*1024**2:
                    raise RuntimeError('Insufficient memory headroom')
            except Exception as exc:
                record['preload']={'result':'TIMEOUT' if isinstance(exc,TimeoutError) else 'BLOCKED',
                    'failure_class':'PRELOAD_FAILED','error':str(exc)}
                finish();return 1
            raw,outcomes,captures=[],[],[]
            original_chat=commands._chat;original_model=commands.model_commands
            original_understand=_TurnRuntimeSupport.understand_turn
            def traced_chat(*a,**kw):
                answer=original_chat(*a,**kw);raw.append(answer);return answer
            def traced_model(**kw):
                old=kw.get('on_outcome')
                def outcome(value):
                    outcomes.append(value)
                    if old: old(value)
                kw['on_outcome']=outcome;return original_model(**kw)
            def traced_understand(self,*a,**kw):
                result=original_understand(self,*a,**kw)
                captures.append({'route':result[0].branch,'commands':[c.public() for c in result[3] or ()],
                    'anchors':[asdict(anchor) for anchor in self.live_context_anchors(a[2],a[1])]})
                return result
            from concierge_kiosk.application.conversation import engine
            with patch.object(adapters,'warm_voice_models',lambda *_: []),patch.object(commands,'_chat',traced_chat), \
                    patch.object(engine,'model_commands',traced_model),patch.object(_TurnRuntimeSupport,'understand_turn',traced_understand):
                app=create_app(cfg)
                with TestClient(app) as client:
                    session=client.post('/api/session').json()
                    with app.state.store.connection() as con:
                        before=con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0]
                    boundary.phase='NLU';started=time.monotonic()
                    response=client.post('/api/ask',headers={'X-CSRF-Token':session['csrf_token']},
                        json={'query':'cho toi 3 chai nuoc suoi phong 502','language':'vi','turn_nonce':'warm-nlu-12345678'})
                    elapsed=time.monotonic()-started;body=response.json()
                    with app.state.store.connection() as con:
                        writes=con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0]-before
                    payload=body.get('service_payload') or {}
                    plain=lambda s: ''.join(c for c in unicodedata.normalize('NFD',str(s)).lower() if not unicodedata.combining(c))
                    passed=(response.status_code==200 and writes==0 and payload.get('quantity')==3
                        and payload.get('room_number')=='502' and plain(payload.get('unit'))=='chai'
                        and plain(payload.get('requested_item'))=='nuoc suoi'
                        and (body.get('suggested_action') or {}).get('service')=='amenity_delivery')
                    result=('TIMEOUT' if any(e['failure_class']=='TIMEOUT' for e in boundary.errors)
                        else 'BLOCKED' if boundary.errors or not raw or raw[-1] is None
                        else 'REAL_MODEL_PASS' if passed else 'FAIL')
                    record['guest']={'result':result,'failure_class':'WARM_NLU_TIMEOUT' if result=='TIMEOUT' else result,
                        'session':'A-anonymized','input':'cho toi 3 chai nuoc suoi phong 502',
                        'raw_model_response':raw,'validation_outcomes':outcomes,'understanding':captures,
                        'response':body,'http_status':response.status_code,'api_elapsed_seconds':elapsed,
                        'business_write_delta':writes}
            boundary.phase='CLOSED'
    finish()
    return 0 if record['guest']['result']=='REAL_MODEL_PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
