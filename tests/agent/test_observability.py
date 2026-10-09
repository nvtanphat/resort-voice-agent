"""WP14 deterministic boundaries. No live model or Cloud requests are permitted."""
import asyncio
import builtins
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from queue import Full
import secrets
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.runtime.observability import (
    Observability, SDKBackend, current_scope, event, observation, sanitize,
)
from concierge_kiosk.runtime.evaluation_observability import export_existing_scores
from concierge_kiosk.agent.understanding.commands import Command, model_commands, parse_commands
from concierge_kiosk.agent.understanding import semantic
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS


class FakeBackend:
    def __init__(self, failure=None):
        self.spans = []
        self.scores = []
        self.lock = Lock()
        self.failure = failure
        self.stopped = False

    def start(self, name, trace_id, parent_id, metadata):
        handle = SimpleNamespace(id=secrets.token_hex(8), trace_id=trace_id or secrets.token_hex(16),
                                 name=name, parent_id=parent_id, metadata=dict(metadata))
        with self.lock:
            self.spans.append(handle)
        return handle

    def finish(self, handle, metadata):
        if self.failure:
            raise self.failure
        handle.metadata = dict(metadata)

    def score(self, **score):
        if self.failure:
            raise self.failure
        self.scores.append(score)

    def shutdown(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def deny_real_network(monkeypatch):
    from concierge_kiosk.runtime import local_http
    def deny(*args, **kwargs):
        pytest.fail('WP14 forbids real HTTP/Qwen/Cloud calls')
    monkeypatch.setattr(local_http._OPENER, 'open', deny)
    import requests
    import httpx
    monkeypatch.setattr(requests.Session, 'request', deny)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', deny)


@pytest.fixture
def telemetry():
    backend = FakeBackend()
    return Observability(backend, model_names=('synthetic-qwen',), pseudonym_key=b'x' * 32), backend


def matching(backend, name):
    return [s for s in backend.spans if s.name == name]


def test_disabled_and_zero_sampling_never_import_sdk_or_create_exporter(monkeypatch):
    original = builtins.__import__
    def no_sdk(name, *args, **kwargs):
        assert not name.startswith('langfuse')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_sdk)
    def never(_cfg):
        pytest.fail('Exporter constructed in disabled mode')
    for cfg in (Settings(environment='test'), Settings(environment='test', langfuse_enabled=True, langfuse_sample_rate=0)):
        owner = Observability.configured(cfg, backend_factory=never)
        with owner.turn('private-session'):
            event('understanding', status='success')
            assert current_scope() is None
        assert owner.backend is None
        assert owner.shutdown()


@pytest.mark.parametrize('settings', [
    {'langfuse_enabled': True},
    {'langfuse_enabled': 'wrong'},
    {'langfuse_enabled': True, 'langfuse_sample_rate': 'wrong'},
    {'langfuse_enabled': True, 'langfuse_sample_rate': float('nan')},
    {'langfuse_enabled': True, 'langfuse_base_url': 'https://secret@invalid.test'},
])
def test_invalid_optional_configuration_fails_closed(settings):
    owner = Observability.configured(Settings(environment='test', **settings),
                                     backend_factory=lambda _: pytest.fail('invalid config enabled'))
    assert owner.backend is None


def test_bad_credentials_constructor_failure_is_optional():
    cfg = Settings(environment='test', langfuse_enabled=True, langfuse_public_key='synthetic-public',
                   langfuse_secret_key='synthetic-secret', langfuse_base_url='https://invalid.test')
    def bad(_):
        raise PermissionError('synthetic credential rejection')
    assert Observability.configured(cfg, backend_factory=bad).backend is None
    assert 'synthetic-secret' not in repr(cfg)


def test_root_parent_and_nested_same_session_turn(telemetry):
    owner, backend = telemetry
    with owner.turn('guest-123') as scope:
        assert len(scope.trace_id) == 32
        with observation('understanding'):
            with owner.turn('guest-123'):
                event('command_validation', status='accepted')
    assert len(matching(backend, 'guest_turn')) == 1
    root, understanding, validation = backend.spans
    assert root.parent_id is None
    assert understanding.parent_id == root.id
    assert validation.parent_id == understanding.id
    assert {s.trace_id for s in backend.spans} == {root.trace_id}
    assert 'guest-123' not in str([s.metadata for s in backend.spans])
    assert current_scope() is None


