from __future__ import annotations

from pathlib import Path

import pytest

from concierge_kiosk.rag import LocalReranker


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "reranker" / "bge-reranker-v2-m3-int4-ov"
MANIFEST = ROOT / "models" / "reranker" / "bge-reranker-v2-m3-int4-ov.manifest.json"


@pytest.mark.skipif(not MODEL.is_dir() or not MANIFEST.is_file(), reason="local reranker is not provisioned")
def test_pinned_bge_reranker_scores_relevant_candidate_higher() -> None:
    reranker = LocalReranker(str(MODEL), str(MANIFEST))
    scores = reranker.score(
        "Where is breakfast served?",
        ["Breakfast is served in the restaurant.", "The fitness room is on level two."],
    )
    assert scores[0] > scores[1]
