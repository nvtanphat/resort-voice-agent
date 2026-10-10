"""Router invariants, separate from the locked real-model evaluation inputs."""
import json
import threading
import time
import unicodedata

import pytest

from concierge_kiosk.agent.understanding.commands import Command, parse_commands
from concierge_kiosk.agent.understanding.intent_evidence import (
    bind_turn_service_ranking, fold, request_clauses, spans,
)
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS
from concierge_kiosk.runtime.admission import AudioAdmission


@pytest.fixture(autouse=True)
def ranking_scope():
    bind_turn_service_ranking(())
    yield
    bind_turn_service_ranking(())


@pytest.mark.parametrize('surface,wrong', [('bàn', 'bán'), ('đá', 'da'), ('cấm', 'cám')])
def test_default_evidence_match_preserves_typed_marks(surface, wrong):
    view = fold(surface)
    assert spans(view, (surface,))
    assert not spans(view, (wrong,))
    assert spans(fold(unicodedata.normalize('NFD', surface)), (surface,))


def test_unmarked_evidence_retains_the_explicit_compatibility_policy():
    assert spans(fold('ban'), ('bàn',))
    assert spans(fold('ban'), ('bán',))


@pytest.mark.parametrize('query', [
    'Bring four washcloths, two shaving kits and three shower caps.',
    'Bring four washcloths, three table coasters and two shower caps.',
    'What are the gym opening hours and closing hours?',
    'Please reserve a restaurant table for three, at 17:50.',
])
def test_complements_and_enumerations_are_one_predicate(query):
    assert len(request_clauses(query, 'en')) == 1


def test_two_independent_predicates_are_distinct_scopes():
    query = 'Please reserve a restaurant table at 17:50 and arrange a taxi at 20:40.'
    parts = request_clauses(query, 'en')
    assert len(parts) == 2
    assert '17:50' in parts[0][1] and '20:40' not in parts[0][1]
    assert '20:40' in parts[1][1] and '17:50' not in parts[1][1]


def test_an_execution_and_a_factual_question_keep_their_own_modality():
    parts = request_clauses('Bring four washcloths and where is the gym?', 'en')
    assert len(parts) == 2
    assert 'where' not in parts[0][1]
    assert len(request_clauses('Where is the gym and what time does it open?', 'en')) == 1


