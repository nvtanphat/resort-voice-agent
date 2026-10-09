from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from concierge_kiosk.agent.core.concierge import AgentToolRequest
from concierge_kiosk.agent.understanding.routing import RouteDecision, classify_dialogue
from concierge_kiosk.agent.tools.scheduling import schedule_read
from concierge_kiosk.application.service_actions import ServiceActionService
from concierge_kiosk.persistence.sqlite_store import Store


def test_availability_routes_to_schedule_and_reads_pinned_release(shipped_db):
    decision = classify_dialogue('Is the spa available tomorrow?', 'en')
    assert decision.branch == 'knowledge'

    result = schedule_read(
        Store(str(shipped_db)),
        query='Is the spa available tomorrow?', language='en',
        property_id='FURAMA_DANANG', path='releases/planning-release.json',
        expected_sha256=sha256((Path('releases') / 'planning-release.json').read_bytes()).hexdigest(),
        effective_date='2026-10-01',
    )
    assert result['schedule_verified'] is True
    assert result['schedule_result'] == {
        'status': 'operating_hours_only',
        'matched_activity_ids': ['v_senses_spa'],
        'verified_availability': False,
        'availability_checked': False,
    }
    assert len(result['citations']) == 1
    assert '09:00' in result['answer']


def test_manage_request_is_a_session_scoped_staff_review_change():
    request_id = 'a' * 32

    class Workflows:
        def list_guest_requests(self, session, limit=10):
            assert session == 'session-a'
            return [{'id': request_id, 'status': 'pending_staff',
                     'guest_change_state': 'none', 'kind': 'facilities',
                     'service_code': 'amenity_delivery'}]

        def guest_request_progress(self, session, request_id):
            return {'details': 'Two towels to room 305'}

        def review_change(self, session, request_id, action, language, payload):
            assert (session, request_id, action) == ('session-a', request_id, 'cancel')
            return {'kind':'facilities','service':'amenity_delivery','details':'Review cancellation for this ticket', 'payload':payload,'change':{'request_id':request_id,'action':action}}

    service = ServiceActionService(
        workflows=Workflows(), task_memory=None, conversations=None, get_graph=lambda: None,
        record_metric=lambda *_: None, logger=SimpleNamespace(),
        enabled_request_kinds={'facilities'}, cfg=None,
    )
    result = service.manage_request_tool(AgentToolRequest(
        query='Cancel my request', language='en', session='session-a',
        effective_date='2026-10-01', action_nonce='nonce-1234',
        decision=RouteDecision('request_change', True),
        change_action='cancel',
    ))
    assert result['tool_route'] == 'manage_request'
    assert result['request_change']['needs_confirmation'] is True
    assert result['suggested_action']['change']['action'] == 'cancel'
    assert result['requires_staff_review'] is True
    assert result['agent_action']['status'] == 'confirmation_required'
    assert result['agent_action']['business_writes'] == 0


def test_manage_request_modify_extracts_only_allowlisted_changed_slots():
    request_id = 'b' * 32

    class Workflows:
        def list_guest_requests(self, session, limit=10):
            return [{'id': request_id, 'status': 'pending_staff',
                     'guest_change_state': 'none', 'kind': 'facilities',
                     'service_code': 'amenity_delivery'}]

        def guest_request_progress(self, session, request_id):
            return {'details': 'Two towels to room 305'}

        def review_change(self, session, request_id, action, language, payload):
            assert action == 'modify'
            assert payload == {'room_number': '306'}
            return {'kind':'facilities','service':'amenity_delivery','details':'Review changed fields for this ticket', 'payload':payload,'change':{'request_id':request_id,'action':action}}

    service = ServiceActionService(
        workflows=Workflows(), task_memory=None, conversations=None, get_graph=lambda: None,
        record_metric=lambda *_: None, logger=SimpleNamespace(),
        enabled_request_kinds={'facilities'}, cfg=None,
    )
    result = service.manage_request_tool(AgentToolRequest(
        query='Change my request to room 306', language='en', session='session-b',
        effective_date='2026-10-01', action_nonce='nonce-5678',
        decision=RouteDecision('request_change', True),
        change_action='modify',
    ))
    assert result['request_change']['needs_confirmation'] is True
    assert result['service_payload'] == {'room_number':'306'}


def test_manage_request_without_command_action_needs_details():
    class Workflows:
        @staticmethod
        def list_guest_requests(*_args, **_kwargs):
            raise AssertionError('missing command action must not inspect request state')

    service = ServiceActionService(
        workflows=Workflows(), task_memory=None, conversations=None, get_graph=lambda: None,
        record_metric=lambda *_: None, logger=SimpleNamespace(),
        enabled_request_kinds={'facilities'}, cfg=None,
    )
    result = service.manage_request_tool(AgentToolRequest(
        query='ambiguous request change', language='en', session='session-c',
        effective_date='2026-10-01', decision=RouteDecision('request_change', True),
    ))

    assert result['request_change'] == {'needs_details': True}
    assert result['requires_staff_review'] is False
