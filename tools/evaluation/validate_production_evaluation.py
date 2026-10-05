"""Validate the checked-in end-to-end production evaluation corpus."""
from __future__ import annotations

from collections import Counter
import json
import sys

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.core.dataset_layout import dataset_path
from concierge_kiosk.core.domain_profile import supported_languages


def _json(relative: str) -> dict:
    return json.loads(dataset_path(relative).read_text(encoding="utf-8"))


def _jsonl(relative: str) -> list[dict]:
    return [json.loads(line) for line in dataset_path(relative).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def validate() -> dict[str, int]:
    scenarios = _jsonl("evaluation/end_to_end/scenarios/production.jsonl")
    journeys = _jsonl("evaluation/end_to_end/journeys/production.jsonl")
    failures = _json("evaluation/end_to_end/failures/production.json")["cases"]
    config = _json("evaluation/end_to_end/simulation/production_config.json")
    events = _jsonl("evaluation/end_to_end/simulation/counterfactual_events.jsonl")
    summary = _json("evaluation/end_to_end/simulation/counterfactual_summary.json")

    languages = set(supported_languages())
    assert scenarios and set(Counter(row["language"] for row in scenarios)) == languages
    assert len({row["scenario_id"] for row in scenarios}) == len(scenarios)
    seen: set[tuple[str, str]] = set()
    for row in scenarios:
        assert row["classification"] in {"synthetic_evaluation", "production_evaluation_synthetic"}
        assert row["must_not_5xx"] is True
        key = (row["language"], " ".join(row["utterance"].casefold().split()))
        assert key not in seen
        seen.add(key)
    assert len(journeys) and len({row["journey_id"] for row in journeys}) == len(journeys)
    assert set(row["language"] for row in journeys) == languages
    assert all(row.get("truth_status") == "synthetic_simulation" for row in journeys)

    assert len(failures) >= 16 and len({row["case_id"] for row in failures}) == len(failures)
    assert all(row.get("truth_status") == "synthetic_evaluation" for row in failures)

    assert config["classification"] == "synthetic_simulation"
    regimes = config.get("regimes", [])
    assert len(regimes) >= 3 and len({row["id"] for row in regimes}) == len(regimes)
    assert all(row.get("truth_status") == "synthetic_simulation"
               for row in config.get("extended_department_capacity", []))

    assert len(events) == summary["events"] and len(events) >= 7000
    assert len({row["event_id"] for row in events}) == len(events)
    for row in events:
        assert row["truth_status"] == "synthetic_counterfactual_simulation"
        assert row["manual"]["front_desk_touch"] == 1
        assert row["agent"]["front_desk_touch"] in (0, 1)
        if row["agent"]["autonomous_dispatch"]:
            assert row["approval"] == "none" and row["agent"]["front_desk_touch"] == 0
        assert row["manual"]["dispatch_delay_min"] >= 0
        assert row["agent"]["dispatch_delay_min"] >= 0
        assert 0 <= row["occupancy_pct"] <= 100

    manual = summary["manual_relay"]
    agent = summary["agent_governed"]
    effect = summary["modeled_product_effect"]
    assert manual["front_desk_touches"] == len(events)
    assert 0 < agent["front_desk_touches"] < manual["front_desk_touches"]
    assert effect["front_desk_touches_avoided"] == manual["front_desk_touches"] - agent["front_desk_touches"]
    assert effect["dispatch_p50_minutes_saved"] > 0
    assert "not measured Furama" not in summary.get("interpretation_guard", "").casefold()

    forbidden = {"guest_name", "email", "phone", "passport", "real_room_number"}
    assert all(not forbidden.intersection(row) for row in events)
    return {
        "scenarios": len(scenarios),
        "journeys": len(journeys),
        "failure_cases": len(failures),
        "simulation_events": len(events),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