class MockStream:
    status = 200
    headers = {}
    def __init__(self, content):
        self.content = content
        self.closed = False
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.closed = True
    def __iter__(self):
        yield json.dumps({'message': {'content': self.content}, 'done': False}).encode()
        yield json.dumps({'done': True, 'message': {'content': ''},
                          'prompt_eval_count': 101, 'eval_count': 23,
                          'prompt_eval_duration': 2000000, 'eval_duration': 4000000,
                          'total_duration': 7000000}).encode()


def nlu(raw, monkeypatch, owner):
    calls = []
    stream = MockStream(raw)
    def transport(request, *, timeout):
        calls.append(timeout)
        return stream
    # Real local_chat_open (loopback/budget/circuit checks) with only HTTP mocked.
    from concierge_kiosk.runtime import local_http
    monkeypatch.setattr(local_http._OPENER, 'open', transport)
    with owner.turn('nlu-session'):
        result = model_commands(query='Please bring water', language='en', base_url='http://localhost:11434',
                                model='synthetic-qwen', enabled_request_kinds=ACTION_REQUEST_KINDS,
                                timeout_seconds=.7)
    assert calls == [.7]
    assert stream.closed
    return result


def test_qwen_stream_metadata_and_semantic_authorized_without_retry(monkeypatch, telemetry):
    owner, backend = telemetry
    result = nlu('{"commands":[{"type":"StartGoal","goal":"amenity_delivery","slots":[]}]}', monkeypatch, owner)
    assert result[0].goal == 'amenity_delivery'
    generation, = matching(backend, 'model_call')
    assert generation.metadata['invocation_type'] == 'NLU'
    assert generation.metadata['schema_version'] == 'WP11_compact'
    assert generation.metadata['model_name'] == 'synthetic-qwen'
    assert generation.metadata['prompt_tokens'] == 101
    assert generation.metadata['completion_tokens'] == 23
    assert generation.metadata['prompt_eval_ms'] == 2
    assert generation.metadata['generation_ms'] == 4
    assert generation.metadata['total_model_ms'] == 7
    assert generation.metadata['status'] == 'success'
    for stage in ('model_proposed', 'server_validated', 'semantically_authorized'):
        span, = matching(backend, stage)
        assert span.metadata['outcome'] == 'accepted'


def test_recorded_wp12_semantic_rejection_has_no_live_ground_truth(telemetry):
    from test_semantic_authorization import WP12_RAW, WP12_QUERY
    owner, backend = telemetry
    with owner.turn('replay'):
        assert parse_commands(WP12_RAW, query=WP12_QUERY, language='vi', enabled_request_kinds=ACTION_REQUEST_KINDS) is None
    rejected = matching(backend, 'semantically_authorized')
    assert len(rejected) == 2
    assert {s.metadata['reason_code'] for s in rejected} == {'unsupported_semantics'}
    assert all(s.metadata['proposal_allowed'] is False for s in rejected)
    assert all(s.metadata['semantic_policy'] == 'WP13' for s in rejected)
    assert 'expected_goal' not in str([s.metadata for s in backend.spans])
    assert not matching(backend, 'tool_execution')


def test_model_timeout_and_cancellation_keep_deadline_and_no_retry(monkeypatch, telemetry):
    owner, backend = telemetry
    calls = []
    def timeout(request, *, timeout):
        calls.append(timeout)
        raise TimeoutError('synthetic phone +66 800 000 000 secret')
    from concierge_kiosk.runtime import local_http
    monkeypatch.setattr(local_http._OPENER, 'open', timeout)
    with owner.turn('timeout'):
        assert model_commands(query='Please bring water', language='en', base_url='http://localhost:11434',
                              model='synthetic-qwen', enabled_request_kinds=ACTION_REQUEST_KINDS, timeout_seconds=.6) is None
    assert calls == [.6]
    assert matching(backend, 'model_call')[0].metadata['failure_class'] == 'timeout'
    calls.clear()
    stream = MockStream('unsafe raw output')
    monkeypatch.setattr(local_http._OPENER, 'open', lambda *a, **k: stream)
    checks = iter([False, False, True, True, True])
    with owner.turn('cancel'):
        assert semantic._chat('http://localhost:11434', {'model':'synthetic-qwen'}, .4,
                              lambda: next(checks, True)) is None
    assert stream.closed
    assert matching(backend, 'model_call')[-1].metadata['status'] == 'cancelled'
    assert '+66' not in str([s.metadata for s in backend.spans])


