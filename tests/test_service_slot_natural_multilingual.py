from concierge_kiosk.agent.tools.service_slots import extract_slots


def test_natural_towel_quantity_and_room_slots_in_all_supported_languages():
    cases = [
        ('vi', 'Mang 2 khăn tắm lên phòng 305'),
        ('en', 'Please bring 2 bath towels to room 305'),
        ('zh', '请送2条毛巾到305房间'),
        ('ko', '305호에 수건 2장 가져다 주세요'),
    ]
    for language, query in cases:
        slots = extract_slots(query, language, 'facilities', mode='amenity_delivery')
        assert slots['room_number'] == '305'
        assert slots['quantity'] == 2


def test_korean_number_before_counter_form_is_supported():
    slots = extract_slots('305호에 2장 수건 가져다 주세요', 'ko', 'facilities', mode='amenity_delivery')
    assert slots == {'room_number': '305', 'quantity': 2}


def test_written_number_towel_phrases_used_by_production_are_parsed():
    cases = [
        ('en', 'Please send two bath towels to room 305'),
        ('vi', 'Mang giúp tôi hai khăn tắm lên phòng 305'),
        ('ko', '305호에 목욕 수건 두 장 보내 주세요'),
        ('zh', '请送两条浴巾到305房'),
    ]
    for language, query in cases:
        slots = extract_slots(query, language, 'facilities', mode='amenity_delivery')
        assert slots['room_number'] == '305'
        assert slots['quantity'] == 2


def test_late_checkout_clock_slots_survive_cjk_grammar():
    cases = [
        ('ko', '305호 체크아웃을 14시로 늦출 수 있나요?'),
        ('zh', '305房可以延迟到14:00退房吗？'),
    ]
    for language, query in cases:
        slots = extract_slots(query, language, 'front_office', mode='late_checkout')
        assert slots['room_number'] == '305'
        assert slots['preferred_time'] == '14:00'
