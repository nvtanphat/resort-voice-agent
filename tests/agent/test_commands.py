from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from concierge_kiosk.agent.understanding.commands import (
    Command, command_schema,
    model_commands, parse_commands, validate_commands,
)
from concierge_kiosk.agent.understanding.service_selector import (
    CommandExample, ServiceSelector, load_command_examples, normalized_situation_group,
)
from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path


def test_command_schema_is_closed():
    schema = command_schema({'amenity_delivery': ['room_number', 'quantity']})
    assert schema['additionalProperties'] is False
    assert schema['properties']['commands']['maxItems'] >= 1


def test_situation_group_normalizes_legacy_frame_and_concept_keys():
    assert normalized_situation_group('FRAME-COLLAPSED', None) == 'collapsed'
    assert normalized_situation_group(None, 'collapsed') == 'collapsed'


def test_model_command_cannot_invent_a_slot_value():
    query = 'Please bring 2 towels to room 305'
    raw = json.dumps({'commands': [{
        'type': 'StartGoal', 'goal': 'amenity_delivery',
        'slots': [{'name': 'quantity', 'text': '2'}, {'name': 'room_number', 'text': '999'}],
    }]})
    commands = parse_commands(raw, query=query,
                              enabled_request_kinds=frozenset({'facilities'}))
    # The invented room is dropped, never trusted; the goal survives so the
    # runtime asks for the room instead of discarding the whole intent.
    assert commands is not None and commands[0].goal == 'amenity_delivery'
    assert [slot.public() for slot in commands[0].slots] == [{'name': 'quantity', 'text': '2'}]


def test_commands_keep_confirmation_and_cancel_non_authoritative():
    assert validate_commands(
        [Command('Confirm', confirmed=True)], query='yes', pending_reply='confirm')
    assert validate_commands([Command('Cancel')], query='cancel')
    assert validate_commands([Command('Confirm', confirmed=True)], query='yes',
                             pending_reply='room_number') is None
    assert validate_commands([Command('Modify')], query='change it to six')


def test_command_schema_exposes_explicit_request_change_verbs():
    variants = command_schema({'amenity_delivery': ['room_number']})[
        'properties']['commands']['items']['anyOf']
    types = {item['properties']['type']['const'] for item in variants}
    assert {'Cancel', 'Modify'} <= types


def test_conditional_start_goal_is_closed_and_preserved():
    raw = json.dumps({'commands': [{
        'type': 'StartGoal', 'goal': 'dining_reservation', 'slots': [],
        'conditional': True,
    }]})
    commands = parse_commands(
        raw, query='if available, book a table',
        enabled_request_kinds=frozenset({'dining'}))
    assert commands and commands[0].conditional is True
    start = next(item for item in command_schema({'dining_reservation': []})
                 ['properties']['commands']['items']['anyOf']
                 if item['properties']['type']['const'] == 'StartGoal')
    assert start['properties']['conditional']['type'] == 'boolean'


class _ServiceEmbedder:
    def encode_query(self, text):
        return [1.0, 0.0] if 'towel' in text.casefold() else [0.0, 1.0]

    def encode_passage(self, text):
        return [1.0, 0.0] if 'towel' in text.casefold() else [0.0, 1.0]


def test_service_selector_returns_catalog_candidates_and_registry_slots():
    selector = ServiceSelector(
        dataset_path(SERVICE_CATALOG), _ServiceEmbedder(), top_k=3)
    candidates = selector.select(
        'Please bring fresh bath towels', language='en',
        enabled_request_kinds=frozenset({'facilities'}))
    towels = next(item for item in candidates if item['service_mode'] == 'amenity_delivery')
    assert towels['catalog_service_id'] == 'service.bath_towels'
    assert towels['accepted_slots'] == ['room_number', 'quantity']
    assert len(candidates) <= 3


def test_service_selector_does_not_bypass_embedding_margin_for_catalog_alias():
    selector = ServiceSelector(dataset_path(SERVICE_CATALOG), _ServiceEmbedder(), top_k=3)
    assert selector.fallback_commands(
        "xin gia hạn thời gian lưu trú", language="vi",
        enabled_request_kinds=frozenset({"front_office"}),
        min_score=0.99, min_margin=0.99) is None


