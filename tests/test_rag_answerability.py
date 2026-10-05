from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.rag.grounding.relevance import answerable


def test_price_question_rejects_minibar_amenity_fact():
    source = {
        "fact_type": "amenity",
        "context_text": "guest room minibar with tea and coffee",
        "title": "Minibar",
        "content": "The room includes a minibar.",
    }
    assert not answerable(
        {"facets": ("price",), "fact_types": ("price_vnd",)},
        "how much is the minibar",
        source,
        language="en",
    )


def test_price_question_accepts_price_fact_for_the_named_item():
    source = {
        "fact_type": "price_vnd",
        "context_text": "minibar Coca-Cola price",
        "title": "Minibar Coca-Cola",
        "content": "Coca-Cola: 50,000 VND.",
    }
    assert answerable(
        {"facets": ("price",), "fact_types": ("price_vnd",)},
        "how much is Coca-Cola in the minibar",
        source,
        language="en",
    )


def test_unrelated_transport_fact_cannot_answer_charging_station_question():
    source = {
        "fact_type": "service_window",
        "context_text": "concierge can arrange car rental",
        "title": "Transportation",
        "content": "Car rental can be arranged through Concierge.",
    }
    assert not answerable(
        {"facets": (), "fact_types": ()},
        "what type of electric vehicle charging station is available",
        source,
        language="en",
    )