def test_flat_model_contract_has_no_slot_generation():
    query = 'Please bring four washcloths.'
    raw = json.dumps({'commands': [{'type': 'StartGoal', 'goal': 'amenity_delivery', 'text': query}]})
    parsed = parse_commands(raw, query=query, language='en', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert parsed and parsed[0].source == query
    assert any(slot.name == 'requested_item' and slot.text == 'washcloths' for slot in parsed[0].slots)


def test_quantity_grammar_accepts_configured_particles_after_measure_words(monkeypatch):
    from concierge_kiosk.agent.tools import service_slots
    monkeypatch.setattr(service_slots, '_QUANTITY_ITEM', {'locale': {
        'order': 'before', 'spaced': True, 'particles': ['suffix'], 'max_words': 1}})
    monkeypatch.setattr(service_slots, '_QUANTITY_UNITS', {'locale': ['measure']})
    monkeypatch.setattr(service_slots, '_NUMBER_WORDS', {'locale': {}})
    monkeypatch.setattr(service_slots, '_ROOM_PATTERNS', {})
    assert service_slots.item_and_unit('object 4 measuresuffix', 'locale') == ('object', 'measure')


def test_a_model_span_cannot_cut_off_its_governing_negation():
    query = 'Please do not reserve a restaurant table at 17:50.'
    bind_turn_service_ranking([('dining_reservation', .95)])
    raw = json.dumps({'commands': [{'type': 'StartGoal', 'goal': 'dining_reservation',
                                   'text': 'reserve a restaurant table at 17:50'}]})
    assert parse_commands(raw, query=query, language='en', enabled_request_kinds=ACTION_REQUEST_KINDS) is None


def test_a_model_span_cannot_turn_a_quotation_into_a_request():
    query = 'Someone wrote "bring four washcloths".'
    raw = json.dumps({'commands': [{'type': 'StartGoal', 'goal': 'amenity_delivery',
                                   'text': 'bring four washcloths'}]})
    assert parse_commands(raw, query=query, language='en', enabled_request_kinds=ACTION_REQUEST_KINDS) is None


def test_a_nonverbatim_model_source_is_rejected():
    raw = json.dumps({'commands': [{'type': 'StartGoal', 'goal': 'housekeeping',
                                   'text': 'Please clean this room'}]})
    assert parse_commands(raw, query='Thanks for your help.', language='en',
                          enabled_request_kinds=ACTION_REQUEST_KINDS) is None


def test_model_sources_keep_independent_times_in_the_governed_state():
    from concierge_kiosk.agent.runtime.state import build_initial_state
    from concierge_kiosk.agent.understanding.routing import RouteDecision
    left = 'Please reserve a restaurant table for three at 17:50'
    right = 'arrange a taxi at 20:40'
    query = left + ' and ' + right + '.'
    raw = json.dumps({'commands': [
        {'type': 'StartGoal', 'goal': 'dining_reservation', 'text': left},
        {'type': 'StartGoal', 'goal': 'transport_request', 'text': right},
    ]})
    parsed = parse_commands(raw, query=query, language='en', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert parsed and len(parsed) == 2
    state = build_initial_state(query=query, language='en', decision=RouteDecision('multi_task', True), commands=parsed)
    assert [c.existing_slots['preferred_time'] for c in state.service_candidates] == ['17:50', '20:40']


def test_composed_few_shot_is_not_limited_by_the_advisory_shortlist():
    from concierge_kiosk.agent.understanding.service_selector import CommandExample, ServiceSelector
    from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path
    class Embedder:
        def encode_query(self, text):
            return [1.0, .1]
        encode_passage = encode_query
    single = CommandExample('en', 'single training request',
                            ({'type': 'StartGoal', 'goal': 'housekeeping'},), 'housekeeping')
    multi = CommandExample('en', 'composed training request', (
        {'type': 'StartGoal', 'goal': 'transport_request'},
        {'type': 'StartGoal', 'goal': 'wake_up_call'},
    ), 'transport_request')
    selector = ServiceSelector(dataset_path(SERVICE_CATALOG), Embedder(), examples=(single, multi), top_k=1, example_k=2)
    candidates, shots = selector.understand('guest turn', language='en', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert len(candidates) == 1
    assert any(len(s['commands']) == 2 for s in shots)


def test_composed_training_ranks_each_owned_source_instead_of_its_first_goal(tmp_path):
    from concierge_kiosk.agent.understanding.service_selector import CommandExample, ServiceSelector
    from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path
    class Embedder:
        def encode_query(self, text):
            return [1., 0.] if text == 'owned first source' else [0., 1.] if text == 'owned second source' else [1., 1.]
        encode_passage = encode_query
    multi = CommandExample('en', 'composed training request', (
        {'type': 'StartGoal', 'goal': 'transport_request', 'source': 'owned first source'},
        {'type': 'StartGoal', 'goal': 'wake_up_call', 'source': 'owned second source'},
    ), 'transport_request')
    selector = ServiceSelector(dataset_path(SERVICE_CATALOG), Embedder(), examples=(multi,), cache_dir=tmp_path)
    selector.warm()
    first = selector.goal_ranking('owned first source', enabled_request_kinds=ACTION_REQUEST_KINDS)
    second = selector.goal_ranking('owned second source', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert first[0] == ('transport_request', 1.)
    assert second[0] == ('wake_up_call', 1.)
    cached = ServiceSelector(dataset_path(SERVICE_CATALOG), Embedder(), examples=(multi,), cache_dir=tmp_path)
    cached.warm()
    assert cached.goal_ranking('owned second source', enabled_request_kinds=ACTION_REQUEST_KINDS) == second


def test_model_few_shots_use_the_flat_wire_and_runtime_uses_no_clause_hints(monkeypatch):
    from concierge_kiosk.agent.understanding import commands
    captured = []
    monkeypatch.setattr(commands, '_chat', lambda b, p, t, c: captured.append(p) or None)
    commands.model_commands(query='unseen guest turn', language='en', base_url='http://127.0.0.1:11434',
        model='model', enabled_request_kinds=ACTION_REQUEST_KINDS,
        examples=[{'guest_turn': 'training request', 'commands': [
            {'type': 'StartGoal', 'goal': 'amenity_delivery', 'slots': [
                {'name': 'room_number', 'text': '412'}, {'name': 'quantity', 'text': '3'},
                {'name': 'requested_item', 'text': 'writing pads'}, {'name': 'unit', 'text': 'pads'},
            ]}]}])
    shown = json.loads(next(m['content'] for m in captured[0]['messages'] if m['role'] == 'assistant'))
    assert 'slots' not in shown['commands'][0]
    assert set(shown['commands'][0]) == {'type', 'goal', 'text', 'item'}
    assert 'request_parts' not in json.loads(captured[0]['messages'][-1]['content'])


def test_guest_waits_for_startup_without_competing_with_native_work():
    admission = AudioAdmission()
    assert admission.try_enter_slm()
    entered = threading.Event()
    result = []
    def guest():
        result.append(admission.enter_guest_slm('guest', timeout=.5))
        entered.set()
    worker = threading.Thread(target=guest)
    worker.start()
    try:
        assert not entered.wait(.03)
        admission.leave_slm()
        assert entered.wait(.5)
        assert result == [True]
        assert not admission.try_enter_slm('other')
        admission.leave_slm()
    finally:
        worker.join(1)


def test_startup_wait_has_a_deadline_and_other_guests_do_not_queue():
    admission = AudioAdmission()
    assert admission.try_enter_slm()
    started = time.monotonic()
    assert not admission.enter_guest_slm('guest', timeout=.02)
    assert time.monotonic() - started < .3
    admission.leave_slm()
    assert admission.try_enter_slm('first')
    assert not admission.enter_guest_slm('second', timeout=.5)
    admission.leave_slm()