def test_model_command_prompt_rechecks_selector_candidates_against_registry(monkeypatch):
    captured = {}

    def fake_chat(_base_url, payload, _timeout, _cancel):
        captured.update(payload)
        return ('{"commands":[{"type":"StartGoal","goal":"amenity_delivery",'
                '"slots":[{"name":"quantity","text":"2"}]}]}')

    monkeypatch.setattr('concierge_kiosk.agent.understanding.commands._chat', fake_chat)
    result = model_commands(
        query='Please bring 2 towels', language='en',
        base_url='http://127.0.0.1:11434', model='local-model',
        enabled_request_kinds=frozenset({'facilities'}),
        service_candidates=(
            {'service_mode': 'not_a_registry_service', 'accepted_slots': ['evil']},
            {'service_mode': 'amenity_delivery', 'accepted_slots': ['evil'],
             'name': 'Fresh Bath Towels'},
        ),
    )
    services = json.loads(captured['messages'][1]['content'])['available_services']
    assert result and result[0].goal == 'amenity_delivery'
    assert all(item['service_mode'] != 'not_a_registry_service' for item in services)
    assert services[0]['accepted_slots'] == ['room_number', 'quantity']


def test_command_schema_closes_goals_and_slots_per_candidate():
    schema = command_schema({'amenity_delivery': ['room_number', 'quantity'],
                             'late_checkout': ['room_number']}, slot_reply=False)
    variants = schema['properties']['commands']['items']['anyOf']
    goals = {item['properties']['goal']['const']: item for item in variants
             if item['properties']['type']['const'] == 'StartGoal'}
    assert set(goals) == {'amenity_delivery', 'late_checkout'}
    slot_names = goals['late_checkout']['properties']['slots']['items']['properties']['name']
    assert slot_names['enum'] == ['room_number']
    types = {item['properties']['type']['const'] for item in variants}
    # Without a pending server question there is nothing a SetSlot could answer.
    assert not types & {'SetSlot', 'CorrectSlot'}
    assert all(item['additionalProperties'] is False for item in variants)
    reply_types = {item['properties']['type']['const'] for item in
                   command_schema({'late_checkout': ['room_number']})['properties']
                   ['commands']['items']['anyOf']}
    assert {'SetSlot', 'CorrectSlot'} <= reply_types


def test_unstated_slot_reply_is_skipped_and_empty_stream_fails_closed():
    query = 'my room is 305'
    kept = validate_commands([Command('SetSlot', field='room_number', value='999'),
                              Command('SetSlot', field='room_number', value='305')],
                             query=query, pending_reply='room_number')
    assert kept is not None and [command.value for command in kept] == ['305']
    assert validate_commands([Command('SetSlot', field='room_number', value='999')],
                             query=query, pending_reply='room_number') is None


def test_model_commands_sends_examples_only_for_offered_goals(monkeypatch):
    captured = {}

    def fake_chat(_base_url, payload, _timeout, _cancel):
        captured.update(payload)
        return '{"commands":[{"type":"StartGoal","goal":"amenity_delivery","slots":[]}]}'

    monkeypatch.setattr('concierge_kiosk.agent.understanding.commands._chat', fake_chat)
    model_commands(
        query='more towels please', language='en', base_url='http://127.0.0.1:11434',
        model='local-model', enabled_request_kinds=frozenset({'facilities', 'front_office'}),
        service_candidates=({'service_mode': 'amenity_delivery'},),
        examples=(
            {'guest_turn': 'two towels', 'commands': [
                {'type': 'StartGoal', 'goal': 'amenity_delivery', 'slots': []}]},
            {'guest_turn': 'late checkout', 'commands': [
                {'type': 'StartGoal', 'goal': 'late_checkout', 'slots': []}]},
            {'guest_turn': 'pool hours?', 'commands': [{'type': 'AskInfo', 'query': 'pool hours?'}]},
        ))
    content = json.loads(captured['messages'][1]['content'])
    assert [item['guest_turn'] for item in content['examples']] == ['two towels', 'pool hours?']
    variants = captured['format']['properties']['commands']['items']['anyOf']
    assert [item['properties']['goal']['const'] for item in variants
            if item['properties']['type']['const'] == 'StartGoal'] == ['amenity_delivery']


