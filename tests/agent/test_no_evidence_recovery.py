from __future__ import annotations

import json
import shutil
from pathlib import Path

from concierge_kiosk.agent.core.tool_contracts import validate_tool_result
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.application.conversation.answers import _no_evidence_handoff_suggestion
from concierge_kiosk.application.conversation.recovery import (
    load_support_directory, recovery_metadata, related_topics, support_contact,
)
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.ingestion import ingest_text
from concierge_kiosk.rag.retrieval import abstention_answer
from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.core.dataset_layout import dataset_path


def _doc(doc_id: str, title: str, language: str, body: str, *, domain: str = 'general') -> str:
    return f"""---
document_id: {doc_id}
property_id: TEST_PROPERTY
title: {title}
language: {language}
classification: public
effective_from: 2026-01-01
domain: {domain}
---
# Information
{body}
"""


def test_plain_no_evidence_does_not_force_human_action(tmp_path: Path):
    result = {
        'answer': abstention_answer('en'), 'sources': [], 'citations': [],
        'suggested_action': None, 'retrieval_mode': 'no_match',
        'generation_mode': 'extractive', 'request_completed': False,
        'grounding': 'no_evidence', 'requires_staff_review': False,
    }
    validate_tool_result(RouteDecision('knowledge', False), result, 'wifi password', 'en')


def test_related_topics_are_current_public_titles(tmp_path: Path):
    store = Store(tmp_path / 'db.sqlite3')
    ingest_text(store, _doc('pool', 'Swimming Pool', 'en',
                            'Swimming pool information for resort guests.', domain='recreation'),
                property_id='TEST_PROPERTY')
    ingest_text(store, _doc('spa_info', 'V-Senses Spa', 'en',
                            'Spa treatment information for resort guests.', domain='spa'),
                property_id='TEST_PROPERTY')
    topics = related_topics(store, property_id='TEST_PROPERTY', language='en',
                            query='pool information please', effective_date='2026-10-01')
    assert topics
    assert topics[0]['label'] == 'Swimming Pool'
    assert topics[0]['query'] == 'Swimming Pool'


def test_support_contact_uses_manifest_pinned_department_directory():
    directory = load_support_directory(str(dataset_path('')), 'FURAMA_DANANG')
    assert directory is not None
    spa = support_contact(directory, 'spa dress code', 'en')
    assert spa is not None
    assert spa['department'] == 'SPA_WELLNESS'
    assert spa['extensions'] == ['16']
    assert spa['grounding'] == 'manifest_pinned_directory'

    dining = support_contact(directory, 'restaurant menu', 'en')
    assert dining is not None
    assert dining['department'] == 'FNB_ROOM_SERVICE'
    assert '12' in dining['extensions']
    assert '+84 236 651 9999' in dining['phones']


def test_support_contact_does_not_default_unrelated_question_to_reception():
    directory = load_support_directory(str(dataset_path('')), 'FURAMA_DANANG')
    assert directory is not None
    assert support_contact(directory, 'wifi password', 'en') is None


def test_tampered_structured_contact_file_is_not_used(tmp_path: Path):
    target = tmp_path / 'datasets'
    shutil.copytree(dataset_path(''), target)
    contacts_path = dataset_path('knowledge/canonical/contacts.json', target)
    contacts = json.loads(contacts_path.read_text(encoding='utf-8'))
    contacts[0]['phones'] = ['0000']
    contacts_path.write_text(json.dumps(contacts), encoding='utf-8')
    assert load_support_directory(str(target), 'FURAMA_DANANG') is None


def test_conflict_recovery_recommends_staff_but_routine_miss_does_not(tmp_path: Path):
    store = Store(tmp_path / 'db.sqlite3')
    normal = recovery_metadata(store, None, property_id='TEST_PROPERTY', language='en',
                               query='unknown question', effective_date='2026-10-01',
                               retrieval_mode='no_match')
    conflict = recovery_metadata(store, None, property_id='TEST_PROPERTY', language='en',
                                 query='unknown question', effective_date='2026-10-01',
                                 retrieval_mode='conflict_abstention')
    assert normal['handoff_recommended'] is False
    assert conflict['handoff_recommended'] is True