@pytest.mark.parametrize('failure', [Full(), TimeoutError(), ConnectionError(), URLError('synthetic DNS failure'), PermissionError('401')])
def test_export_failures_never_change_response(failure):
    owner = Observability(FakeBackend(failure))
    with owner.turn('safe'):
        with observation('confirmation'):
            result = {'guest_response': 'synthetic approved response', 'writes': 1}
    assert result['writes'] == 1
    assert current_scope() is None


def test_allowlist_drops_nested_memory_model_output_and_http_error(telemetry):
    owner, backend = telemetry
    pii = {'guest': 'Synthetic Guest', 'room_number':'502', 'phone':'+66-123-456',
           'email':'synthetic@example.test', 'passport':'TEST-ID', 'card':'4111111111111111',
           'nested':[{'state': {'token':'synthetic-auth-token'}}]}
    assert sanitize(pii) == {}
    with owner.turn('synthetic-session-token'):
        event('memory_resolution', state=pii, status='Synthetic Guest', anchor_existed=True)
        with pytest.raises(ValueError):
            with observation('model_call', output=pii, error=pii):
                raise ValueError(json.dumps(pii))
    exported = str([s.metadata for s in backend.spans])
    for value in ('Synthetic Guest', '502', 'synthetic@example.test', '4111111111111111', 'synthetic-auth-token', 'synthetic-session-token'):
        assert value not in exported


def test_concurrent_sessions_and_async_thread_context_are_isolated(telemetry):
    owner, backend = telemetry
    barrier = Barrier(2)
    def worker(session):
        with owner.turn(session):
            barrier.wait()
            event('memory_resolution', anchor_existed=session == 'first')
            return current_scope().trace_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        traces = list(pool.map(worker, ('first', 'second')))
    assert len(set(traces)) == 2
    for trace in traces:
        spans = [s for s in backend.spans if s.trace_id == trace]
        assert len({s.metadata['session_pseudonym'] for s in spans}) == 1
    async def run():
        with owner.turn('async') as scope:
            await asyncio.to_thread(event, 'memory_resolution', anchor_existed=True)
            assert current_scope().trace_id == scope.trace_id
    asyncio.run(run())
    assert current_scope() is None


def test_scores_reuse_harness_meaning_and_stable_reexport_identity(telemetry):
    from concierge_kiosk.agent.runtime.eval.harness import TaskJourneyScore
    owner, backend = telemetry
    score = TaskJourneyScore('synthetic-case', True, 3, 0.0, 0.0, 100.5, .75)
    at = datetime(2026, 10, 9, tzinfo=timezone.utc)
    kwargs = {'evaluation_id':'same-result', 'evaluated_at':at, 'trace_id':'1' * 32}
    assert export_existing_scores(owner, score, **kwargs)['queued'] == 6
    first = list(backend.scores)
    export_existing_scores(owner, score, **kwargs)
    assert first == backend.scores[6:]
    assert first[0]['data_type'] == 'BOOLEAN' and first[0]['value'] == 1
    latency = next(s for s in first if s['name'] == 'p95_latency_ms')
    assert latency['value'] == 100.5
    assert export_existing_scores(owner, score, evaluation_id='offline', evaluated_at=at)['status'] == 'unlinked'
    assert export_existing_scores(owner, {'invented_score':1, 'wer':None}, **kwargs)['queued'] == 0


def test_shutdown_is_finite_and_disabled_has_no_helper_thread(telemetry):
    owner, backend = telemetry
    assert owner.shutdown()
    assert backend.stopped
    release = Event()
    stalled = FakeBackend()
    stalled.shutdown = lambda: release.wait(3)
    owner = Observability(stalled)
    assert owner.shutdown(timeout=.01) is False
    release.set()
    with owner.turn('after-shutdown'):
        assert current_scope() is None