class _AxisEmbedder:
    """Maps text to fixed axes so similarity is deterministic in tests."""

    def __init__(self, axes):
        self.axes = axes

    def encode_query(self, text):
        lowered = text.casefold()
        return [1.0 if marker in lowered else 0.0 for marker in self.axes] + [0.01]

    encode_passage = encode_query


def test_reviewed_examples_rank_services_and_become_few_shots():
    examples = (
        CommandExample('en', 'the shower is leaking', (
            {'type': 'StartGoal', 'goal': 'maintenance', 'slots': []},), 'maintenance'),
        CommandExample('en', 'is the pool open', (
            {'type': 'AskInfo', 'query': 'is the pool open'},), None),
    )
    selector = ServiceSelector(dataset_path(SERVICE_CATALOG),
                               _AxisEmbedder(('leak', 'pool')), top_k=3,
                               examples=examples, example_k=2)
    candidates, shots = selector.understand(
        'water is leaking from the ceiling', language='en',
        enabled_request_kinds=frozenset({'facilities', 'housekeeping'}))
    # Maintenance has no catalog row; its reviewed example ranks it first.
    assert candidates[0]['service_mode'] == 'maintenance'
    assert shots[0]['guest_turn'] == 'the shower is leaking'


def test_load_command_examples_keeps_train_rows_and_literal_slots(tmp_path):
    path = tmp_path / 'train.jsonl'
    rows = [
        {'split': 'train', 'language': 'en', 'utterance': 'two towels to room 305',
         'expected_route': 'service', 'service_code': 'amenity_delivery',
         'expected_slots': {'room_number': '305', 'quantity': 2}},
        {'split': 'test', 'language': 'en', 'utterance': 'pool hours',
         'expected_route': 'knowledge'},
        {'split': 'train', 'language': 'en', 'utterance': 'pool hours',
         'expected_route': 'knowledge'},
    ]
    path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    examples = load_command_examples([path])
    assert [example.utterance for example in examples] == ['two towels to room 305', 'pool hours']
    # quantity 2 is not literally in the utterance ("two"), so it is not shown.
    assert examples[0].commands[0]['slots'] == [{'name': 'room_number', 'text': '305'}]
    assert examples[1].commands[0] == {'type': 'AskInfo', 'query': 'pool hours'}


def test_load_command_examples_keeps_reviewed_multi_step_commands(tmp_path):
    path = tmp_path / 'multi.jsonl'
    path.write_text(json.dumps({
        'split': 'train', 'language': 'en',
        'utterance': 'send towels to room 305 and book a taxi at 6am',
        'expected_route': 'multi_step', 'frame_id': 'FRAME-MULTI',
        'commands': [
            {'type': 'StartGoal', 'goal': 'amenity_delivery',
             'slots': [{'name': 'room_number', 'text': '305'}]},
            {'type': 'StartGoal', 'goal': 'transport_request',
             'slots': [{'name': 'preferred_time', 'text': '6am'}]},
        ],
    }), encoding='utf-8')
    examples = load_command_examples([path])
    assert len(examples) == 1
    assert [item['goal'] for item in examples[0].commands] == [
        'amenity_delivery', 'transport_request']


def test_availability_mode_uses_only_the_best_semantic_match():
    selector = ServiceSelector(dataset_path(SERVICE_CATALOG), _ServiceEmbedder(), top_k=8)
    # The best match (towels) has no availability source; a lower-ranked
    # bookable service must not be picked just because it has one.
    assert selector.select_availability_mode(
        'Please bring fresh bath towels', language='en',
        enabled_request_kinds=frozenset({'facilities', 'dining'})) is None
