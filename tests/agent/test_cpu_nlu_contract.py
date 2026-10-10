"""Compact schema/server authority and explicit NLU recovery: mocked HTTP only."""
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from concierge_kiosk.agent.understanding import commands, semantic
from concierge_kiosk.agent.understanding.routing import RouteDecision, fast_response
from concierge_kiosk.application.conversation.engine import _TurnRuntimeSupport
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS, SERVICE_DEFINITIONS, accepted_slots
from concierge_kiosk.runtime import local_http
from concierge_kiosk.core.domain_profile import get_domain_profile


GOALS = {code: accepted_slots(code) for code, definition in SERVICE_DEFINITIONS.items()
         if definition.request_kind in ACTION_REQUEST_KINDS and definition.request_kind != 'directions'}


@pytest.fixture(autouse=True)
def no_model_http(monkeypatch):
    monkeypatch.setattr(local_http._OPENER, 'open',
                        lambda *a, **kw: pytest.fail('No real model HTTP permitted'))


def test_every_command_type_in_the_prompt_spec_parses():
    policy = get_domain_profile().semantic_authorization
    spec = commands.command_output_spec()
    concept = policy['services']['amenity_delivery']['concepts']['en'][0]
    meanings = policy['preferences']['party_size']
    party = meanings.get('integer', next(iter(meanings.values())))['en'][0]
    samples = [
        ({'type': 'StartGoal', 'goal': 'amenity_delivery', 'slots': []}, f'please {concept}'),
        ({'type': 'AskInfo', 'query': 'guest words'}, 'guest words'),
        ({'type': 'Navigate', 'query': 'guest words'}, 'guest words'),
        ({'type': 'Plan', 'query': 'guest words'}, 'guest words'),
        ({'type': 'AskStatus'}, 'guest words'), ({'type': 'Cancel'}, 'guest words'),
        ({'type': 'Modify'}, 'guest words'), ({'type': 'Clarify'}, 'guest words'),
        ({'type': 'Confirm', 'confirmed': True}, 'guest words'),
        ({'type': 'Handoff', 'reason': 'guest words'}, f'please {policy["services"][policy["handoff_goal"]]["concepts"]["en"][0]}'),
        ({'type': 'ChitChat', 'kind': 'thanks'}, 'guest words'),
        ({'type': 'SwitchLanguage', 'target': 'vi'}, 'guest words'),
        ({'type': 'SetPreference', 'field': 'party_size', 'value': '3', 'evidence': f'3 {party}'}, f'3 {party}'),
    ]
    for command, query in samples:
        assert command['type'] in spec
        assert commands.parse_commands(json.dumps({'commands': [command]}), query=query,
                                       enabled_request_kinds=ACTION_REQUEST_KINDS,
                                       pending_reply='confirm'), command


