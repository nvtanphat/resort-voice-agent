"""Deterministic grammar: greetings, cancel phrases, slot replies and normalization.

Which service a turn asks for is decided by understanding and measured by
``tools/evaluation/evaluate_command_understanding.py``, not by these tests.
"""
from __future__ import annotations

import pytest

from concierge_kiosk.agent.understanding.intent import normalize_intent_text




def test_voice_room_readback_speaks_digits_without_touching_times_or_quantities():
    from concierge_kiosk.application.service_actions import _voice_numeric_review as review
    assert review('Khăn tắm x5 lúc 15:00 phòng 305', {'room_number': '305', 'quantity': '5'}, 'vi') == (
        'Khăn tắm x5 lúc 15:00 phòng ba không năm')
    assert review('15:00 Room 15', {'room_number': '15'}, 'en') == '15:00 Room one five'
    assert review('Towel x2 Room 305', {'room_number': '305', 'quantity': 2}, 'en') == (
        'Towel 2 items Room three zero five')


def test_voice_service_readback_uses_catalog_name_and_structured_slots():
    from concierge_kiosk.application.service_actions import _canonical_service_review, _voice_numeric_review
    review = _canonical_service_review(
        mode='amenity_delivery', language='vi',
        slots={'room_number': '305', 'quantity': 2},
        fallback_details='khăn tấm phòng ba không năm',
    )
    assert 'Khăn tắm sạch bổ sung' in review
    assert 'khăn tấm phòng ba không năm' not in review
    spoken = _voice_numeric_review(review, {'room_number': '305', 'quantity': 2}, 'vi')
    assert '2 cái' in spoken
    assert 'ba không năm' in spoken


def test_dining_readback_resolves_one_named_restaurant_from_dataset():
    from concierge_kiosk.application.service_actions import (
        _canonical_service_review, _configured_venue_name,
    )
    from concierge_kiosk.core.settings import Settings

    cfg = Settings(environment='test', structured_dataset_dir='datasets')
    query = 'Đặt bàn Don Cipriani cho 4 người lúc 19h'
    assert _configured_venue_name(query, 'vi', cfg) == 'Nhà hàng Ý Don Cipriani'
    review = _canonical_service_review(
        mode='dining_reservation', language='vi',
        slots={'restaurant_name': 'Nhà hàng Ý Don Cipriani', 'party_size': 4,
               'preferred_time': '19:00'},
        fallback_details=query,
    )
    assert 'Nhà hàng Ý Don Cipriani' in review


@pytest.mark.parametrize(('query', 'language', 'room'), [
    ('phòng ba không năm', 'vi', '305'),
    ('phòng ba trăm lẻ năm', 'vi', '305'),
    ('phòng 300 lẻ năm', 'vi', '305'),
    ('three oh five', 'en', '305'),
    ('my room number is 305', 'en', '305'),
])
def test_spoken_room_numbers_are_accepted_as_slot_replies(query, language, room):
    from concierge_kiosk.agent.tools.service_slots import _room_number
    assert _room_number(query, language) == room



def test_room_digits_with_transcript_spacing_are_compacted():
    from concierge_kiosk.agent.tools.service_slots import _room_number
    assert _room_number('12 03', 'vi') == '1203'




@pytest.mark.parametrize(("query", "language"), [
    ("Alo", "vi"), ("a lô", "vi"), ("alô xin chào", "vi"), ("Alo, xin chào!", "vi"),
    ("hello hi", "en"), ("chào bạn nha", "vi"),
])
def test_greetings_do_not_bypass_command_understanding(query, language):
    from concierge_kiosk.agent.understanding.routing import classify_dialogue
    assert classify_dialogue(query, language).branch == "knowledge"




@pytest.mark.parametrize(("query", "language", "branch"), [
    ("xin chào hồ bơi mấy giờ mở cửa", "vi", "knowledge"),
])
def test_greeting_followed_by_a_real_request_is_not_swallowed(query, language, branch):
    from concierge_kiosk.agent.understanding.routing import classify_dialogue
    assert classify_dialogue(query, language).branch == branch


@pytest.mark.parametrize(('query', 'language', 'branch'), [
    ('where is the spa', 'en', 'knowledge'),
])
def test_navigation_requests_wait_for_command_understanding(query, language, branch):
    from concierge_kiosk.agent.understanding.routing import classify_dialogue
    assert classify_dialogue(query, language).branch == branch


@pytest.mark.parametrize(('query', 'language', 'expected'), [
    ('ờ, tôi muốn mang 2 khăn lên phòng 305', 'vi', 'tôi muốn mang 2 khăn lên phòng 305'),
    ('phòng 305 à không, phòng 306', 'vi', 'phòng 306'),
    ('um, i mean send towels to room 305', 'en', 'send towels to room 305'),
])
def test_voice_disfluencies_and_corrections_are_profile_normalized(query, language, expected):
    assert normalize_intent_text(query, language) == expected
