from __future__ import annotations

from tools.evaluation.evaluate_vector_semantic import _percentile


def test_semantic_benchmark_percentile_is_deterministic():
    assert _percentile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.0
    assert _percentile([], 0.95) is None
