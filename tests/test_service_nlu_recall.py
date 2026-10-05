"""Imperative service requests must route to service; how-to questions must not."""
from __future__ import annotations

import pytest

from concierge_kiosk.agent.understanding.intent import normalize_intent_text, suggest_service_request
from concierge_kiosk.domain.service_registry import resolve_service_code


@pytest.mark.parametrize(("query", "language", "service"), [
    ("send two bath towels to room 305", "en", "amenity_delivery"),
    ("send four bottles of water to room 706", "en", "amenity_delivery"),
    ("send one extra pillow to room 822", "en", "amenity_delivery"),
    ("the sink in room 822 is leaking", "en", "maintenance"),
    ("the light in room 305 will not turn on", "en", "maintenance"),
    ("the TV in room 706 has no signal", "en", "maintenance"),
    ("Please adjust the thermostat in room 305", "en", "maintenance"),
    ("The room is too hot, please fix the AC", "en", "maintenance"),
    ("cho phòng 822 thêm 1 cái gối", "vi", "amenity_delivery"),
    ("bồn rửa phòng 822 đang rò nước", "vi", "maintenance"),
    ("Làm ơn chỉnh điều hòa phòng 305 giúp tôi", "vi", "maintenance"),
    ("Phòng tôi nóng quá, máy lạnh không mát", "vi", "maintenance"),
    ("822호에 베개 하나 더 보내 주세요", "ko", "amenity_delivery"),
    ("305호 조명이 켜지지 않아요", "ko", "maintenance"),
    ("请送四瓶水到706房", "zh", "amenity_delivery"),
    ("822房的马桶堵了", "zh", "maintenance"),
])
def test_imperative_service_request_resolves_concrete_service(query, language, service):
    suggestion = suggest_service_request(query, language)
    assert suggestion is not None and suggestion.kind == "facilities"
    assert resolve_service_code(normalize_intent_text(query), language, suggestion.kind) == service


@pytest.mark.parametrize(("query", "language"), [
    ("Please tell me how to use the air conditioner", "en"),
    ("please explain the spa", "en"),
    ("I do not need towels", "en"),
    ("do not send towels", "en"),
    ("Làm ơn cho tôi biết cách dùng điều hòa", "vi"),
    ("请告诉我空调怎么用", "zh"),
    ("请不要送毛巾", "zh"),
    ("에어컨 사용법을 알려 주세요", "ko"),
    ("수건 보내지 마세요", "ko"),
])
def test_information_or_negated_request_is_not_an_action(query, language):
    assert suggest_service_request(query, language) is None


@pytest.mark.parametrize(('query', 'language'), [
    ('I need medical centre assistance', 'en'),
])
def test_catalog_only_staff_services_use_the_governed_human_route(query, language):
    suggestion = suggest_service_request(query, language)
    assert suggestion is not None and suggestion.kind == 'human'


@pytest.mark.parametrize(("query", "language"), [
    ("What is the cancellation policy?", "en"),
    ("Can I cancel my dinner booking tomorrow?", "en"),
    ("Chính sách hủy phòng thế nào?", "vi"),
])
def test_questions_containing_cancel_words_do_not_cancel_a_pending_draft(query, language):
    from concierge_kiosk.agent.tools.service_slots import is_cancel_pending
    assert is_cancel_pending(query, language) is False


@pytest.mark.parametrize(("query", "language"), [
    ("cancel", "en"), ("please cancel", "en"), ("never mind", "en"),
    ("hủy", "vi"), ("hủy ạ", "vi"), ("vui lòng hủy", "vi"),
    ("取消", "zh"), ("请取消", "zh"), ("취소", "ko"), ("취소해 주세요", "ko"),
])
def test_whole_utterance_cancel_commands_still_cancel(query, language):
    from concierge_kiosk.agent.tools.service_slots import is_cancel_pending
    assert is_cancel_pending(query, language) is True


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


@pytest.mark.parametrize(('query', 'language', 'room'), [
    ('phòng ba không năm', 'vi', '305'),
    ('phòng ba trăm lẻ năm', 'vi', '305'),
    ('phòng 300 lẻ năm', 'vi', '305'),
    ('three oh five', 'en', '305'),
    ('my room number is 305', 'en', '305'),
])
def test_spoken_room_numbers_are_accepted_as_slot_replies(query, language, room):
    from concierge_kiosk.agent.tools.service_slots import _room_number, looks_like_slot_reply
    assert _room_number(query, language) == room
    assert looks_like_slot_reply(query, language, ('room_number',)) is True


@pytest.mark.parametrize("query", [
    "spa mở cửa đến mấy giờ", "spa đóng cửa lúc mấy giờ", "hồ bơi mở đến mấy giờ",
    "nhà hàng mở cửa đến khi nào",
])
def test_vietnamese_until_what_time_questions_are_opening_hours_queries(query):
    from concierge_kiosk.rag.relevance import is_opening_hours_query, normalized_query
    assert is_opening_hours_query(query, "vi")
    assert normalized_query(query, "vi").endswith("operating hours")


@pytest.mark.parametrize(("query", "language"), [
    ("Alo", "vi"), ("a lô", "vi"), ("alô xin chào", "vi"), ("Alo, xin chào!", "vi"),
    ("hello hi", "en"), ("chào bạn nha", "vi"),
])
def test_greetings_including_chained_ones_are_routed_as_greeting(query, language):
    from concierge_kiosk.agent.understanding.routing import classify_dialogue
    assert classify_dialogue(query, language).branch == "greeting"


@pytest.mark.parametrize(("query", "language", "branch"), [
    ("xin chào hồ bơi mấy giờ mở cửa", "vi", "knowledge"),
    ("alo cho tôi 2 khăn tắm phòng 305", "vi", "service"),
])
def test_greeting_followed_by_a_real_request_is_not_swallowed(query, language, branch):
    from concierge_kiosk.agent.understanding.routing import classify_dialogue
    assert classify_dialogue(query, language).branch == branch


@pytest.mark.parametrize(('query', 'language', 'branch'), [
    ('két sắt phòng 510 không mở được', 'vi', 'handoff'),
    ('gia đình tôi 5 người muốn ăn tối lúc 18:30', 'vi', 'service'),
    ('mang một tô phở lên phòng 1205', 'vi', 'service'),
    ('phòng bên cạnh ồn quá', 'vi', 'handoff'),
    ('tôi cần báo thức lúc 5 giờ sáng', 'vi', 'service'),
    ('tôi cần hỗ trợ hành lý khi trả phòng', 'vi', 'handoff'),
    ('đặt giúp tôi tour Hội An', 'vi', 'service'),
    ('where is the spa', 'en', 'navigation'),
    ('room next door is noisy', 'en', 'handoff'),
    ('the safe in room 510 will not open and I am leaving for the airport', 'en', 'handoff'),
])
def test_natural_service_and_navigation_requests_do_not_fall_into_knowledge(query, language, branch):
    from concierge_kiosk.agent.understanding.routing import classify_dialogue
    assert classify_dialogue(query, language).branch == branch


@pytest.mark.parametrize(('query', 'language', 'expected'), [
    ('ờ, tôi muốn mang 2 khăn lên phòng 305', 'vi', 'tôi muốn mang 2 khăn lên phòng 305'),
    ('phòng 305 à không, phòng 306', 'vi', 'phòng 306'),
    ('um, i mean send towels to room 305', 'en', 'send towels to room 305'),
])
def test_voice_disfluencies_and_corrections_are_profile_normalized(query, language, expected):
    assert normalize_intent_text(query, language) == expected
