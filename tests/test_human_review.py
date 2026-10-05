import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datasets" / "evaluation" / "gold"


def _rows():
    rows = []
    for path in sorted(GOLD.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def test_review_corpus_has_gold_labels_and_identity_boundary():
    rows = _rows()
    assert len(rows) >= 500
    assert {row["gold_status"] for row in rows} == {"GOLD"}
    assert {row["language"] for row in rows} == {"vi"}
    assert len({row["scenario_id"] for row in rows}) == len(rows)
    assert all(row["utterance"].strip() and row["source_id"] for row in rows)
    assert all(not {"guest_name", "email", "phone", "passport"}.intersection(row) for row in rows)


def test_review_splits_are_explicit_and_concepts_are_traceable():
    rows = _rows()
    assert {row["split"] for row in rows} == {"dev", "test"}
    assert all(row["concept_key"].strip() for row in rows)
    assert all(row["source_family"].strip() for row in rows)
    assert all(row["targets"]["capability"] for row in rows)


def test_review_gold_routes_cover_safety_and_truth_boundaries():
    routes = {row["expected_route"] for row in _rows()}
    assert {"service", "knowledge", "knowledge_abstain", "emergency", "privacy_guard"} <= routes


def test_review_corpus_is_reproducible_and_unversioned():
    rows = _rows()
    assert all("review_version" not in row for row in rows)
    assert all(row["source_family"] in {
        "independent_core_benchmark", "natural_challenge_curated", "truth_boundary_hard_negative",
    } for row in rows)