def test_score_categories_units_and_failure_preserve_existing_results(telemetry):
    owner, backend = telemetry
    values = {'task_success': False, 'budget_exhausted': 'steps', 'r3': .75,
              'p95_upper_ms': 500, 'fallback_mode': .5, 'wer': float('nan'), 'raw_label': 'Synthetic Guest'}
    kwargs = {'evaluation_id': 'synthetic-result',
              'evaluated_at': datetime(2026, 10, 9, tzinfo=timezone.utc),
              'session_pseudonym': owner.pseudonym('session', 'synthetic')}
    report = export_existing_scores(owner, values, **kwargs)
    assert report['queued'] == 5 and report['skipped'] == 2
    exported = {s['name']: s for s in backend.scores}
    assert exported['task_success']['value'] == 0
    assert exported['budget_exhausted']['data_type'] == 'CATEGORICAL'
    assert exported['budget_exhausted']['value'] == 'steps'
    assert exported['p95_upper_ms']['value'] == 500
    backend.failure = Full()
    assert export_existing_scores(owner, values, **kwargs)['status'] == 'export_failed'
    assert values['budget_exhausted'] == 'steps'


def test_failed_root_start_suppresses_orphans_without_changing_result():
    backend = FakeBackend()
    def fail(*args, **kwargs):
        raise TimeoutError('Synthetic Guest exporter unavailable')
    backend.start = fail
    owner = Observability(backend)
    with owner.turn('synthetic'):
        event('understanding', status='success')
        result = 'synthetic guest response'
    assert result == 'synthetic guest response'
    assert not backend.spans and current_scope() is None


def test_sdk_export_stage_and_scope_masking_with_real_v4_sdk():
    pytest.importorskip('langfuse')
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    sink = InMemorySpanExporter()
    cfg = Settings(environment='test', llm_model='synthetic-qwen', langfuse_enabled=True,
                   langfuse_public_key='synthetic-public-' + secrets.token_hex(4),
                   langfuse_secret_key='synthetic-secret', langfuse_base_url='https://invalid.test',
                   langfuse_sample_rate=1)
    backend = SDKBackend(cfg, span_exporter=sink)
    owner = Observability(backend, model_names=('synthetic-qwen',))
    pii = 'Synthetic Guest room 502 email synthetic@example.test token synthetic-secret'
    with owner.turn('synthetic-raw-session'):
        scope = current_scope()
        handle = backend.start('model_call', scope.trace_id, scope.parent_id,
                               {'privacy_policy':'metadata_only', 'nested': {'raw':[pii]}})
        handle.update(input={'state':[{'raw':pii}]}, output={'model_output':pii},
                      metadata={'raw':pii}, usage_details={'input':12,'output':3})
        backend.finish(handle, {'privacy_policy':'metadata_only', 'status':'success'})
    backend.provider.force_flush(timeout_millis=2000)
    spans = sink.get_finished_spans()
    assert len(spans) == 2
    root = next(s for s in spans if s.name == 'guest_turn')
    model = next(s for s in spans if s.name == 'model_call')
    assert root.parent is None
    assert root.attributes['langfuse.environment'] == 'development'
    assert model.parent.span_id == root.context.span_id
    assert model.context.trace_id == root.context.trace_id
    serialized = str([(s.name, dict(s.attributes), dict(s.resource.attributes),
                       dict(s.instrumentation_scope.attributes or {}), s.events, s.links) for s in spans])
    for value in (pii, 'synthetic-secret', cfg.langfuse_public_key.get_secret_value(), 'synthetic-raw-session'):
        assert value not in serialized
    assert owner.shutdown(timeout=2)


