"""A corrected clock time keeps the daypart of the time it replaces.

"7 giờ tối, à không, 8 giờ" supersedes the first time, but the guest never said that the
meal moved to the morning.  The rule is grammar (daypart words come from the profile), not a
phrase list, so other wordings and other values must behave the same way.
"""
from __future__ import annotations

import pytest

from concierge_kiosk.agent.tools.numerals import _preferred_time_core, preferred_time


@pytest.mark.parametrize("text,expected", [
    # the corrected hour inherits the superseded daypart
    ("lúc 7 giờ tối, à không, 8 giờ", "20:00"),
    ("lúc 6 giờ chiều, ý tôi là 7 giờ", "19:00"),
    ("lúc 7 giờ sáng, sửa lại là 9 giờ", "09:00"),
    ("lúc 7 giờ sáng, à không, 10 giờ", "10:00"),
    # a new explicit daypart wins over the superseded one
    ("lúc 7 giờ sáng, à không, 8 giờ tối", "20:00"),
    # no correction: unchanged behaviour
    ("lúc 8 giờ tối", "20:00"),
    ("lúc 8 giờ", "08:00"),
    ("lúc 7 giờ tối", "19:00"),
    # an explicit 24-hour value is never re-interpreted
    ("lúc 7 giờ tối, à không, 14:30", "14:30"),
])
def test_corrected_time_keeps_the_superseded_daypart(text: str, expected: str):
    assert preferred_time(text, "vi") == expected


def test_conflicting_dayparts_before_the_correction_are_not_guessed():
    # Two different dayparts were superseded: nothing safe to inherit, keep the bare clock.
    assert preferred_time("sáng nay 7 giờ sáng hay 6 giờ chiều, à không, 8 giờ", "vi") == "08:00"


def test_a_daypart_that_is_not_attached_to_a_clock_time_is_not_inherited():
    assert preferred_time("sáng mai nhé, lúc 9 giờ", "vi") == "09:00"


@pytest.mark.parametrize("language", ["en", "zh", "ko"])
def test_languages_without_daypart_grammar_are_unchanged(language: str):
    # No profile grammar for dayparts: behaviour must equal the plain parser (no crash, no guess).
    text = "7 pm, I mean 8"
    assert preferred_time(text, language) == _preferred_time_core(text, language)


@pytest.mark.parametrize("text,previous,expected", [
    # a correction in a later turn keeps the half of the day of the open draft
    ("đổi thành 8 giờ", "19:00", "20:00"),
    ("đổi thành 9 giờ", "07:00", "09:00"),
    # an explicit daypart, a written minute or a 24-hour clock is taken as stated
    ("đổi thành 8 giờ sáng", "19:00", "08:00"),
    ("đổi thành 8:30", "19:00", "08:30"),
    ("đổi thành 20 giờ", "07:00", "20:00"),
    # nothing to inherit from
    ("đổi thành 8 giờ", None, "08:00"),
])
def test_a_later_correction_keeps_the_draft_daypart(text, previous, expected):
    from concierge_kiosk.agent.tools.numerals import corrected_time

    assert corrected_time(text, "vi", previous) == expected


def test_a_multi_word_minus_marker_never_matches_without_an_hour():
    # "to" is one of several "minus" markers ("ten to eight"); an ungrouped
    # alternation once matched a bare "to 8" with no hour and raised TypeError.
    from concierge_kiosk.agent.tools.numerals import preferred_time

    assert preferred_time("change it to 8", "en") is None
    assert preferred_time("8h to 10", "en") == "07:50"
