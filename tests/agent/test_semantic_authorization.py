"""Semantic authorization is independent of the model's closed command syntax."""
import json
import pytest

from concierge_kiosk.agent.understanding.commands import Command, CommandSlot, parse_commands, validate_commands
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS, SERVICE_DEFINITIONS
from concierge_kiosk.core.domain_profile import get_domain_profile

WP12_QUERY = 'cho toi 3 chai nuoc suoi phong 502'
WP12_RAW = '{ "commands": [{"type":"StartGoal","goal":"housekeeping","slots":[],"conditional":false},{"type":"SetPreference","field":"quiet","value":"quiet","evidence":"cho toi 3 chai nuoc suoi phong 502"}] }'


def test_recorded_wrong_service_and_preference_are_rejected():
    assert parse_commands(WP12_RAW, query=WP12_QUERY,
                          language='vi', enabled_request_kinds=ACTION_REQUEST_KINDS) is None


@pytest.mark.parametrize('query,goal,item', [
    ('Please explain housekeeping', 'housekeeping', None),
    ('Please help with the room', 'housekeeping', None),
    ('Please bring pizza', 'amenity_delivery', 'pizza'),
    ('Please bring cleaning supplies', 'housekeeping', None),
])
def test_information_ambiguity_and_conflicting_object_do_not_authorize_goal(query, goal, item):
    slots = (CommandSlot('requested_item', item),) if item else ()
    assert validate_commands((Command('StartGoal', goal=goal, slots=slots),),
        query=query, language='en', enabled_request_kinds=ACTION_REQUEST_KINDS) is None


@pytest.mark.parametrize('goal', list(SERVICE_DEFINITIONS))
@pytest.mark.parametrize('language,prefix', [('en','please'),('vi','giúp'),('zh','请'),('ko','부탁')])
def test_complete_enabled_registry_is_groundable(goal, language, prefix):
    concept = get_domain_profile().semantic_authorization['services'][goal]['concepts'][language][0]
    query = f'{prefix} {concept}'
    assert validate_commands((Command('StartGoal', goal=goal),), query=query, language=language,
                             enabled_request_kinds=ACTION_REQUEST_KINDS)


