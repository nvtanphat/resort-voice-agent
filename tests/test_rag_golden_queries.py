from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.common import LocalEmbedder
from concierge_kiosk.rag.retrieval.engine import retrieve


class RagGoldenQueryTests(unittest.TestCase):
    def test_cafe_hours_rank_correct_fact_for_all_languages_in_hybrid_mode(self):
        store = Store(str(ROOT / "data/concierge.sqlite3"))
        embedder = LocalEmbedder(
            str(ROOT / "models/embeddings/hash-multilingual"),
            str(ROOT / "models/embeddings/hash-multilingual.manifest.json"),
        )
        cases = {
            "en": "Cafe Indochine opening hours",
            "vi": "Café Indochine mở mấy giờ",
            "ko": "카페 인도차이나 영업시간",
            "zh": "印度支那咖啡厅 营业时间",
        }
        for language, query in cases.items():
            result = retrieve(
                store,
                property_id="FURAMA_DANANG",
                language=language,
                query=query,
                effective_date="2026-10-01",
                mode="hybrid",
                top_k=5,
                embedder=embedder,
            )
            self.assertIn(result.mode, {"hybrid", "lexical"}, language)
            self.assertTrue(result.sources, language)
            self.assertTrue(all(src["source_id"] == "kb_restaurant_cafe_indochine" for src in result.sources), language)
            evidence = "\n".join(src["content"] for src in result.sources)
            for window in ("06:30–10:30", "11:30–14:00", "18:00–22:00", "18:30–22:00"):
                self.assertIn(window, evidence, (language, window))


def test_p0_3_wifi_single_term_is_bound_to_wifi_fact():
    store = Store(str(ROOT / "data/concierge.sqlite3"))
    cases = {
        "vi": ("wifi", ("wi-fi", "wifi")),
        "en": ("wifi", ("wifi",)),
    }
    for language, (query, markers) in cases.items():
        result = retrieve(
            store,
            property_id="FURAMA_DANANG",
            language=language,
            query=query,
            effective_date="2026-10-01",
            mode="lexical",
            top_k=5,
        )
        assert result.sources, language
        text = "\n".join(source["content"] for source in result.sources).casefold()
        assert any(marker in text for marker in markers), (language, text)
        assert "198" not in result.answer


def test_p0_3_vietnamese_late_checkout_does_not_use_ceiling_height():
    store = Store(str(ROOT / "data/concierge.sqlite3"))
    result = retrieve(
        store,
        property_id="FURAMA_DANANG",
        language="vi",
        query="Tôi có thể ở lại phòng đến 2 giờ chiều không?",
        effective_date="2026-10-01",
        mode="lexical",
        top_k=5,
    )
    text = "\n".join(source["content"] for source in result.sources).casefold()
    assert "chiều cao trần" not in text
    assert not result.sources or "trả phòng" in text or "18:00" in text


def test_p0_3_broken_air_conditioner_abstains_instead_of_using_gym_equipment():
    store = Store(str(ROOT / "data/concierge.sqlite3"))
    result = retrieve(
        store,
        property_id="FURAMA_DANANG",
        language="vi",
        query="Điều hòa phòng tôi bị hỏng",
        effective_date="2026-10-01",
        mode="lexical",
        top_k=5,
    )
    text = "\n".join(source["content"] for source in result.sources).casefold()
    assert "nautilus" not in text
    assert "thiết bị tập tim mạch" not in text
    assert not result.sources or "điều hòa" in text or "máy lạnh" in text

if __name__ == "__main__":
    unittest.main()
