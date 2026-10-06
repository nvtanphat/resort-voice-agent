from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from concierge_kiosk.agent.understanding.intent import emergency_response
from concierge_kiosk.agent.tools.service_slots import assess_service
from tools.evaluation.validate_production_evaluation import validate

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'datasets' / 'evaluation' / 'end_to_end'


def _rows():
    return [json.loads(line) for line in (OUT / 'scenarios' / 'production.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]


def test_production_dataset_validator_and_language_balance():
    result = validate()
    assert result['scenarios'] == len(_rows())
    assert result['journeys'] == 32
    assert result['failure_cases'] >= 16
    assert result['simulation_events'] >= 7000
    assert set(Counter(row['language'] for row in _rows())) == {'vi', 'en', 'ko', 'zh'}


def test_production_natural_towel_requests_preserve_quantity_and_room_across_languages():
    cases = [
        ('vi', 'Mang giúp tôi 2 khăn tắm lên phòng 305'),
        ('en', 'Please send two bath towels to room 305'),
        ('ko', '305호에 목욕 수건 두 장 보내 주세요'),
        ('zh', '请送两条浴巾到305房'),
    ]
    for language, query in cases:
        assessment = assess_service(query, language, 'facilities', mode='amenity_delivery')
        assert assessment.slots['room_number'] == '305'
        assert assessment.slots['quantity'] == 2


def test_production_natural_late_checkout_keeps_staff_gate_slots_across_languages():
    cases = [
        ('vi', 'Phòng 305 có thể trả lúc 14:00 không?'),
        ('en', 'Can room 305 check out at 14:00?'),
        ('ko', '305호 체크아웃을 14시로 늦출 수 있나요?'),
        ('zh', '305房可以延迟到14:00退房吗？'),
    ]
    for language, query in cases:
        assessment = assess_service(query, language, 'front_office', mode='late_checkout')
        assert assessment.slots['room_number'] == '305'
        assert assessment.slots['preferred_time'] == '14:00'


def test_production_natural_emergency_phrases_preempt_model_in_all_languages():
    cases = [
        ('vi', 'Tôi ngửi thấy mùi khói ngoài hành lang'),
        ('en', "I can't find my son anywhere"),
        ('ko', '아이를 찾을 수 없어요'),
        ('zh', '空调附近有烧焦味'),
    ]
    for language, query in cases:
        assert emergency_response(query, language)


def test_production_evaluation_is_explicitly_synthetic():
    failures = json.loads((OUT / 'failures' / 'production.json').read_text(encoding='utf-8'))
    assert failures['classification'] == 'production_evaluation_synthetic'
    assert all(case['truth_status'] == 'synthetic_evaluation' for case in failures['cases'])