@pytest.mark.parametrize('query,language,item,unit', [
    ('Please bring 3 bottles of water to room 502', 'en', 'water', 'bottles'),
    ('Cho tôi 3 chai nước suối phòng 502', 'vi', 'nước suối', 'chai'),
    ('请送3瓶矿泉水到502房间', 'zh', '矿泉水', '瓶'),
    ('502호에 생수 3병 가져다 주세요', 'ko', '생수', '병'),
])
def test_natural_multilingual_delivery_keeps_guest_slots(query, language, item, unit):
    command = Command('StartGoal', goal='amenity_delivery', slots=(
        CommandSlot('requested_item', item), CommandSlot('unit', unit),
        CommandSlot('quantity', '3'), CommandSlot('room_number', '502')))
    accepted = validate_commands((command,), query=query, language=language,
                                 enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert accepted and accepted[0].slots == command.slots


@pytest.mark.parametrize('query,language', [
    ('Could you tidy up our suite please', 'en'),
    ('Nhờ dọn dẹp phòng giúp tôi', 'vi'),
    ('请打扫我的房间', 'zh'),
    ('객실 청소해 주세요', 'ko'),
])
def test_legitimate_housekeeping_paraphrases(query, language):
    assert validate_commands((Command('StartGoal', goal='housekeeping'),),
        query=query, language=language, enabled_request_kinds=ACTION_REQUEST_KINDS)


@pytest.mark.parametrize('query,evidence,language', [
    ('I prefer a quiet room', 'quiet room', 'en'),
    ('Tôi muốn phòng yên tĩnh', 'yên tĩnh', 'vi'),
    ('我想要安静的房间', '安静', 'zh'),
    ('조용한 방을 원합니다', '조용', 'ko'),
])
def test_quiet_preference_has_meaning_and_still_requires_review(query, evidence, language):
    command = Command('SetPreference', field='quiet', value='quiet', evidence=evidence)
    assert validate_commands((command,), query=query, language=language)


@pytest.mark.parametrize('query,evidence,language', [
    ('I do not want a quiet room', 'quiet room', 'en'),
    ('Tôi không muốn phòng yên tĩnh', 'yên tĩnh', 'vi'),
    ('我不要安静的房间', '安静', 'zh'),
    ('조용한 방은 원하지 않아요', '조용', 'ko'),
    ('What is a quiet room?', 'quiet room', 'en'),
    ('I dislike quiet rooms', 'quiet', 'en'),
    ('I want a quiet room, but I do not want a quiet room now', 'quiet room', 'en'),
])
def test_negation_or_knowledge_mention_cannot_authorize_preference(query, evidence, language):
    assert validate_commands((Command('SetPreference', field='quiet', value='quiet', evidence=evidence),),
        query=query, language=language) is None


def test_later_preference_correction_controls_current_field_value():
    query = 'I am vegetarian, but I want vegan meals'
    assert validate_commands((Command('SetPreference', field='dietary', value='vegetarian',
        evidence='vegetarian'),), query=query, language='en') is None
    assert validate_commands((Command('SetPreference', field='dietary', value='vegan',
        evidence='vegan'),), query=query, language='en')


def test_valid_siblings_survive_wrong_service_or_preference():
    query = 'Please bring towels to room 502, and what time does the spa close?'
    commands = (Command('StartGoal', goal='housekeeping'),
        Command('SetPreference', field='quiet', value='quiet', evidence=query),
        Command('StartGoal', goal='amenity_delivery', slots=(CommandSlot('requested_item','towels'),)),
        Command('AskInfo', query='what time does the spa close?'))
    accepted = validate_commands(commands, query=query, language='en', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert [c.type for c in accepted] == ['StartGoal','AskInfo']
    assert accepted[0].goal == 'amenity_delivery'


def test_unknown_delivered_item_is_not_defaulted_and_invented_slot_is_removed():
    query = 'Please bring travel sewing kits to room 502'
    accepted = validate_commands((Command('StartGoal', goal='amenity_delivery',
        slots=(CommandSlot('requested_item','travel sewing kits'), CommandSlot('room_number','999'))),),
        query=query, language='en', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert accepted and accepted[0].slots == (CommandSlot('requested_item','travel sewing kits'),)


def test_correct_intent_missing_item_is_not_unsupported_goal():
    accepted = validate_commands((Command('StartGoal', goal='amenity_delivery'),),
        query='Please bring some supplies to room 502', language='en', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert accepted and not accepted[0].slots
    from concierge_kiosk.agent.tools.service_slots import assess_service
    assert 'requested_item' in assess_service('Please bring some supplies to room 502', 'en',
        'facilities', mode='amenity_delivery').missing


def test_grounded_reference_requires_server_topic_not_model_flag():
    command = Command('StartGoal', goal='spa_reservation', refers_to_context=True)
    kwargs = dict(query='Please book it', language='en', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert validate_commands((command,), **kwargs) is None
    assert validate_commands((command,), context_topic='V-Senses Spa', **kwargs)
    assert validate_commands((command,), context_topic='Swimming pool', **kwargs) is None


def test_owned_pending_goal_continuation_is_bounded_to_current_field():
    command = Command('StartGoal', goal='wake_up_call', slots=(CommandSlot('preferred_time','06:00'),))
    kwargs = dict(query='Make it 06:00', language='en', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert validate_commands((command,), **kwargs) is None
    assert validate_commands((command,), pending_goal='wake_up_call', pending_reply='preferred_time', **kwargs)
    assert validate_commands((command,), pending_goal='housekeeping', pending_reply='preferred_time', **kwargs) is None


def test_integer_preference_needs_field_meaning_not_just_number():
    assert validate_commands((Command('SetPreference', field='children', value='3', evidence=WP12_QUERY),),
        query=WP12_QUERY, language='vi') is None
    assert validate_commands((Command('SetPreference', field='children', value='3', evidence='3 children'),),
        query='We have 3 children', language='en')


@pytest.mark.parametrize('legacy_projection', [False, True])
def test_direct_governed_runtime_cannot_bypass_semantic_gate(legacy_projection):
    from concierge_kiosk.agent.core.concierge import AgentToolRequest, BoundedToolRegistry, BOUNDED_TOOLS
    from concierge_kiosk.agent.runtime.runtime import AutonomousConciergeRuntime
    from concierge_kiosk.agent.understanding.routing import RouteDecision
    commands = tuple(Command(**item) for item in json.loads(WP12_RAW)['commands'])
    runtime = AutonomousConciergeRuntime(BoundedToolRegistry({
        name: lambda _: pytest.fail('No tool after rejected intent') for name in BOUNDED_TOOLS}))
    request = AgentToolRequest(query=WP12_QUERY, language='vi', session='owned', effective_date='2026-10-09',
                               decision=RouteDecision('service', True, semantic_service_code='housekeeping'))
    run = runtime.run(request, commands=None if legacy_projection else commands,
                      planner=lambda _: pytest.fail('No planner after rejected intent'))
    assert run.state.termination_reason == 'unsupported_semantics'
    assert not run.state.service_candidates and not run.observations
    assert run.business_write_count() == 0


def test_replayed_model_transport_cannot_create_wrong_review_or_clear_pending(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from concierge_kiosk.agent.understanding import commands
    from concierge_kiosk.runtime import local_http
    from concierge_kiosk.runtime import local_ai
    from concierge_kiosk.voice.runtime import adapters
    from test_understanding_layers import _client
    monkeypatch.setattr(local_http._OPENER, 'open', lambda *a, **kw: pytest.fail('WP13 forbids model HTTP'))
    monkeypatch.setattr(local_ai, 'warm_local_slm', lambda *a, **kw: False)
    monkeypatch.setattr(adapters, 'warm_voice_models', lambda *a, **kw: [])
    captured = []
    monkeypatch.setattr(commands, '_chat', lambda b,p,t,c: captured.append(p) or WP12_RAW)
    app = _client(tmp_path, llm_base_url='http://127.0.0.1:1', llm_model='mock', llm_fallback_model='')
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        proposal = app.state.workflows.prepare(session['session_id'], 'facilities', 'en',
            'Bring water to room 502', 'semantic-existing-123',
            {'requested_item':'water','quantity':3,'unit':'bottles','room_number':'502'},
            service_code='amenity_delivery')
        app.state.conversations.remember_expected_reply(session['session_id'], 'vi', 'confirm')
        monkeypatch.setattr(app.state.conversation_engine.concierge_agent, 'run',
            lambda **kw: pytest.fail('No governed tools after unsupported semantic commands'))
        reply = client.post('/api/ask', headers={'X-CSRF-Token':session['csrf_token']},
            json={'query':WP12_QUERY,'language':'vi','turn_nonce':'semantic-replay-123'})
        assert reply.status_code == 200, reply.text
        body = reply.json()
        assert body['failure_class'] == 'AMBIGUOUS_INTENT'
        assert body['understanding_outcome'] == 'unsupported_semantics'
        assert not body.get('suggested_action') and not body.get('tool_calls') and not body.get('citations')
        assert captured and body['understanding_commands'] == []
        with app.state.store.connection() as con:
            assert con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] == 0
            row = con.execute('SELECT status,payload_json FROM proposals WHERE id=?',(proposal['id'],)).fetchone()
            assert row[0] == 'awaiting_confirmation' and json.loads(row[1])['requested_item'] == 'water'
        assert app.state.conversations.workflow_projection(session['session_id'],'vi')['expected_reply'] == 'confirm'
        assert app.state.preference_memory.load(session['session_id']) == {}
        import os
        from pathlib import Path
        if trace_path := os.environ.get('WP13_TRACE_PATH'):
            rejected = []
            parse_commands(WP12_RAW, query=WP12_QUERY, language='vi',
                           enabled_request_kinds=ACTION_REQUEST_KINDS, rejections=rejected)
            Path(trace_path).write_text(json.dumps({
                'session': 'anonymized-replay-session', 'guest_input': WP12_QUERY,
                'raw_model_response': WP12_RAW, 'semantic_rejections': rejected,
                'api': {key: body.get(key) for key in ('answer', 'tool_route', 'failure_class',
                    'understanding_outcome', 'understanding_commands', 'tool_calls', 'citations', 'suggested_action')},
                'business_writes': 0, 'existing_proposal_status': row[0],
                'existing_proposal_payload': json.loads(row[1]), 'qwen_http_calls': 0,
                'verification': 'DETERMINISTIC_REPLAY_NOT_REAL_MODEL'},
                ensure_ascii=False, indent=2), encoding='utf-8')
