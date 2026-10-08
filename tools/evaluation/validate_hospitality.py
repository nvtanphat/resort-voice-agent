"""Validate the canonical hospitality evaluation and simulation artifacts."""
from __future__ import annotations

from datetime import datetime
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


def _assert_no_pii(row: dict) -> None:
    assert not {"guest_name", "email", "phone", "passport", "real_room_number"}.intersection(row)


def validate() -> dict[str, int | float]:
    base = "evaluation/end_to_end"
    archetypes = _json(f"{base}/simulation/guest_archetypes.json")["archetypes"]
    scenarios = _jsonl(f"{base}/scenarios/production.jsonl")
    journeys = _jsonl(f"{base}/journeys/production.jsonl")
    failures = _json(f"{base}/failures/production.json")["cases"]
    stays = _jsonl(f"{base}/simulation/stay_episodes.jsonl")
    events = _jsonl(f"{base}/simulation/hospitality_events.jsonl")
    summary = _json(f"{base}/simulation/hospitality_summary.json")

    languages = set(supported_languages())
    valid_archetypes = {row["archetype_id"] for row in archetypes}
    assert len(archetypes) >= 30 and len(valid_archetypes) == len(archetypes)
    assert all(row.get("truth_status") == "synthetic_guest_archetype" for row in archetypes)

    scenario_ids = {row["scenario_id"] for row in scenarios}
    assert scenarios and len(scenario_ids) == len(scenarios)
    assert {row["language"] for row in scenarios} == languages
    required_routes = {
        "service", "knowledge", "knowledge_abstain", "status", "clarification",
        "multi_step", "privacy_guard", "non_action", "emergency", "safety_escalation",
    }
    assert required_routes <= {row["expected_route"] for row in scenarios}
    seen_utterances: set[tuple[str, str]] = set()
    for row in scenarios:
        assert row["classification"] in {"synthetic_evaluation", "production_evaluation_synthetic"}
        assert row["must_not_5xx"] is True
        assert row["language"] in languages and row["utterance"].strip()
        key = (row["language"], " ".join(row["utterance"].casefold().split()))
        assert key not in seen_utterances
        seen_utterances.add(key)
        _assert_no_pii(row)
        if row["expected_route"] in {"emergency", "safety_escalation"}:
            assert row["model_policy"] == "forbidden"
        if row["expected_route"] in {"knowledge_abstain", "privacy_guard", "non_action", "emergency", "safety_escalation"}:
            assert row["expected_business_writes_before_confirmation"] == 0
        if row.get("expected_staff_review"):
            assert row["approval_path"] in {"staff", None}

    journey_ids = {row["journey_id"] for row in journeys}
    assert journeys and len(journey_ids) == len(journeys)
    assert {row["language"] for row in journeys} == languages
    assert all(row.get("truth_status") == "synthetic_simulation" for row in journeys)
    assert all(row.get("turns") and all(turn.get("utterance", "").strip() for turn in row["turns"])
               for row in journeys)
    assert all(not {"guest_name", "email", "phone", "passport", "real_room_number"}.intersection(row)
               for row in journeys)

    assert len(failures) >= 16 and len({row["case_id"] for row in failures}) == len(failures)
    assert all(row.get("truth_status") == "synthetic_evaluation" for row in failures)

    stay_ids = {row["stay_id"] for row in stays}
    unit_ids = {row["unit_id"] for row in stays}
    assert len(stays) == summary["stays"] and len(stay_ids) == len(stays)
    assert len(unit_ids) <= 266
    assert all(row["truth_status"] == "synthetic_stay_episode" for row in stays)
    assert all(row["language"] in languages and row["guest_archetype_id"] in valid_archetypes for row in stays)
    assert all(row["unit_id"].startswith(("SIM-ROOM-", "SIM-VILLA-")) for row in stays)
    for row in stays:
        assert datetime.fromisoformat(row["check_in_at"]) < datetime.fromisoformat(row["check_out_at"])
        assert 1 <= row["party_size"] <= 8
        _assert_no_pii(row)

    assert len(events) == summary["events"] and len({row["event_id"] for row in events}) == len(events)
    for row in events:
        assert row["truth_status"] == "synthetic_hospitality_simulation"
        assert row["stay_id"] in stay_ids and row["unit_id"].startswith(("SIM-ROOM-", "SIM-VILLA-"))
        assert row["guest_archetype_id"] in valid_archetypes and row["language"] in languages
        assert 0 <= row["occupancy_pct"] <= 100 and 0.7 <= row["staffing_pressure"] <= 1.6
        _assert_no_pii(row)
        manual = row["manual_relay"]
        agent = row["agent_governed"]
        experience = row["guest_experience"]
        assert manual["front_desk_touch"] == 1
        assert manual["dispatch_delay_min"] >= 0 and agent["dispatch_delay_min"] >= 0
        assert agent["front_desk_touch"] in (0, 1)
        if agent["autonomous_dispatch"]:
            assert row["approval"] == "none" and agent["front_desk_touch"] == 0
        assert 1 <= experience["simulated_satisfaction_1_to_5"] <= 5
        assert 1 <= experience["effort_turns"] <= 4

    manual = summary["manual_relay"]
    agent = summary["agent_governed"]
    experience = summary["guest_experience"]
    effect = summary["modeled_product_effect"]
    assert manual["front_desk_touches"] == len(events)
    assert 0 < agent["front_desk_touches"] < manual["front_desk_touches"]
    assert effect["front_desk_touches_avoided"] == manual["front_desk_touches"] - agent["front_desk_touches"]
    assert 0.55 <= manual["sla_met_rate"] <= 0.85
    assert 0.72 <= agent["sla_met_rate"] <= 0.93
    assert 0.12 <= experience["recovery_needed_rate"] <= 0.35
    assert 0.60 <= experience["recovery_success_rate"] <= 0.90
    assert 3.6 <= experience["simulated_satisfaction_avg"] <= 4.5
    assert 0.30 <= effect["front_desk_touch_reduction_rate"] <= 0.60
    assert 1.0 <= effect["median_dispatch_minutes_saved"] <= 5.0
    assert "not measured Furama" in summary["truth_boundary"]

    return {
        "archetypes": len(archetypes),
        "scenarios": len(scenarios),
        "journeys": len(journeys),
        "failure_cases": len(failures),
        "stays": len(stays),
        "events": len(events),
        "agent_sla_met_rate": agent["sla_met_rate"],
        "recovery_needed_rate": experience["recovery_needed_rate"],
        "front_desk_touch_reduction_rate": effect["front_desk_touch_reduction_rate"],
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