def test_item_date_unit_and_multi_intent_fixtures():
    items = [{'type': 'StartGoal', 'goal': 'amenity_delivery', 'conditional': False,
              'slots': [{'name': k, 'text': v} for k, v in
                        {'requested_item':'water', 'quantity':'3', 'unit':'bottles','room_number':'502'}.items()]},
             {'type':'StartGoal', 'goal':'wake_up_call','conditional':False,
              'slots':[{'name':'requested_date','text':'tomorrow'},{'name':'preferred_time','text':'6am'}]},
             {'type':'AskInfo','query':'spa hours'}]
    body = {'commands':items}
    kept = commands.parse_commands(json.dumps(body), query='Please bring 3 bottles water to 502; wake me tomorrow 6am; spa hours',
                                   enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert kept and [c.public() for c in kept] == [
        {k:v for k,v in item.items() if k != 'conditional'} for item in items]


@pytest.mark.parametrize('bad', [
    {'type':'StartGoal','goal':'unknown','slots':[]},
    {'type':'ExecuteSQL','query':'guest words'},
    {'type':'Confirm','confirmed':True},
    {'type':'Confirm','confirmed':True,'capability':'commit'},
    {'type':'StartGoal','goal':'amenity_delivery','conditional':'false'},
    {'type':['AskInfo'],'query':'guest words'},
    {'type':'StartGoal','goal':['amenity_delivery'],'slots':[]},
    {'type':'AskInfo','query':{'sql':'guest words'}},
    {'type':'SetSlot','field':'room_number','value':'999'},
    {'type':'StartGoal','slots':'wrong'},
    {'type':'StartGoal','goal':'amenity_delivery','slots':[{'name':'quantity','text':2}]},
    {'type':'Handoff','reason':(' '*160)+'guest words'},
])
def test_invalid_command_is_dropped_without_losing_valid_clause(bad):
    raw = json.dumps({'commands':[bad, {'type':'AskInfo','query':'guest words'}]})
    kept = commands.parse_commands(raw, query='guest words', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert kept and [c.type for c in kept] == ['AskInfo']


def test_union_slot_schema_is_not_permission_for_other_goal():
    body = {'commands':[{'type':'StartGoal','goal':'wake_up_call','conditional':False,
                         'slots':[{'name':'quantity','text':'3'}, {'name':'room_number','text':'999'}]}]}
    kept = commands.parse_commands(json.dumps(body), query='3 wake up', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert kept and kept[0].slots == ()  # unsupported quantity and invented room both removed


def test_every_registry_goals_valid_slots_survive():
    values = {'quantity':'3','requested_item':'water','unit':'bottles','room_number':'502',
              'preferred_time':'06:00','requested_date':'2026-10-12','party_size':'4',
              'destination':'airport','issue':'leak','restaurant_name':'restaurant'}
    for goal,names in GOALS.items():
        slots = [{'name':name,'text':values[name]} for name in names]
        body = {'commands':[{'type':'StartGoal','goal':goal,'slots':slots,'conditional':False}]}
        concept = get_domain_profile().semantic_authorization['services'][goal]['concepts']['en'][0]
        kept = commands.parse_commands(json.dumps(body),query='please ' + concept + ' ' + ' '.join(values.values()),
                                       enabled_request_kinds=ACTION_REQUEST_KINDS)
        assert kept and [s.public() for s in kept[0].slots] == slots


def test_conditional_and_compound_confirm_handoff_context_survive():
    body = {'commands':[
        {'type':'StartGoal','goal':'dining_reservation','slots':[],'conditional':True,'refers_to_context':False},
        {'type':'Confirm','confirmed':True},
        {'type':'Handoff','reason':'staff assistance'},
        {'type':'Navigate','query':'reach that place','refers_to_context':True},
        {'type':'AskInfo','query':'spa hours','refers_to_context':False}]}
    kept = commands.parse_commands(json.dumps(body),query='if a table is free, book it; please send staff assistance; reach that place; spa hours',
                                   enabled_request_kinds=ACTION_REQUEST_KINDS,pending_reply='confirm')
    assert kept and kept[0].conditional and kept[3].refers_to_context
    assert [c.type for c in kept] == [c['type'] for c in body['commands']]


@pytest.mark.parametrize('raw', ['not json','[]','{"commands":[]}',
    '{"commands":[],"extra":1}', json.dumps({'commands':[{'type':'Clarify'}]*9})])
def test_invalid_envelope_and_original_count_fail_closed(raw):
    assert commands.parse_commands(raw, query='guest words', enabled_request_kinds=ACTION_REQUEST_KINDS) is None


def support(*, busy=False, permitted=True, configured=True):
    cfg = SimpleNamespace(llm_base_url='http://127.0.0.1:11434' if configured else '',
        llm_model='mock', llm_candidates=lambda: ('mock',), slm_num_gpu=0,
        intent_parser_timeout_seconds=3, voice_slm_caps={'intent':3})
    return _TurnRuntimeSupport(cfg=cfg, workflows=SimpleNamespace(),
        conversations=SimpleNamespace(workflow_projection=lambda *a: None, recent_anchors=lambda *a: ()),
        agent_tasks=SimpleNamespace(load=lambda *a: None, clear=lambda *a: None),
        agent_checkpoints=None, slm_permitted=lambda: permitted,
        audio_admission=SimpleNamespace(try_enter_slm=lambda *a: not busy,
            leave_slm=lambda: None, slm_cancelled=lambda *a: False))


@pytest.mark.parametrize('error,expected', [(TimeoutError('deadline'),'NLU_TIMEOUT'),
    (URLError(TimeoutError('deadline')),'NLU_TIMEOUT'),(ConnectionError('offline'),'NLU_UNAVAILABLE')])
def test_transport_failure_never_becomes_knowledge_or_resolver(monkeypatch,error,expected):
    monkeypatch.setattr(semantic,'urlopen',lambda *a,**kw: (_ for _ in ()).throw(error))
    runtime = support()
    monkeypatch.setattr(runtime,'fallback_commands',lambda *a,**kw: pytest.fail('No service substitution'))
    monkeypatch.setattr(runtime,'reference_command_for_session',lambda *a,**kw: pytest.fail('No retry'))
    decision,ctx,q,items = runtime.understand_turn('guest request','en','s',RouteDecision('knowledge'),
                                                enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert decision.branch == 'nlu_failure' and decision.failure_class == expected
    assert items is None
    result = fast_response(decision,q,'en')
    assert result['suggested_action'] is None and result['business_writes'] == 0
    assert result['retrieval_mode'] == 'not_used' and result['grounding'] != 'no_evidence'


@pytest.mark.parametrize('options,expected', [({'busy':True},'MODEL_BUSY'),
    ({'permitted':False},'MODEL_NOT_READY'),({'configured':False},'NLU_UNAVAILABLE')])
def test_admission_readiness_and_configuration_do_not_issue_http(options,expected):
    runtime = support(**options)
    decision,*_ = runtime.understand_turn('guest request','en','s',RouteDecision('knowledge'),
                                         enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert decision.branch == 'nlu_failure' and decision.failure_class == expected


def test_invalid_model_output_recovers_without_retry(monkeypatch):
    monkeypatch.setattr(commands,'_chat',lambda *a: '{"commands":[{"type":"ForgedTool"}]}')
    decision,*_ = support().understand_turn('guest request','en','s',RouteDecision('knowledge'),
                                           enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert decision.failure_class == 'INVALID_MODEL_OUTPUT'


def test_failure_does_not_poison_the_next_sessions_valid_command(monkeypatch):
    monkeypatch.setattr(semantic,'urlopen',lambda *a,**kw: (_ for _ in ()).throw(TimeoutError()))
    first,*_ = support().understand_turn('guest request','en','s1',RouteDecision('knowledge'),
                                        enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert first.failure_class == 'NLU_TIMEOUT'
    monkeypatch.setattr(commands,'_chat',lambda *a: '{"commands":[{"type":"AskInfo","query":"spa hours"}]}')
    second,_,_,items = support().understand_turn('spa hours','en','s2',RouteDecision('knowledge'),
                                                 enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert second.branch == 'knowledge' and second.failure_class is None
    assert items and items[0].query == 'spa hours'


@pytest.mark.parametrize('language', ['vi','en','zh','ko'])
def test_recovery_copy_and_response_contract(language):
    from concierge_kiosk.i18n import text
    from concierge_kiosk.agent.core.tool_contracts import validate_tool_result
    for key in ('nlu.retry','nlu.clarify'):
        assert '?' not in text(key,language)
    decision = RouteDecision('nlu_failure',True,failure_class='NLU_TIMEOUT')
    validate_tool_result(decision, fast_response(decision,'guest request',language),'guest request',language)


def test_emergency_bypasses_command_transport(monkeypatch):
    runtime = support()
    monkeypatch.setattr(runtime,'command_for_session',lambda *a,**kw: pytest.fail('No emergency model'))
    decision,*_ = runtime.understand_turn('fire','en','s',RouteDecision('emergency',True),
                                         enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert decision.branch == 'emergency'


def test_guest_api_timeout_preserves_draft_and_does_not_run_agent_tools(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from concierge_kiosk.core.settings import Settings
    from concierge_kiosk.main import create_app
    from concierge_kiosk.runtime import local_ai
    from concierge_kiosk.voice.runtime import adapters
    monkeypatch.setattr(local_ai,'warm_local_slm',lambda *a,**kw: False)
    monkeypatch.setattr(adapters,'warm_voice_models',lambda *a: [])
    monkeypatch.setattr(semantic,'urlopen',lambda *a,**kw: (_ for _ in ()).throw(TimeoutError('header timeout')))
    app = create_app(Settings(environment='test',db_path=tmp_path/'guest.sqlite3',
                              llm_base_url='http://127.0.0.1:11434',llm_model='mock',
                              llm_fallback_model='',slm_num_gpu=0,
                              property_id='FURAMA_DANANG',property_name='Furama Resort Danang',
                              property_timezone='Asia/Ho_Chi_Minh',
                              property_profile_path='releases/property-profile.json',
                              property_profile_sha256=Path('releases/property-profile.sha256').read_text().strip()))
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        sid = session['session_id']
        proposal = app.state.workflows.prepare(sid,'facilities','en','Water for room 502',
            'offline-proposal-123',{'requested_item':'water','quantity':3,'unit':'bottles','room_number':'502'},
            service_code='amenity_delivery')
        # A saved proposal stays pending; a timeout cannot confirm or discard it.
        app.state.conversations.remember_expected_reply(sid,'en','confirm')
        monkeypatch.setattr(app.state.concierge_agent,'run',lambda *a,**kw: pytest.fail('No fallback tool execution'))
        response = client.post('/api/ask',headers={'X-CSRF-Token':session['csrf_token']},
                               json={'query':'Please change the quantity','language':'en'})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['failure_class'] == 'NLU_TIMEOUT' and body['tool_route'] == 'nlu_failure'
        assert body['suggested_action'] is None and body['understanding_commands'] == []
        assert body['speech_plan']['chunks'] and not body.get('tool_calls')
        assert app.state.conversations.workflow_projection(sid,'en')['expected_reply'] == 'confirm'
        with app.state.store.connection() as con:
            assert con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] == 0
            row = con.execute('SELECT * FROM proposals WHERE id=?',(proposal['id'],)).fetchone()
            assert row is not None and row['status'] == 'awaiting_confirmation'
            assert json.loads(row['payload_json'])['requested_item'] == 'water'
        Path('reports').mkdir(exist_ok=True)
        Path('reports/cpu-nlu-recovery.json').write_text(json.dumps({
            'transport':'MOCK_TIMEOUT', 'real_qwen_calls':0, 'session':'anonymized',
            'input':'Please change the quantity', 'response':body,
            'business_writes':0, 'proposal_status_after':'awaiting_confirmation'},
            ensure_ascii=False,indent=2),encoding='utf-8')
