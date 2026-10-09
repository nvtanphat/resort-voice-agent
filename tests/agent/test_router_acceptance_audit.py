"""WP14.1/WP15 offline acceptance. All model/network transports are blocked."""
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import secrets

import pytest

from test_observability import deny_real_network, FakeBackend, MockStream, matching
from concierge_kiosk.runtime.observability import Observability, SDKBackend
from concierge_kiosk.runtime.evaluation_observability import export_existing_scores
from concierge_kiosk.core.settings import Settings


def test_real_sdk_fastapi_wrong_and_valid_mock_nlu_with_existing_scores(tmp_path, monkeypatch):
    pytest.importorskip('langfuse')
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from fastapi.testclient import TestClient
    from concierge_kiosk.main import create_app
    from test_semantic_authorization import WP12_QUERY, WP12_RAW
    from concierge_kiosk.application.conversation import engine
    from concierge_kiosk.agent.understanding.commands import model_commands
    from concierge_kiosk.agent.runtime.eval.harness import grade_journey
    from concierge_kiosk.runtime import local_http
    from concierge_kiosk.i18n import text
    from concierge_kiosk.runtime import observability

    filtered = []
    original_filter = observability.should_export_span
    def recording_filter(span):
        accepted = original_filter(span)
        filtered.append((span.name, accepted, len(span.events), len(span.links),
                         bool(getattr(span.status, 'description', None))))
        return accepted
    monkeypatch.setattr(observability, 'should_export_span', recording_filter)

    sink = InMemorySpanExporter()
    cfg = Settings(environment='test', llm_model='synthetic-qwen', langfuse_enabled=True,
        langfuse_sample_rate=1, langfuse_public_key='synthetic-public-'+secrets.token_hex(5),
        langfuse_secret_key='synthetic-secret', langfuse_base_url='https://invalid.test')
    backend = SDKBackend(cfg, span_exporter=sink)
    telemetry = Observability(backend, model_names=('synthetic-qwen',))
    queued = []
    monkeypatch.setattr(backend.client, 'create_score', lambda **score: queued.append(score))
    profile = Path(__file__).resolve().parents[2]/'releases/property-profile.json'
    property_data = json.loads(profile.read_text(encoding='utf-8'))
    app = create_app(Settings(environment='test', db_path=tmp_path/'acceptance.sqlite3',
        property_id=property_data['property_id'], property_name=property_data['property_name'],
        property_timezone=property_data['property_timezone'],
        property_profile_path=str(profile), property_profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest()))
    app.state.observability = app.state.workflows.observability = telemetry
    calls = []
    def mocked_transport(request, *, timeout):
        calls.append(timeout)
        content = WP12_RAW if len(calls) == 1 else '{"commands":[{"type":"StartGoal","goal":"amenity_delivery","slots":[]}]}'
        return MockStream(content)
    monkeypatch.setattr(local_http._OPENER, 'open', mocked_transport)
    def nlu(self, query, language, session, **kwargs):
        assert 'facilities' in kwargs['enabled_request_kinds'], kwargs['enabled_request_kinds']
        return model_commands(query=query, language=language, base_url='http://localhost:11434',
            model='synthetic-qwen', enabled_request_kinds=kwargs['enabled_request_kinds'],
            on_outcome=engine._COMMAND_OUTCOME.set)
    monkeypatch.setattr(engine._TurnRuntimeSupport, 'command_for_session', nlu)
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        headers = {'X-CSRF-Token': session['csrf_token']}
        wrong = client.post('/api/ask', headers=headers, json={'query':WP12_QUERY,'language':'vi'}).json()
        assert wrong['tool_route'] == 'nlu_failure' and wrong['answer'] == text('nlu.clarify', 'vi')
        with app.state.store.connection() as con:
            for table in ('proposals', 'service_requests', 'agent_session_preferences'):
                assert con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
        valid = client.post('/api/ask', headers=headers,
            json={'query':'Please bring water','language':'en'}).json()
        assert valid['tool_route'] == 'service', (valid.get('failure_class'),len(calls),filtered)
        # Existing harness grades the actual response; no invented score/judge.
        score = grade_journey({'journey_id':'synthetic-sdk-acceptance',
            'turns':[{'turn':1,'expected_route':'service'}]}, lambda *args: valid)
        assert score.task_success
        association = valid['observability']['trace_id']
        kwargs = dict(evaluation_id='synthetic-sdk-result',
            evaluated_at=datetime(2026,10,9,tzinfo=timezone.utc), trace_id=association)
        export_existing_scores(telemetry, score, **kwargs)
        first = list(queued)
        export_existing_scores(telemetry, score, **kwargs)
        assert first == queued[len(first):] and all(s['trace_id'] == association for s in queued)
        backend.provider.force_flush(timeout_millis=2000)
        spans = sink.get_finished_spans()
        roots = [s for s in spans if s.parent is None]
        assert len(roots) == 2 and all(s.name == 'guest_turn' for s in roots), filtered
        assert len(calls) == 2  # One mocked transport per turn, no hidden retry.
        by_id = {s.context.span_id:s for s in spans}
        assert all(s.parent is None or s.parent.span_id in by_id for s in spans)
        wrong_spans = [s for s in spans if format(s.context.trace_id,'032x') == wrong['observability']['trace_id']]
        rejected = [s for s in wrong_spans if s.name == 'semantically_authorized']
        assert len(rejected) == 2
        assert all(s.attributes['langfuse.observation.metadata.reason_code'] == 'unsupported_semantics' for s in rejected)
        assert not any(s.name in {'prepare','tool_execution'} for s in wrong_spans)
        assert any(s.name == 'langgraph_execution' for s in spans)
        for span in spans:
            assert span.attributes['langfuse.observation.metadata.latency_ms'] >= 0
            if span.name == 'model_call':
                assert span.attributes['langfuse.observation.metadata.prompt_eval_ms'] == 2
                assert span.attributes['langfuse.observation.metadata.generation_ms'] == 4
                assert span.attributes['langfuse.observation.metadata.total_model_ms'] == 7
        exported = str([(s.name,dict(s.attributes),dict(s.resource.attributes),
                         dict(s.instrumentation_scope.attributes or {}),s.events,s.links) for s in spans])
        for sensitive in (WP12_QUERY, 'Please bring water', session['session_id'],
                          session['csrf_token'], 'synthetic-secret', cfg.langfuse_public_key.get_secret_value()):
            assert sensitive not in exported
        assert all(str(value) != '502' for s in spans for value in s.attributes.values())
        # Retain sanitized acceptance evidence, never inputs/state/session tokens.
        evidence = {'status':'OFFLINE_INTEGRATION_PASS','cloud_status':'CLOUD_INGESTION_NOT_VERIFIED',
            'mock_http_calls':len(calls),'real_qwen_http_calls':0,
            'observations':[{'name':s.name,'trace_id':format(s.context.trace_id,'032x'),
                'span_id':format(s.context.span_id,'016x'),
                'parent_id':format(s.parent.span_id,'016x') if s.parent else None,
                'attributes':dict(s.attributes)} for s in spans],
            'existing_score_names':[s['name'] for s in first],
            'score_trace_id':association, 'reexport_identity_equal':first == queued[len(first):]}
        output = tmp_path/'observability-acceptance.json'
        output.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')


