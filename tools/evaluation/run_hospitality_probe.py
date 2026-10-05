"""Probe Hospitality direct-style hospitality scenarios through the real HTTP boundary.

This is a product-gap probe, not a Furama KPI. Context-dependent status cases are
excluded because they require a preceding request in the same session.
"""
from __future__ import annotations
import json, os, shutil, sqlite3, tempfile, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / 'src'))
from concierge_kiosk.core.dataset_layout import dataset_path

SUITE=dataset_path('evaluation/end_to_end/scenarios/production.jsonl')


def configure(db):
    os.environ.update({'PYTHON_DOTENV_DISABLED':'1','CONCIERGE_ENV':'test','CONCIERGE_RUNTIME_PROFILE':'test','CONCIERGE_DB_PATH':str(db),'CONCIERGE_ORCHESTRATOR':'direct','CONCIERGE_SEMANTIC_GENERATION_ENABLED':'false','CONCIERGE_INTENT_PARSER_ENABLED':'false'})
    for line in (ROOT/'config/local-runtime.env.example').read_text(encoding='utf-8').splitlines():
        if '=' not in line or line.startswith('#'): continue
        k,v=line.split('=',1)
        if k.startswith(('CONCIERGE_PROPERTY_PROFILE_','CONCIERGE_MAP_RELEASE_','CONCIERGE_PLANNING_RELEASE_')):
            if k.endswith('_PATH'): v=str((ROOT/v).resolve())
            os.environ[k]=v


def oracle(case,p,status):
    if status!=200: return False,f'http_{status}'
    route=case['expected_route']; action=p.get('agent_action') or {}; writes=action.get('business_writes') or 0
    if route=='service':
        return p.get('tool_route')=='service' and action.get('service_mode')==case['service_code'], 'service_route'
    if route=='knowledge':
        return p.get('tool_route')=='knowledge' and p.get('evidence_status')=='SUPPORTED', 'grounded_knowledge'
    if route=='knowledge_abstain':
        return p.get('tool_route')=='knowledge' and p.get('evidence_status')=='UNSUPPORTED', 'unsupported_abstention'
    if route=='emergency':
        return p.get('tool_route')=='emergency', 'emergency'
    if route=='safety_escalation':
        return p.get('tool_route')=='emergency' or p.get('safety_sos_available') is True, 'safety_escalation'
    if route=='clarification':
        ok=(writes==0 and p.get('tool_route')!='emergency' and (p.get('action_options') or p.get('evidence_status')=='UNSUPPORTED' or p.get('requires_clarification')))
        return bool(ok),'clarification_no_write'
    if route=='non_action':
        return writes==0 and p.get('tool_route')!='emergency','non_action_no_write'
    if route=='privacy_guard':
        statuses=p.get('request_statuses') or []
        return writes==0 and not statuses,'privacy_no_disclosure'
    if route=='multi_step':
        return p.get('tool_route')=='service' and status==200,'multi_step_boundary'
    return True,'observed_only'


def run(output: Path, language: str | None = None):
    cases=[json.loads(x) for x in SUITE.read_text(encoding='utf-8').splitlines() if x.strip()]
    # The production scenario schema intentionally keeps only the behavioral
    # oracle fields; older probe releases expected optional ``surface_style``
    # and ``concept_key`` columns that are not part of the pinned dataset.
    # Missing surface metadata means the case is a normal direct HTTP turn.
    cases=[r for r in cases if r.get('surface_style', 'direct')=='direct'
           and r['expected_route']!='status'
           and (language is None or r['language']==language)]
    td=Path(tempfile.mkdtemp(prefix='hospitality-probe-')); db=td/'concierge.sqlite3'; shutil.copy2(ROOT/'data/concierge.sqlite3',db); configure(db)
    import sys; sys.path.insert(0,str(ROOT/'src'))
    from fastapi.testclient import TestClient
    from concierge_kiosk.main import create_app
    rows=[]
    with TestClient(create_app(),raise_server_exceptions=False) as client:
        for i,c in enumerate(cases,1):
            s=client.post('/api/session').json(); t=time.perf_counter()
            r=client.post('/api/ask',headers={'X-CSRF-Token':s['csrf_token']},json={'query':c['utterance'],'language':c['language'],'turn_nonce':f'hospitality-probe-{i:04d}'})
            ms=(time.perf_counter()-t)*1000
            try: p=r.json()
            except Exception: p={}
            ok,rule=oracle(c,p,r.status_code)
            rows.append({'scenario_id':c['scenario_id'],'concept_key':c.get('concept_key', c['scenario_id']),
                         'language':c['language'],'expected_route':c['expected_route'],
                         'expected_service':c.get('service_code'),'utterance':c['utterance'],
                         'passed':ok,'oracle':rule,'http_status':r.status_code,
                         'latency_ms':round(ms,2),'tool_route':p.get('tool_route'),
                         'evidence_status':p.get('evidence_status'),
                         'service_mode':(p.get('agent_action') or {}).get('service_mode'),
                         'requires_staff_review':p.get('requires_staff_review'),
                         'answer':(p.get('answer') or '')[:220]})
            with sqlite3.connect(db) as con: con.execute('DELETE FROM rate_limits'); con.commit()
    by_lang={l:{'cases':sum(x['language']==l for x in rows),'passed':sum(x['language']==l and x['passed'] for x in rows)} for l in ('vi','en','ko','zh')}
    failed=[x for x in rows if not x['passed']]
    result={'classification':'local_hospitality_gap_probe','environment_guard':'Local TestClient/test profile/temp SQLite; status journeys excluded; not a Furama KPI.','cases':len(rows),'passed':len(rows)-len(failed),'pass_rate':round((len(rows)-len(failed))/len(rows),4),'by_language':by_lang,'failures':failed,'results':rows}
    output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument('--lang', choices=['vi','en','ko','zh']); ap.add_argument('--output', type=Path); args=ap.parse_args()
    out=args.output or ROOT/f'evaluation-results/hospitality-direct-probe-{args.lang or 'all'}.json'
    r=run(out,args.lang); print(json.dumps({k:r[k] for k in ['cases','passed','pass_rate','by_language']},ensure_ascii=False))
