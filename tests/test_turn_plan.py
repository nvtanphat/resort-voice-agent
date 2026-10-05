from __future__ import annotations

import json

from concierge_kiosk.agent.runtime.state import build_initial_state
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.agent.understanding.turn_plan import (
    TurnPlan, model_turn_plan, parse_turn_plan, turn_plan_schema,
)


def _intent(kind: str, *, mode=None, slots=None, question=None, refers_to=None):
    return {
        'type': kind, 'service_mode': mode, 'slots': slots or [],
        'question': question, 'refers_to': refers_to,
    }


def test_turn_plan_schema_is_closed_and_slot_text_is_bound_to_utterance():
    query = 'Please bring 2 towels to room 305'
    raw = json.dumps({'intents': [_intent(
        'write', mode='amenity_delivery',
        slots=[{'name': 'quantity', 'text': '2'}, {'name': 'room_number', 'text': '305'}],
    )]})
    plan = parse_turn_plan(raw, query=query, language='en',
                           enabled_request_kinds=frozenset({'facilities'}))
    assert isinstance(plan, TurnPlan)
    assert plan.writes[0].slots[0].text == '2'
    assert turn_plan_schema()['additionalProperties'] is False
    assert parse_turn_plan(
        raw.replace('"305"', '"999"'), query=query, language='en',
        enabled_request_kinds=frozenset({'facilities'})) is None


def test_turn_plan_validates_mixed_write_and_read_without_granting_authority():
    query = 'Send two towels to room 305 and what time does the pool close?'
    raw = json.dumps({'intents': [
            _intent('write', mode='amenity_delivery', slots=[
            {'name': 'quantity', 'text': 'two'}, {'name': 'room_number', 'text': '305'}]),
        _intent('read', question='what time does the pool close?'),
    ]})
    plan = parse_turn_plan(raw, query=query, language='en',
                           enabled_request_kinds=frozenset({'facilities'}))
    assert plan is not None
    assert len(plan.writes) == 1
    assert plan.writes[0].service_mode == 'amenity_delivery'

    state = build_initial_state(
        query=query, language='en', decision=RouteDecision('multi_task'), turn_plan=plan)
    assert any(req.outcome == 'service:amenity_delivery' for req in state.goal_contract.requirements)
    assert any(req.outcome == 'verified_answer' for req in state.goal_contract.requirements)


def test_turn_plan_pending_reply_requires_exact_server_owned_context():
    query = 'yes'
    valid = json.dumps({'intents': [_intent(
        'answer_to_pending', refers_to='confirm') ]})
    assert parse_turn_plan(valid, query=query, language='en',
                           enabled_request_kinds=frozenset(), pending_reply='confirm') is not None
    assert parse_turn_plan(valid, query=query, language='en',
                           enabled_request_kinds=frozenset(), pending_reply='room_number') is None


def test_model_turn_plan_uses_structured_schema_and_server_parser(monkeypatch):
    seen = {}

    def fake_chat(_base_url, payload, _timeout, _cancel):
        seen['format'] = payload['format']
        return json.dumps({'intents': [_intent('read', question='Where is the spa?')]})

    monkeypatch.setattr('concierge_kiosk.agent.understanding.turn_plan._chat', fake_chat)
    plan = model_turn_plan(
        query='Where is the spa?', language='en', base_url='http://local', model='small',
        enabled_request_kinds=frozenset({'facilities'}))
    assert plan is not None and plan.intents[0].type == 'read'
    assert isinstance(seen['format'], dict)
    assert seen['format']['additionalProperties'] is False


def test_model_turn_plan_receives_bounded_t2_candidates_and_icl_examples(monkeypatch):
    seen = {}

    def fake_chat(_base_url, payload, _timeout, _cancel):
        seen['user'] = json.loads(payload['messages'][1]['content'])
        return json.dumps({'intents': [_intent('read', question='Where is the spa?')]})

    monkeypatch.setattr('concierge_kiosk.agent.understanding.turn_plan._chat', fake_chat)
    model_turn_plan(
        query='Where is the spa?', language='en', base_url='http://local', model='small',
        enabled_request_kinds=frozenset({'facilities'}),
        candidate_labels=(('info:location', 0.41), ('navigation', 0.39),
                          ('out_of_scope', 0.08), ('extra', 0.01)),
        nearest_examples=tuple({'example_id': str(i), 'text': 'spa', 'route': 'knowledge',
                                'score': '0.9'} for i in range(7)),
    )
    assert len(seen['user']['t2_candidates']) == 3
    assert len(seen['user']['nearest_examples']) == 5