def test_no_evidence_recovery_handoff_offers_advisory_human_handoff():
    query = 'My AC is not cooling'
    # Only the bounded recovery decision may offer staff; the answer path no
    # longer guesses a service from the guest's words.
    assert _no_evidence_handoff_suggestion(query, 'en', False) is None
    suggestion = _no_evidence_handoff_suggestion(query, 'en', True)
    assert suggestion == {'kind': 'human', 'details': query}

    # The knowledge contract accepts only a consent-only human suggestion; no
    # service workflow or business write is authorized by this fallback.
    result = {
        'answer': abstention_answer('en'), 'sources': [], 'citations': [],
        'suggested_action': suggestion, 'retrieval_mode': 'no_match',
        'generation_mode': 'extractive', 'request_completed': False,
        'grounding': 'no_evidence', 'requires_staff_review': True,
    }
    validate_tool_result(RouteDecision('knowledge', False), result, query, 'en')




def test_no_evidence_retry_counter_keeps_only_fingerprint_and_resets():
    from concierge_kiosk.agent.memory.conversation import ConversationMemory
    memory = ConversationMemory(ttl=60, max_sessions=4)
    assert memory.note_no_evidence('s1', 'What is the secret spa rule?') == 1
    assert memory.note_no_evidence('s1', '  what is the secret spa rule?  ') == 2
    assert memory.note_no_evidence('s1', 'Different question') == 1
    memory.clear_no_evidence('s1')
    assert memory.note_no_evidence('s1', 'Different question') == 1
    # The memory structure must not retain raw guest text.
    assert 'Different question' not in repr(memory._no_evidence_retries)


def test_no_evidence_contract_accepts_only_exact_structured_recovery_text():
    topics = [{'label': 'Wi-Fi', 'query': 'Wi-Fi', 'domain': 'rooms', 'language': 'en'}]
    contact = {
        'label': 'Front Office', 'department': 'FRONT_OFFICE', 'phones': [],
        'extensions': ['10'], 'email': None, 'grounding': 'manifest_pinned_directory',
    }
    answer = (abstention_answer('en') + '\n'
              + i18n_text('recovery.related_prompt', 'en', topics='Wi-Fi') + '\n'
              + i18n_text('recovery.contact_extension', 'en',
                          department='FRONT_OFFICE', extension='10'))
    result = {
        'answer': answer, 'sources': [], 'citations': [], 'suggested_action': None,
        'retrieval_mode': 'no_match', 'generation_mode': 'extractive',
        'request_completed': False, 'grounding': 'no_evidence',
        'requires_staff_review': False, 'related_topics': topics,
        'support_contact': contact, 'recovery_mode': 'self_service',
    }
    validate_tool_result(RouteDecision('knowledge', False), result, 'wifi password', 'en')

    tampered = dict(result)
    tampered['answer'] = answer + '\nThe minibar costs 100,000 VND.'
    import pytest
    with pytest.raises(RuntimeError, match='unsupported hotel facts'):
        validate_tool_result(RouteDecision('knowledge', False), tampered, 'wifi password', 'en')


def test_planning_no_evidence_with_related_topics_returns_recovery_answer(tmp_path: Path, monkeypatch):
    """Regression: the planning no-evidence branch used an undefined variable."""
    from types import SimpleNamespace
    from concierge_kiosk.application.conversation import answers

    store = Store(tmp_path / 'db.sqlite3')
    monkeypatch.setattr(answers, 'recovery_metadata', lambda *a, **k: {
        'related_topics': [{'label': 'Swimming Pool', 'query': 'Swimming Pool'}],
        'support_contact': None, 'handoff_recommended': False})
    cfg = SimpleNamespace(structured_dataset_dir='', property_id='TEST_PROPERTY',
                          planning_release_path='', planning_release_sha256='')
    services = answers.build_answer_services(
        store=store, workflows=None, cfg=cfg, conversations=None, rag_policy=None,
        embedder=None, reranker=None, record_metric=lambda *a: None,
        speech_metric=lambda *a: None, slm_permitted=lambda: False,
        audio_admission=None, observe_slm=lambda *a: None)
    result = services.planning_answer(
        'Từ 5 giờ chiều đến 9 giờ tối, sắp xếp giúp tôi ăn tối rồi đi spa', 'vi', 's1',
        effective_date='2026-10-01')
    assert result['evidence_status'] == 'UNSUPPORTED'
    assert result['answer'].startswith(abstention_answer('vi'))
    assert 'Swimming Pool' in result['answer']
    assert result['citations'] == []
