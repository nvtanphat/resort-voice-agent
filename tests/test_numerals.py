from __future__ import annotations

import json
from pathlib import Path

from concierge_kiosk.agent.tools.numerals import normalize_number_words, preferred_time


def _cases() -> list[dict]:
    path = Path(__file__).parent / 'data' / 'numerals.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def test_number_and_clock_examples_are_profile_data_driven():
    for case in _cases():
        if 'expected' in case:
            assert normalize_number_words(case['input'], case['language']) == case['expected']
        if 'expected_time' in case:
            assert preferred_time(case['input'], case['language']) == case['expected_time']


def test_number_parser_keeps_cardinal_tens_distinct_from_digit_sequences():
    assert normalize_number_words('two towels', 'en') == '2 towels'
    assert normalize_number_words('ten five towels', 'en') == '15 towels'
    assert normalize_number_words('three hundred five', 'en') == '305'