def test_api_guest_turn_langgraph_draft_confirmation_and_replay(tmp_path, monkeypatch, understand, telemetry):
    from test_understanding_layers import _client
    from fastapi.testclient import TestClient
    owner, backend = telemetry
    app = _client(tmp_path)
    app.state.observability = owner
    app.state.workflows.observability = owner
    understand('Please bring towels', 'amenity_delivery')
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        headers = {'X-CSRF-Token':session['csrf_token']}
        ask = client.post('/api/ask', headers=headers, json={'query':'Please bring towels', 'language':'en'})
        assert ask.status_code == 200, ask.text
        assert len(matching(backend, 'guest_turn')) == 1
        assert len(matching(backend, 'final_response')) == 1
        assert matching(backend, 'langgraph_execution')
        assert matching(backend, 'verify_goal')
        assert matching(backend, 'tool_execution')
        graph = matching(backend, 'langgraph_execution')[0]
        nodes = [s for s in backend.spans if s.name in {'verify_goal','plan_next_action','execute_tool'}]
        assert all(s.parent_id == graph.id for s in nodes)
        tools = matching(backend, 'tool_execution')
        execute_nodes = {s.id for s in matching(backend, 'execute_tool')}
        assert all(s.parent_id in execute_nodes for s in tools)
        with app.state.store.connection() as con:
            assert con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] == 0
        # Authoritative workflow receives a complete synthetic payload, unchanged.
        proposal = app.state.workflows.prepare(session['session_id'], 'facilities', 'en',
            'Synthetic wake-up request', 'synthetic-wakeup-nonce', {'preferred_time':'08:00'}, service_code='wake_up_call')
        first = app.state.workflows.confirm(session['session_id'], proposal['id'], True)
        second = app.state.workflows.confirm(session['session_id'], proposal['id'], True)
        assert first['id'] == second['id']
        assert second['idempotent_replay'] is True
        receipts = matching(backend, 'verified_write_receipt')
        assert [s.metadata['business_writes'] for s in receipts] == [1, 0]
        assert receipts[0].metadata['proposal_link'] == receipts[1].metadata['proposal_link']
        assert receipts[0].trace_id != receipts[1].trace_id
        with app.state.store.connection() as con:
            assert con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] == 1


def test_real_lexical_retrieval_and_citation_only_export_metadata(tmp_path, telemetry):
    from concierge_kiosk.persistence.sqlite_store import Store
    from concierge_kiosk.rag.ingestion import ingest_text
    from concierge_kiosk.rag.retrieval.engine import retrieve
    from concierge_kiosk.rag.retrieval.policy import RAGPolicy
    from concierge_kiosk.rag.grounding.citations import bind_citations
    owner, backend = telemetry
    store = Store(tmp_path / 'rag.sqlite3')
    text = 'The swimming pool opens at 07:00.'
    document = ('---\ndocument_id: synthetic-source\nproperty_id: TEST_PROPERTY\n'
                'title: Swimming pool\nlanguage: en\nclassification: public\n'
                'effective_from: 2026-01-01\ndomain: general\n---\n# Information\n' + text)
    ingest_text(store, document, property_id='TEST_PROPERTY')
    with owner.turn('rag-guest'):
        result = retrieve(store, property_id='TEST_PROPERTY', language='en',
                          query='swimming pool opens', effective_date='2026-10-09', mode='lexical',
                          policy=RAGPolicy(lexical_coverage=.1))
        assert result.sources
        citations = bind_citations(store, property_id='TEST_PROPERTY', language='en',
                                   answer=text, sources=result.sources, effective_date='2026-10-09')
        assert citations.citations
    retrieval, = matching(backend, 'retrieval')
    assert retrieval.metadata['retrieval_mode'] == 'lexical'
    assert retrieval.metadata['top_k'] == 3
    assert retrieval.metadata['candidate_count'] >= 1
    assert retrieval.metadata['source_count'] >= 1
    assert len(retrieval.metadata['source_links'][0]) == 64
    assert matching(backend, 'citation_validation')[0].metadata['verified'] is True
    assert text not in str([s.metadata for s in backend.spans])
    assert 'synthetic-source' not in str([s.metadata for s in backend.spans])


