"""Slot extraction and service policy for understood service goals.

Understanding (commands or the embedding fallback) names the service; these
tests check what the governed workflow derives once the goal is known: slots
parsed from the guest text, the owning department and the slots still missing.
"""
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.agent.tools.service_slots import assess_service
from concierge_kiosk.domain.service_registry import service_definition


def _assess(query: str, language: str, mode: str):
    definition = service_definition(mode)
    assert definition is not None
    return assess_service(query, language, definition.request_kind, mode=mode)


def test_vietnamese_towel_quantity_is_service_draft():
    assessment = _assess('cho tôi thêm 2 khăn tắm', 'vi', 'amenity_delivery')
    assert assessment.slots['quantity'] == 2
    assert assessment.public_state()['department'] == 'housekeeping'
    assert assessment.missing == ('room_number',)


def test_vietnamese_broken_ac_is_maintenance_draft():
    assessment = _assess('phòng tôi máy lạnh hỏng', 'vi', 'maintenance')
    assert assessment.public_state()['department'] == 'engineering'
    # A complaint is enough to prepare a draft, but not enough to identify a room.
    assert assessment.missing == ('room_number',)


def test_late_checkout_routes_to_front_office():
    assessment = _assess('tôi muốn trả phòng muộn', 'vi', 'late_checkout')
    assert assessment.public_state()['department'] == 'front_office'
    # The reviewed service policy requires both the room and the checkout time.
    assert assessment.missing == ('room_number', 'preferred_time')


def test_spa_massage_extracts_time_and_routes_to_spa():
    assessment = _assess('I want to book a spa massage tomorrow 3pm', 'en', 'spa_reservation')
    assert assessment.public_state()['department'] == 'spa'
    assert assessment.slots['preferred_time'] == '3pm'
    assert assessment.missing == ()


def test_airport_taxi_routes_to_transport_not_handoff():
    assessment = _assess('can you get me a taxi to the airport', 'en', 'transport_request')
    assert assessment.public_state()['department'] == 'transport'
    # The reviewed service policy requires a pickup time before dispatch.
    assert assessment.missing == ('preferred_time',)


def test_cancel_and_hours_phrasings_wait_for_command_understanding():
    assert classify_dialogue(
        'I do not need the In-Room Dining (Room Service) I asked for earlier anymore. '
        'Could you cancel it?', 'en').branch == 'knowledge'
    overview = 'Could you give me an overview of In-Room Dining (Room Service), including the hours?'
    assert classify_dialogue(overview, 'en').branch == 'knowledge'
