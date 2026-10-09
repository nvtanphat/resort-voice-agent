"""Regressions found by natural multi-turn E2E (guest wording written before the run)."""
from __future__ import annotations

import pytest

from concierge_kiosk.agent.tools.numerals import corrected_time
from concierge_kiosk.agent.tools.service_slots import extract_slots, item_and_unit
from concierge_kiosk.agent.understanding.commands import Command, CommandSlot
from concierge_kiosk.agent.understanding.intent_evidence import mentioned_services, service_evidence, turn_defers


@pytest.mark.parametrize('previous,expected', [('tối nay 19:00', '20:00'), ('19:00', '20:00'), ('sáng mai 07:00', '08:00')])
def test_a_corrected_hour_keeps_the_half_of_day_of_a_prefixed_draft_time(previous, expected):
    assert corrected_time('À không, đổi sang 8 giờ giúp mình', 'vi', previous) == expected


def test_an_explicit_daypart_in_the_correction_still_wins():
    assert corrected_time('đổi sang 8 giờ sáng', 'vi', 'tối nay 19:00') == '08:00'


def test_a_compound_noun_is_not_a_competing_service():
    toothbrush = Command('StartGoal', goal='amenity_delivery', slots=(CommandSlot('requested_item', 'bàn chải đánh răng'),))
    assert service_evidence(toothbrush, 'Cho mình 1 bàn chải đánh răng lên phòng 330', 'vi') is not None
    # The bare concept as the whole object still belongs to the other service.
    table = Command('StartGoal', goal='amenity_delivery', slots=(CommandSlot('requested_item', 'bàn'),))
    assert service_evidence(table, 'Mang cho mình một cái bàn lên phòng 330', 'vi') is None


def test_chinese_food_ordering_verb_and_portion_measure_ground_a_food_order():
    assert service_evidence(Command('StartGoal', goal='food_order'), '我在1105房间，想点一份炒饭和一杯橙汁送到房间。', 'zh')
    assert 'food_order' in mentioned_services('请送一份三明治到815房', 'zh')


def test_unaccented_item_phrases_stop_at_boundary_words():
    assert item_and_unit('cho minh them 2 khan tam len phong 808 voi', 'vi') == ('khan tam', None)
    slots = extract_slots('cho minh them 2 khan tam len phong 808 voi', 'vi', 'facilities',
                          mode='amenity_delivery', existing={'requested_item': 'khan tam len'})
    assert slots['requested_item'] == 'khan tam' and slots['room_number'] == '808'


def test_a_deferral_naming_no_service_is_recognised():
    assert turn_defers('À khoan đã, để mình hỏi ý vợ mình đã', 'vi')
    assert not mentioned_services('À khoan đã, để mình hỏi ý vợ mình đã', 'vi')


def test_a_model_conditional_flag_needs_the_guest_to_state_a_condition():
    from concierge_kiosk.agent.understanding.commands import validate_commands
    from concierge_kiosk.domain.service_registry import service_definition

    kinds = frozenset({service_definition('spa_reservation').request_kind})
    plain = validate_commands((Command('StartGoal', goal='spa_reservation', conditional=True),),
                              query='Đặt giúp mình một suất massage lúc 3 giờ chiều mai',
                              enabled_request_kinds=kinds, language='vi')
    assert plain and plain[0].conditional is False
    stated = validate_commands((Command('StartGoal', goal='spa_reservation', conditional=True),),
                               query='Nếu còn chỗ thì đặt giúp mình massage lúc 3 giờ',
                               enabled_request_kinds=kinds, language='vi')
    assert stated and stated[0].conditional is True


def test_a_food_order_keeps_what_the_guest_ordered():
    from concierge_kiosk.agent.understanding.commands import validate_commands
    from concierge_kiosk.domain.service_registry import accepted_slots, service_definition

    assert 'requested_item' in accepted_slots('food_order')
    kept = validate_commands((Command('StartGoal', goal='food_order', slots=(
        CommandSlot('room_number', '1105'), CommandSlot('requested_item', '一份炒饭和一杯橙汁'))),),
        query='我在1105房间，想点一份炒饭和一杯橙汁送到房间。',
        enabled_request_kinds=frozenset({service_definition('food_order').request_kind}), language='zh')
    assert kept and {slot.name: slot.text for slot in kept[0].slots}['requested_item'] == '一份炒饭和一杯橙汁'


def test_a_missing_item_is_taken_from_the_guest_text_and_numeric_units_are_dropped():
    food = extract_slots('我在1105房间，想点一份炒饭和一杯橙汁送到房间。', 'zh', 'dining', mode='food_order')
    assert food['requested_item'] in '我在1105房间，想点一份炒饭和一杯橙汁送到房间。'
    brush = extract_slots('Cho mình 1 bàn chải đánh răng lên phòng 330', 'vi', 'facilities',
                          mode='amenity_delivery', existing={'unit': '1', 'requested_item': 'bàn chải đánh răng'})
    assert 'unit' not in brush and brush['requested_item'] == 'bàn chải đánh răng'


def test_a_service_with_an_optional_item_keeps_its_name_in_the_review():
    from concierge_kiosk.application.service_actions import _canonical_service_review

    review = _canonical_service_review(mode='food_order', language='en',
                                       slots={'room_number': '1105', 'requested_item': 'fried rice'},
                                       fallback_details='')
    assert len(review.splitlines()) == 3 and 'fried rice' in review
