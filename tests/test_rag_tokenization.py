from __future__ import annotations

from concierge_kiosk.rag.common import search_index_text, tokens
from concierge_kiosk.rag.tokenization import segment_terms


def test_profile_selects_whitespace_terms_for_latin_locales():
    assert tokens("send towels to room 305", language="en", limit=None) == [
        "send", "towels", "room", "305"
    ]


def test_profile_selects_trigrams_for_cjk_without_crossing_spaces():
    terms = segment_terms("印度支那咖啡厅 营业时间", "zh")
    assert "印度支" in terms
    assert "度支那" in terms
    assert "营时" not in terms
    assert "业时间" in terms


def test_index_text_uses_the_same_profile_stream_as_queries():
    indexed = search_index_text("印度支那咖啡厅 营业时间", "zh")
    assert "印度支" in indexed
    assert "度支那" in indexed
    assert "营时" not in indexed