def test_memory_ttl_and_session_lookup_do_not_export_anchors(monkeypatch, telemetry):
    from concierge_kiosk.agent.memory.conversation import ConversationMemory
    from concierge_kiosk.agent.memory import conversation
    owner, backend = telemetry
    now = [100.0]
    monkeypatch.setattr(conversation.time, 'monotonic', lambda: now[0])
    memory = ConversationMemory(ttl=10)
    source = {'source_id':'synthetic-private-id', 'chunk_id':'synthetic-chunk', 'revision':'r1',
              'title':'Synthetic Guest', 'heading':'synthetic@example.test', 'language':'en'}
    assert memory.commit_topic('first', 'en', expected_version=0, sources=[source])[0]
    with owner.turn('first'):
        assert memory.recent_anchor('first', 'en') is not None
        assert memory.recent_anchor('other', 'en') is None
        now[0] += 11
        assert memory.recent_anchor('first', 'en') is None
    spans = matching(backend, 'memory_lookup')
    assert [s.metadata['session_ownership_verified'] for s in spans] == [True, False, True]
    assert spans[-1].metadata['context_invalidation_reason'] == 'ttl_expired'
    assert spans[-1].metadata['ttl_valid'] is False
    assert 'Synthetic Guest' not in str([s.metadata for s in spans])


@pytest.mark.parametrize('telemetry_fails', [False, True])
def test_emergency_api_is_independent_of_qwen_and_exporter(tmp_path, monkeypatch, telemetry_fails):
    from concierge_kiosk.main import create_app
    from fastapi.testclient import TestClient
    owner = Observability(FakeBackend(ConnectionError() if telemetry_fails else None))
    app = create_app(Settings(environment='test', db_path=tmp_path/'emergency.sqlite3'))
    app.state.observability = owner
    app.state.workflows.observability = owner
    monkeypatch.setattr(semantic, '_chat', lambda *a, **kw: pytest.fail('Emergency called Qwen'))
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token':session['csrf_token']},
                               json={'query':'SOS', 'language':'en'})
        assert response.status_code == 200, response.text
        assert response.json()['tool_route'] == 'emergency'
        assert not matching(owner.backend, 'model_call')
        assert len(matching(owner.backend, 'guest_turn')) == 1


def test_business_confirmation_export_failure_and_staff_link_preserve_writes(tmp_path, telemetry):
    from test_understanding_layers import _client
    from fastapi.testclient import TestClient
    owner, backend = telemetry
    app = _client(tmp_path)
    app.state.observability = owner
    app.state.workflows.observability = owner
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        proposal = app.state.workflows.prepare(session['session_id'], 'facilities', 'en',
            'Synthetic wake-up request', 'synthetic-confirm-failure', {'preferred_time':'08:00'}, service_code='wake_up_call')
        backend.failure = Full()
        first = app.state.workflows.confirm(session['session_id'], proposal['id'], True)
        backend.failure = None
        replay = app.state.workflows.confirm(session['session_id'], proposal['id'], True)
        assert replay['id'] == first['id'] and replay['idempotent_replay'] is True
        reviewed = app.state.workflows.staff_transition(first['id'], 'approve', 'synthetic-staff',
                         verified=True, note='Synthetic independent staff review')
        assert reviewed['status'] == 'approved'
        staff, = matching(backend, 'status_transition')
        assert staff.metadata['request_link'] == owner.pseudonym('request', first['id'])
        assert 'session_pseudonym' not in staff.metadata
        assert 'synthetic-staff' not in str([s.metadata for s in backend.spans])


def test_sdk_rejects_auto_captured_nested_state_events_and_secret_names():
    from concierge_kiosk.runtime.observability import should_export_span
    span = SimpleNamespace(name='guest_turn', attributes={'langfuse.observation.metadata.privacy_policy':'metadata_only'},
                           events=(), links=(), status=SimpleNamespace(description=None))
    assert should_export_span(span)
    span.events = ('Synthetic Guest exception event',)
    assert not should_export_span(span)
    span.events = ()
    span.name = 'Synthetic Guest room 502'
    assert not should_export_span(span)
    span.name = 'guest_turn'
    span.attributes = {'raw_state':'synthetic@example.test'}
    assert not should_export_span(span)


