"""A failed command stream can recover only a bounded verified read reference."""
from types import SimpleNamespace
from concierge_kiosk.agent.memory.conversation import ConversationMemory
from concierge_kiosk.agent.memory.task_memory import AgentTaskMemory
from concierge_kiosk.agent.memory.models import EvidenceAnchor
from concierge_kiosk.agent.understanding.commands import Command
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.application.conversation.engine import _TurnRuntimeSupport
import pytest


def test_empty_commands_can_recover_verified_navigation(monkeypatch):
    memory = ConversationMemory()
    anchor = EvidenceAnchor('source', 'r1', 'chunk', 'Verified pool', 'Pool', 'en')
    support = _TurnRuntimeSupport(cfg=SimpleNamespace(), workflows=SimpleNamespace(),
        conversations=memory, agent_checkpoints=None, agent_tasks=AgentTaskMemory(),
        audio_admission=SimpleNamespace(), slm_permitted=lambda: True)
    monkeypatch.setattr(support, 'command_for_session', lambda *a, **kw: None)
    monkeypatch.setattr(support, 'fallback_commands', lambda *a, **kw: None)
    monkeypatch.setattr(support, 'reference_command_for_session', lambda *a, **kw:
        (Command('Navigate', query='How do I reach that place?', refers_to_context=True), anchor),
        raising=False)
    result = support.understand_turn('How do I reach that place?', 'en', 's1',
        RouteDecision('knowledge', False), enabled_request_kinds=frozenset({'facilities'}))
    assert result[0].branch == 'navigation'
    assert 'Verified pool' in result[2]


@pytest.mark.parametrize('raw', ['{"anchor_index":null,"intent":"abstain"}',
    '{"anchor_index":true,"intent":"Navigate"}', '{"anchor_index":2,"intent":"Navigate"}',
    '{"anchor_index":0,"intent":"StartGoal"}',
    '{"anchor_index":0,"intent":"Navigate","query":"invented"}'])
def test_reference_resolver_rejects_invalid_or_abstaining_proposals(monkeypatch, raw):
    from concierge_kiosk.agent.memory import reference_resolver as resolver
    monkeypatch.setattr(resolver, '_chat', lambda *a: raw)
    anchor = EvidenceAnchor('source', 'r1', 'chunk', 'Verified pool', 'Pool', 'en')
    assert resolver.model_reference_choice(query='that place?', language='en', candidates=(anchor,),
        base_url='http://127.0.0.1:1', model='mock', read_intent=True) is None


def test_failed_nlu_followup_uses_live_pool_map_and_never_crosses_sessions(
        tmp_path, shipped_db, monkeypatch):
    import shutil
    from pathlib import Path
    from fastapi.testclient import TestClient
    from test_understanding_layers import _client

    shutil.copyfile(shipped_db, tmp_path / 'edge.sqlite3')
    app = _client(tmp_path, map_release_path='releases/map-release.json',
                  map_release_sha256=Path('releases/map-release.sha256').read_text().strip())
    def recover(self, query, language, session):
        anchors = self.live_context_anchors(session, language)
        if len(anchors) == 1:
            return Command('Navigate', query=query, refers_to_context=True), anchors[0]
        return None
    monkeypatch.setattr(_TurnRuntimeSupport, 'reference_command_for_session', recover)
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        headers = {'X-CSRF-Token': session['csrf_token']}
        first = client.post('/api/ask', headers=headers,
            json={'query': 'What time does the swimming pool open?', 'language': 'en'}).json()
        assert first['citations'], first
        second = client.post('/api/ask', headers=headers,
            json={'query': 'How can I reach that place from the lobby?', 'language': 'en'})
        assert second.status_code == 200, second.text
        assert second.json()['map_guidance']['status'] == 'verified', second.json()
        assert 'Pool' in second.json()['map_guidance']['destination']
        support = app.state.conversation_engine.turn_support
        explicit = 'How do I reach V-Senses Spa?'
        result = support._apply_commands((Command('Navigate', query=explicit, refers_to_context=True),),
            RouteDecision('knowledge', False), query=explicit, execution_query=explicit,
            language='en', session=session['session_id'], pending_task=None, has_pending_proposal=False)
        assert result[2] == explicit, 'an explicit destination must outrank the pool anchor'
        anchors = support.live_context_anchors(session['session_id'], 'en')
        assert anchors
        with app.state.store.connection(write=True) as con:
            for anchor in anchors:
                con.execute('UPDATE knowledge SET active=0 WHERE id=?', (anchor.chunk_id,))
        assert not support.live_context_anchors(session['session_id'], 'en'), 'withdrawn sources are unusable'
        # A new session receives no candidate from the previous guest.
        other = client.post('/api/session').json()
        assert not app.state.conversation_engine.turn_support.live_context_anchors(other['session_id'], 'en')


def test_ambiguous_reference_clarifies_instead_of_selecting_a_place(monkeypatch):
    from concierge_kiosk.agent.memory import reference_resolver as resolver
    monkeypatch.setattr(resolver, '_chat', lambda *a: '{"anchor_index":null,"intent":"Clarify"}')
    anchors = tuple(EvidenceAnchor('s', 'r', title, title, title, 'en') for title in ('Pool', 'Spa'))
    result = resolver.model_reference_choice(query='how do I get there?', language='en', candidates=anchors,
        base_url='http://127.0.0.1:1', model='mock', read_intent=True)
    assert result[0].type == 'Clarify' and result[1] is None


def test_memory_keeps_multi_place_reference_ambiguous():
    memory = ConversationMemory()
    sources = [{'source_id': 's', 'revision': 'r', 'chunk_id': str(i), 'title': title,
                'heading': title} for i, title in enumerate(('Pool', 'Spa'))]
    memory.commit_topic('s1', 'en', expected_version=0, sources=sources)
    assert memory.recent_anchor('s1', 'en') is None
    assert len(memory.recent_anchors('s1', 'en')) == 2
    assert memory.snapshot('s1', 'unrelated question', 'en').anchor is None
