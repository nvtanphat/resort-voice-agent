"""Regression coverage for natural guest service requests from the UX review."""
from concierge_kiosk.agent.understanding.intent import suggest_service_request
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.agent.tools.service_slots import assess_service


def _candidate(query: str, language: str):
    decision = classify_dialogue(query, language)
    suggestion = suggest_service_request(query, language)
    assert decision.branch == 'service'
    assert suggestion is not None
    assessment = assess_service(query, language, suggestion.kind)
    return suggestion, assessment


def test_vietnamese_towel_quantity_is_service_draft():
    suggestion, assessment = _candidate('cho tôi thêm 2 khăn tắm', 'vi')
    assert suggestion.kind == 'facilities'
    assert assessment.mode == 'amenity_delivery'
    assert assessment.slots['quantity'] == 2
    assert assessment.public_state()['department'] == 'housekeeping'
    assert assessment.missing == ('room_number',)


def test_vietnamese_broken_ac_is_maintenance_draft():
    suggestion, assessment = _candidate('phòng tôi máy lạnh hỏng', 'vi')
    assert suggestion.kind == 'facilities'
    assert assessment.mode == 'maintenance'
    assert assessment.public_state()['department'] == 'engineering'
    # A complaint is enough to prepare a draft, but not enough to identify a room.
    assert assessment.missing == ('room_number',)


def test_late_checkout_routes_to_front_office():
    suggestion, assessment = _candidate('tôi muốn trả phòng muộn', 'vi')
    assert suggestion.kind == 'front_office'
    assert assessment.mode == 'late_checkout'
    assert assessment.public_state()['department'] == 'front_office'
    # The reviewed service policy requires both the room and the checkout time.
    assert assessment.missing == ('room_number', 'preferred_time')


def test_spa_massage_extracts_time_and_routes_to_spa():
    suggestion, assessment = _candidate('I want to book a spa massage tomorrow 3pm', 'en')
    assert suggestion.kind == 'facilities'
    assert assessment.mode == 'spa_reservation'
    assert assessment.public_state()['department'] == 'spa'
    assert assessment.slots['preferred_time'] == '3pm'
    assert assessment.missing == ()


def test_airport_taxi_routes_to_transport_not_handoff():
    suggestion, assessment = _candidate('can you get me a taxi to the airport', 'en')
    assert suggestion.kind == 'transport'
    assert assessment.mode == 'transport_request'
    assert assessment.public_state()['department'] == 'transport'
    # The reviewed service policy requires a pickup time before dispatch.
    assert assessment.missing == ('preferred_time',)


def test_information_questions_do_not_become_service_drafts():
    assert classify_dialogue('taxi giá bao nhiêu?', 'vi').branch == 'knowledge'
    assert suggest_service_request('taxi giá bao nhiêu?', 'vi') is None
    assert classify_dialogue('what time is checkout?', 'en').branch == 'knowledge'
    assert suggest_service_request('what time is checkout?', 'en') is None


def test_natural_maintenance_reports_route_to_facilities_service():
    cases = [
        ('Điều hòa phòng tôi bị hỏng', 'vi'),
        ('Phòng tôi nóng quá, máy lạnh không mát', 'vi'),
        ('My AC is not cooling', 'en'),
        ('The air conditioner in my room is not working', 'en'),
        ('房间的空调不制冷了', 'zh'),
        ('객실 에어컨이 시원하지 않아요', 'ko'),
    ]
    for query, language in cases:
        suggestion, assessment = _candidate(query, language)
        assert suggestion.kind == 'facilities'
        assert assessment.mode == 'maintenance'
        assert assessment.public_state()['department'] == 'engineering'


def test_negated_maintenance_commands_remain_non_actionable():
    cases = [
        ('Đừng sửa điều hòa', 'vi'),
        ("Don't fix the AC", 'en'),
        ('不要修空调', 'zh'),
        ('에어컨 수리하지 마세요', 'ko'),
    ]
    for query, language in cases:
        assert classify_dialogue(query, language).branch == 'knowledge'
        assert suggest_service_request(query, language) is None
