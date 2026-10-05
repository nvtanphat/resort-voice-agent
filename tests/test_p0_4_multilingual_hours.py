from __future__ import annotations

from pathlib import Path

import pytest

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.retrieval.engine import retrieve

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("language", "query", "language_marker"),
    [
        ("en", "What time is breakfast?", "Opening hours"),
        ("zh", "早餐几点开始？", "营业时间"),
        ("ko", "아침 식사는 몇 시에 시작해요?", "운영 시간"),
    ],
)
def test_p0_4_breakfast_hours_are_recalled_in_guest_language(
    language: str, query: str, language_marker: str
):
    store = Store(str(ROOT / "data/concierge.sqlite3"))
    result = retrieve(
        store,
        property_id="FURAMA_DANANG",
        language=language,
        query=query,
        effective_date="2026-10-01",
        mode="lexical",
        top_k=5,
    )

    assert result.sources, (language, query, result.answer)
    assert "06:30–10:30" in result.answer
    assert language_marker in result.answer


@pytest.mark.parametrize(
    ("language", "query", "language_marker"),
    [
        ("en", "What time does the pool close?", "Schedule"),
        ("zh", "游泳池几点关门？", "活动时间"),
        ("ko", "수영장은 몇 시에 닫아요?", "일정"),
    ],
)
def test_p0_4_pool_close_hours_are_recalled_in_guest_language(
    language: str, query: str, language_marker: str
):
    store = Store(str(ROOT / "data/concierge.sqlite3"))
    result = retrieve(
        store,
        property_id="FURAMA_DANANG",
        language=language,
        query=query,
        effective_date="2026-10-01",
        mode="lexical",
        top_k=5,
    )

    assert result.sources, (language, query, result.answer)
    assert "06:00–18:30" in result.answer
    assert language_marker in result.answer

@pytest.mark.parametrize(
    ("language", "query", "expected_fragment"),
    [
        ("en", "When does the pool close?", "pool operating hours"),
        ("en", "What time does the spa open?", "spa operating hours"),
        ("zh", "游泳池几点结束？", "游泳池 operating hours"),
        ("ko", "수영장은 몇 시에 닫아요?", "수영장 operating hours"),
    ],
)
def test_p0_4_time_question_variants_normalize_to_hours_facet(
    language: str, query: str, expected_fragment: str
):
    from concierge_kiosk.rag.grounding.relevance import normalized_query

    assert expected_fragment in normalized_query(query, language)
