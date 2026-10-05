"""A tool/model contract violation fails closed instead of becoming HTTP 500."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.core.tool_contracts import (
    contract_failure_result, tool_error_observation, validate_tool_result,
)
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.core.domain_profile import supported_languages
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.main import create_app
from concierge_kiosk.rag.retrieval import abstention_answer


def test_tool_error_observation_is_bounded_and_not_guest_text():
    observation = tool_error_observation(RuntimeError('secret db path C:\\data\\x.sqlite3'))
    assert observation == {'ok': False, 'error': 'RuntimeError', 'hint': 'retry_or_staff_handoff'}
    odd = tool_error_observation('bad label; DROP TABLE', 'x' * 500)
    assert odd['ok'] is False
    assert odd['error'].replace('_', '').isalnum() and len(odd['error']) <= 64
    assert len(odd['hint']) <= 160
    assert tool_error_observation(None)['error'] == 'tool_error'


@pytest.mark.parametrize('language', sorted(supported_languages()))
def test_contract_failure_result_is_evidence_free_and_itself_valid(language: str):
    result = contract_failure_result(language)
    assert result['answer'] == abstention_answer(language)
    assert result['sources'] == [] and result['citations'] == []
    assert result['suggested_action'] is None
    assert result['requires_staff_review'] is False
    assert result['request_completed'] is False
    validate_tool_result(RouteDecision('knowledge', False), result, 'pool hours', language)


def _app(tmp_path: Path):
    return create_app(Settings(
        db_path=tmp_path / 'contract-fallback.sqlite3',
        property_id='FURAMA_DANANG',
        property_name='Furama Resort Danang',
        property_timezone='Asia/Ho_Chi_Minh',
        environment='test',
        orchestrator='direct',
    ))


def test_non_emergency_contract_violation_answers_with_abstention_not_500(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import concierge_kiosk.application.conversation.engine as engine_module

    original = engine_module.validate_tool_result
    calls: list[str] = []

    def violate(decision, result, query, language):
        if decision.branch == 'emergency':
            return original(decision, result, query, language)
        calls.append(decision.branch)
        raise RuntimeError('simulated tool contract violation')

    monkeypatch.setattr(engine_module, 'validate_tool_result', violate)
    with TestClient(_app(tmp_path), raise_server_exceptions=False) as client:
        session = client.post('/api/session')
        response = client.post(
            '/api/ask',
            headers={'X-CSRF-Token': session.json()['csrf_token']},
            json={'query': 'What time does the pool open?', 'language': 'en'},
        )

    assert calls, 'the contract check was never reached'
    assert response.status_code == 200
    body = response.json()
    assert body['answer'] == abstention_answer('en')
    assert body['citations'] == []
    assert body['suggested_action'] is None
    assert body['requires_staff_review'] is False
