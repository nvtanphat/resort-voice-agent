from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from concierge_kiosk.agent.tools import planning
from concierge_kiosk.core.domain_profile import default_domain_profile_binding, load_domain_profile
from concierge_kiosk.core.structured_loader import load_structured_dataset
from concierge_kiosk.core.dataset_layout import dataset_path
from concierge_kiosk.domain.entity_resolver import property_entity_matches
from concierge_kiosk.rag.grounding import relevance


def _payload() -> dict:
    path, _ = default_domain_profile_binding()
    return json.loads(Path(path).read_text(encoding="utf-8"))




def test_property_entity_names_are_resolved_from_property_alias_data():
    dataset = load_structured_dataset(dataset_path(""))
    assert property_entity_matches("Tell me about Don Cipriani", "en", dataset.aliases) == (
        "restaurant.don_cipriani",
    )
    assert property_entity_matches("Where is V-Senses Spa?", "en", dataset.aliases) == (
        "spa.v_senses_wellness",
    )
    assert property_entity_matches("I need beach towels", "en", dataset.aliases) == (
        "service.beach_towels",
    )
