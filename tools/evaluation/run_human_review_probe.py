"""Run context-independent reviewed direct scenarios through the real HTTP boundary."""
from __future__ import annotations
import json, os, shutil, sqlite3, tempfile, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / 'src'))
from concierge_kiosk.core.dataset_layout import EVAL_SERVICE_ACTIONS, dataset_path

SUITE=dataset_path('evaluation/holdout/service_workflow.jsonl')

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
    expected_facts = case.get('expected_facts', [])
    if expected_facts:
        answer = str(p.get('answer') or '').casefold()
        cited = ' '.join(str(item.get('quote') or item.get('claim') or '')
                          for item in (p.get('citations') or [])
                          if isinstance(item, dict)).casefold()
        haystack = answer + ' ' + cited
        missing = [fact for fact in expected_facts
                   if not isinstance(fact, str) or fact.casefold().strip() not in haystack]
        if missing:
            return False,'expected_facts_missing'
    route=case['expected_route']; action=p.get('agent_action') or {}; writes=action.get('business_writes') or 0
    if route=='service':
        ok=p.get('tool_route')=='service' and action.get('service_mode')==case['service_code']
        if case['approval_path']=='staff': ok=ok and bool(p.get('requires_staff_review'))
        return bool(ok),'service_route_policy'
    if route=='knowledge': return p.get('tool_route')=='knowledge' and p.get('evidence_status')=='SUPPORTED','grounded_knowledge'
    if route=='knowledge_abstain': return p.get('tool_route')=='knowledge' and p.get('evidence_status')=='UNSUPPORTED','unsupported_abstention'
    if route=='emergency': return p.get('tool_route')=='emergency','emergency'
    if route=='clarification':
        return bool(writes==0 and p.get('tool_route')!='emergency' and (p.get('action_options') or p.get('evidence_status')=='UNSUPPORTED' or p.get('requires_clarification'))),'clarification'
    if route=='privacy_guard': return bool(writes==0 and not (p.get('request_statuses') or [])),'privacy_guard'
    if route=='policy_guard': return bool(writes==0 and p.get('tool_route')!='emergency'),'policy_guard'
    if route=='multi_step':
        # The reviewed oracle requires the dedicated multi-task boundary. Mixed consequential tasks
        # must surface staff review; all-low-risk tasks must not.
        if p.get('tool_route')!='multi_task': return False,'multi_step_boundary'
        expected_staff=bool(case.get('expected_staff_review'))
        return bool(p.get('requires_staff_review'))==expected_staff,'multi_step_boundary'
    if route=='request_change':
        return p.get('tool_route')=='request_change' or bool(p.get('request_change')),'request_change'
    return True,'observed_only'

def run(out,lang):
    cases=[json.loads(x) for x in SUITE.read_text(encoding='utf-8').splitlines() if x.strip()]
    cases=[c for c in cases if c['surface_style']=='direct' and c['language']==lang and not c['context_requirements'] and c['expected_route']!='status']
    td=Path(tempfile.mkdtemp(prefix='human-probe-')); db=td/'concierge.sqlite3'; shutil.copy2(ROOT/'data/concierge.sqlite3',db); configure(db)
    import sys; sys.path.insert(0,str(ROOT/'src'))
    from fastapi.testclient import TestClient
    from concierge_kiosk.main import create_app
    rows=[]
    with TestClient(create_app(),raise_server_exceptions=False) as client:
        for i,c in enumerate(cases,1):
            s=client.post('/api/session').json(); t=time.perf_counter()
            r=client.post('/api/ask',headers={'X-CSRF-Token':s['csrf_token']},json={'query':c['utterance'],'language':lang,'turn_nonce':f'human-probe-{lang}-{i:04d}'})
            ms=(time.perf_counter()-t)*1000
            try:p=r.json()
            except Exception:p={}
            ok,rule=oracle(c,p,r.status_code)
            rows.append({'scenario_id':c['scenario_id'],'concept_key':c['concept_key'],'language':lang,'expected_route':c['expected_route'],'expected_service':c['service_code'],'passed':ok,'oracle':rule,'http_status':r.status_code,'latency_ms':round(ms,2),'tool_route':p.get('tool_route'),'evidence_status':p.get('evidence_status'),'service_mode':(p.get('agent_action') or {}).get('service_mode'),'requires_staff_review':p.get('requires_staff_review'),'answer':(p.get('answer') or '')[:220]})
            with sqlite3.connect(db) as con: con.execute('DELETE FROM rate_limits'); con.commit()
    result={'classification':'local_human_gold_gap_probe','human_review_boundary':'AI-simulated expert gold, not Furama staff labels.','environment_guard':'Local TestClient/test profile; semantic/model fallback disabled; context-dependent scenarios excluded.','language':lang,'cases':len(rows),'passed':sum(x['passed'] for x in rows),'pass_rate':round(sum(x['passed'] for x in rows)/len(rows),4),'failures':[x for x in rows if not x['passed']],'results':rows}
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['language','cases','passed','pass_rate']},ensure_ascii=False))
if __name__=='__main__':
    import argparse; ap=argparse.ArgumentParser(); ap.add_argument('--lang',required=True,choices=['vi','en','ko','zh']); ap.add_argument('--output',type=Path); a=ap.parse_args(); run(a.output or ROOT/f'evaluation-results/human-gold-probe-{a.lang}.json',a.lang)