def test_prepare_status_and_confirm_receipts_preserve_idempotency(tmp_path):
    from concierge_kiosk.domain.requests.workflows import Workflows
    from concierge_kiosk.persistence.sqlite_store import Store
    owner = Observability(FakeBackend())
    workflows = Workflows(Store(tmp_path/'business.sqlite3'), 'synthetic-property', observability=owner)
    session, _, _ = workflows.new_session()
    prepared = workflows.prepare(session,'facilities','en','Synthetic towels','synthetic-nonce',
        {'room_number':'1203','quantity':2,'requested_item':'bath towels','unit':'towels'}, service_code='amenity_delivery')
    assert matching(owner.backend,'prepare')[0].metadata['status'] == 'awaiting_confirmation'
    first = workflows.confirm(session,prepared['id'],True)
    replay = workflows.confirm(session,prepared['id'],True)
    assert first['id'] == replay['id']
    receipts = matching(owner.backend,'verified_write_receipt')
    assert [s.metadata['business_writes'] for s in receipts] == [1,0]
    assert {s.metadata['proposal_link'] for s in receipts} == {owner.pseudonym('proposal',prepared['id'])}


def test_offline_audit_keeps_oracle_labels_separate_from_predictions():
    from tools.evaluation.audit_router_evidence import oracle_command, percentile
    row = {'utterance':'Please bring water to room 502','service_code':'amenity_delivery',
           'expected_slots':{'requested_item':'water','room_number':'502','quantity':3}}
    command = oracle_command(row)
    assert {s.name for s in command.slots} == {'requested_item','room_number'}
    assert percentile([3,1,2],.95) == 3
    assert row['expected_slots']['quantity'] == 3


@pytest.mark.parametrize('initial,initial_language,followup,language,first_command', [
    ('what time does the pool open','en','where is it?','en',None),
    ('hồ bơi mở cửa lúc mấy giờ','vi','nó ở đâu','vi',None),
    ('hồ bơi mở cửa lúc mấy giờ','vi','where is it?','en',None),
    ('spa ở đâu','vi','mấy giờ mở cửa','vi','Navigate'),
])
def test_b2_probe_with_explicit_reference_flag(tmp_path, monkeypatch, understand,
        initial, initial_language, followup, language, first_command):
    from test_conversation_memory_followups import client as existing_client
    from concierge_kiosk.main import create_app
    from concierge_kiosk import main
    from concierge_kiosk.agent.understanding.commands import Command
    apps = []
    def capture_app(*args, **kwargs):
        app = create_app(*args, **kwargs)
        apps.append(app)
        return app
    monkeypatch.setattr(main,'create_app',capture_app)
    if first_command:
        understand(initial,Command(first_command,query=initial))
    else:
        understand(initial,Command('AskInfo',query=initial,facet='hours'))
    understand(followup,Command('AskInfo' if first_command else 'Navigate',
        query=followup, refers_to_context=True, facet='hours' if first_command else None))
    fixture = existing_client.__wrapped__(tmp_path, monkeypatch)
    ask = next(fixture)
    try:
        first = ask(initial,initial_language)
        app = apps[0]
        # Read current anchors for the active test session, not a forged one.
        with app.state.store.connection() as con:
            session = con.execute('SELECT id FROM sessions ORDER BY created_at DESC LIMIT 1').fetchone()[0]
        anchors = app.state.conversations.recent_anchors(session,initial_language)
        second = ask(followup,language)
        diagnostic = {'case':first_command or ('cross_language' if language != initial_language else language),
                      'first_grounding':first.get('grounding'), 'anchor_count':len(anchors),
                      'second_route':second.get('tool_route'), 'second_evidence_status':second.get('evidence_status'),
                      'map_status':(second.get('map_guidance') or {}).get('status')}
        print('B2_DIAGNOSTIC '+json.dumps(diagnostic))
        assert second['map_guidance']['status'] == 'verified' if not first_command else second['evidence_status'] == 'SUPPORTED'
    finally:
        fixture.close()