def test_wp12_api_regression_reports_rejection_without_proposal_or_write(tmp_path, monkeypatch, telemetry):
    from test_understanding_layers import _client
    from test_semantic_authorization import WP12_QUERY, WP12_RAW
    from concierge_kiosk.application.conversation import engine
    from concierge_kiosk.agent.understanding import commands
    from fastapi.testclient import TestClient
    owner, backend = telemetry
    app = _client(tmp_path)
    app.state.observability = owner
    app.state.workflows.observability = owner
    monkeypatch.setattr(commands, '_chat', lambda *a, **kw: WP12_RAW)
    def mocked_nlu(self, query, language, session, **kwargs):
        return model_commands(query=query, language=language, base_url='http://localhost:11434',
                              model='synthetic-qwen', enabled_request_kinds=kwargs['enabled_request_kinds'],
                              on_outcome=engine._COMMAND_OUTCOME.set)
    monkeypatch.setattr(engine._TurnRuntimeSupport, 'command_for_session', mocked_nlu)
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token':session['csrf_token']},
                               json={'query':WP12_QUERY,'language':'vi'})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['tool_route'] == 'nlu_failure'
        assert not body['suggested_action']
        assert body['observability']['trace_id'] == matching(backend,'guest_turn')[0].trace_id
        final, = matching(backend, 'final_response')
        assert final.metadata['service_proposal_count'] == 0
        assert final.metadata['business_writes'] == 0
        assert final.metadata['route'] == 'nlu_failure'
        assert not matching(backend, 'tool_execution')
        with app.state.store.connection() as con:
            assert con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] == 0
            assert con.execute('SELECT COUNT(*) FROM proposals').fetchone()[0] == 0


def test_no_model_span_for_pre_cancel_or_disabled_generation(monkeypatch, telemetry):
    from concierge_kiosk.agent.orchestration.grounding import grounded_response
    owner, backend = telemetry
    with owner.turn('no-inference'):
        assert semantic._chat('http://localhost:11434', {'model':'synthetic-qwen'}, .1, lambda: True) is None
        assert grounded_response(base_url='', model='', question='synthetic', evidence=[], language='en') is None
    assert not matching(backend, 'model_call')


def test_actual_sdk_export_failure_cannot_propagate_to_guest():
    pytest.importorskip('langfuse')
    from opentelemetry.sdk.trace.export import SpanExporter
    class BrokenSink(SpanExporter):
        def export(self, spans):
            raise Full('synthetic queue full')
        def shutdown(self):
            pass
    cfg = Settings(environment='test', langfuse_public_key='synthetic-public-'+secrets.token_hex(4),
                   langfuse_secret_key='synthetic-secret', langfuse_base_url='https://invalid.test')
    backend = SDKBackend(cfg, span_exporter=BrokenSink())
    owner = Observability(backend)
    with owner.turn('no-network'):
        event('confirmation', business_writes=1, confirmation_verified=True)
        result = 'synthetic guest response'
    assert backend.provider.force_flush(timeout_millis=2000)
    assert result == 'synthetic guest response'
    assert owner.shutdown(timeout=2)


def test_sdk_debug_logs_and_cached_provider_fail_closed(monkeypatch, caplog):
    pytest.importorskip('langfuse')
    import logging
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    monkeypatch.setenv('LANGFUSE_DEBUG', 'true')
    cfg = Settings(environment='test', langfuse_enabled=True, langfuse_sample_rate=1,
                   langfuse_public_key='synthetic-public-' + secrets.token_hex(4),
                   langfuse_secret_key='synthetic-secret', langfuse_base_url='https://invalid.test')
    with caplog.at_level(logging.DEBUG):
        backend = SDKBackend(cfg, span_exporter=InMemorySpanExporter())
        logging.getLogger('langfuse').error('Synthetic room 502 key %s', cfg.langfuse_secret_key.get_secret_value())
        logging.getLogger('opentelemetry.exporter.otlp.proto.http.trace_exporter').error('synthetic HTTP body %s', cfg.langfuse_public_key.get_secret_value())
        second = Observability.configured(cfg, backend_factory=lambda c: SDKBackend(c, span_exporter=InMemorySpanExporter()))
    assert second.backend is None
    assert cfg.langfuse_public_key.get_secret_value() not in caplog.text
    assert cfg.langfuse_secret_key.get_secret_value() not in caplog.text
    assert 'room 502' not in caplog.text
    assert Observability(backend).shutdown(timeout=2)
