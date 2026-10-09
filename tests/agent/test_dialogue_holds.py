"""Dialogue acts that must not move a request forward: a time window, a deferral, a DND conflict.

Understanding is scripted (the test profile has no SLM); the validator, the evidence gate,
the governed loop, task memory and the workflow store are real.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from concierge_kiosk.agent.understanding.commands import Command, CommandSlot
from test_understanding_layers import _client

DINING = ('dining.restaurant_reservation',)


def _turn(client, headers, query):
    response = client.post('/api/ask', headers=headers, json={
        'query': query, 'language': 'vi', 'turn_nonce': uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()


def _session(client):
    session = client.post('/api/session').json()
    return session['session_id'], {'X-CSRF-Token': session['csrf_token']}


def _proposals(app, session_id):
    with app.state.store.connection() as con:
        return con.execute('select count(*) from proposals where session_id=?', (session_id,)).fetchone()[0]


def test_a_time_window_is_kept_but_a_booking_still_asks_for_the_hour(tmp_path, understand):
    understand('đặt bàn', Command('StartGoal', goal='dining_reservation',
                                  slots=(CommandSlot('party_size', '4'),)))
    understand('7 giờ', Command('SetSlot', field='preferred_time', value='7 giờ'))
    app = _client(tmp_path, DINING)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        first = _turn(client, headers, 'Tối nay đặt bàn cho 4 người giúp tôi')
        action = first['agent_action']
        assert action['status'] == 'needs_user_input'
        assert 'preferred_time' in action['missing_slots']
        assert action['collected_slots']['preferred_time'] == 'tối nay'
        # The hour given next takes the window's half of the day.
        second = _turn(client, headers, '7 giờ')
    assert second['agent_action']['collected_slots']['preferred_time'] == '19:00'
    assert second['agent_action']['status'] == 'confirmation_required'
    assert _proposals(app, session_id) == 0


def test_a_deferral_keeps_the_draft_without_offering_confirmation(tmp_path, understand):
    understand('Đặt bàn', Command('StartGoal', goal='dining_reservation',
                                  slots=(CommandSlot('party_size', '2'),
                                         CommandSlot('preferred_time', '7 giờ tối'))))
    app = _client(tmp_path, DINING)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        body = _turn(client, headers, 'Đặt bàn 2 người lúc 7 giờ tối, để mình xem lại đã')
    assert body['agent_action']['status'] == 'needs_user_input'
    assert body['suggested_action'] is None
    assert body['agent_action']['business_writes'] == 0
    assert app.state.agent_tasks.load(session_id, 'vi') is not None  # the details are kept
    assert _proposals(app, session_id) == 0


def test_a_deferral_while_a_request_waits_neither_confirms_nor_cancels(tmp_path, understand):
    understand('đặt bàn', Command('StartGoal', goal='dining_reservation',
                                  slots=(CommandSlot('party_size', '2'),)))
    app = _client(tmp_path, DINING)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        _turn(client, headers, 'Cho mình đặt bàn 2 người')
        pending = app.state.agent_tasks.load(session_id, 'vi')
        assert pending is not None
        held = _turn(client, headers, 'Khoan đã, để mình xem lại lịch đã')
    assert held['agent_action'] == {'status': 'needs_user_input', 'business_writes': 0}
    after = app.state.agent_tasks.load(session_id, 'vi')
    assert after is not None and after.mode == pending.mode
    assert _proposals(app, session_id) == 0


def test_do_not_disturb_and_a_room_entry_service_are_clarified_not_overridden(tmp_path, understand):
    understand('dọn phòng', Command('StartGoal', goal='housekeeping',
                                    slots=(CommandSlot('room_number', '305'),)))
    app = _client(tmp_path, ('service.room_cleaning',))
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        conflict = _turn(client, headers, 'Phòng 305 đang bật DND nhưng mình muốn dọn phòng ngay')
        assert conflict['agent_action'] == {'status': 'needs_user_input', 'business_writes': 0}
        assert app.state.agent_tasks.load(session_id, 'vi') is None
        plain = _turn(client, headers, 'Phòng 305 dọn phòng giúp mình')
    assert plain['agent_action']['service_mode'] == 'housekeeping'
    assert _proposals(app, session_id) == 0
