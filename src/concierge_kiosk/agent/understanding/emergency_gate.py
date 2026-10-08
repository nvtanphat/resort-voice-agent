"""Semantic emergency detection gate (layer A, second tier).

Architecture
- Tier 1: deterministic grammar in ``classify_dialogue`` (instant, high precision).
- Tier 2 (this module): a small logistic-regression classifier over the same
  multilingual embedding (bge-m3) used everywhere else.  It is trained from
  the reviewed training examples (positive = ``emergency`` examples, negative
  = every other example), so a new way of describing an emergency is handled
  by adding reviewed examples, never by adding phrases.  No SLM is involved:
  the gate costs one cached query embedding plus one dot product, and it keeps
  working when the SLM is down.

Decision (probability p that the turn is an emergency)
- p >= emergency_min_prob      -> RouteDecision('emergency')        full escalation
- p >= emergency_review_prob   -> RouteDecision('emergency_check')  ask the guest first
- otherwise                    -> None

A false 'emergency_check' costs the guest one tap; a missed emergency can cost
far more, so the review threshold is calibrated for recall
(``tools/nlu/calibrate_emergency_gate.py``).
"""
from __future__ import annotations

import threading
from typing import Any, Protocol, Sequence

from concierge_kiosk.agent.understanding.routing import RouteDecision


def emergency_confirm_question(language: str) -> str:
    """Multilingual confirmation question for emergency_check review zone."""
    from concierge_kiosk.i18n import text as i18n_text
    return i18n_text("emergency.confirm_question", language)


class EmergencyClassifier(Protocol):
    def probability(self, vector: Sequence[float]) -> float: ...


class LogisticClassifier:
    """L2-regularized logistic regression with balanced class weights (numpy)."""

    def __init__(self, weights: Any, bias: float) -> None:
        self.weights = weights
        self.bias = float(bias)

    @staticmethod
    def _unit_rows(matrix: Any) -> Any:
        import numpy as np

        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(norms > 0, norms, 1.0)

    @classmethod
    def fit(cls, vectors: Sequence[Sequence[float]], labels: Sequence[int], *, l2: float,
            iterations: int = 300, learning_rate: float = 0.1) -> "LogisticClassifier":
        """Full-batch Adam; deterministic (zero init, no sampling)."""
        import numpy as np

        x = cls._unit_rows(np.asarray(vectors, dtype=np.float64))
        y = np.asarray(labels, dtype=np.float64)
        positives = float(y.sum())
        negatives = float(len(y) - positives)
        if positives == 0 or negatives == 0:
            raise ValueError("classifier needs positive and negative examples")
        sample_weight = np.where(y == 1.0, 0.5 / positives, 0.5 / negatives)
        w = np.zeros(x.shape[1])
        b = 0.0
        m_w, v_w = np.zeros_like(w), np.zeros_like(w)
        m_b = v_b = 0.0
        beta1, beta2, eps = 0.9, 0.999, 1e-8
        for step in range(1, iterations + 1):
            z = np.clip(x @ w + b, -30.0, 30.0)
            p = 1.0 / (1.0 + np.exp(-z))
            residual = (p - y) * sample_weight
            grad_w = x.T @ residual + l2 * w
            grad_b = float(residual.sum())
            m_w = beta1 * m_w + (1 - beta1) * grad_w
            v_w = beta2 * v_w + (1 - beta2) * grad_w * grad_w
            m_b = beta1 * m_b + (1 - beta1) * grad_b
            v_b = beta2 * v_b + (1 - beta2) * grad_b * grad_b
            c1, c2 = 1 - beta1 ** step, 1 - beta2 ** step
            w -= learning_rate * (m_w / c1) / (np.sqrt(v_w / c2) + eps)
            b -= learning_rate * (m_b / c1) / (np.sqrt(v_b / c2) + eps)
        return cls(w, b)

    def probability(self, vector: Sequence[float]) -> float:
        import numpy as np

        x = np.asarray(vector, dtype=np.float64)
        norm = float(np.linalg.norm(x))
        z = float(np.clip((x / norm if norm > 0 else x) @ self.weights + self.bias, -30.0, 30.0))
        return 1.0 / (1.0 + float(np.exp(-z)))


class EmergencyGate:
    """Logistic emergency classifier trained from reviewed examples."""

    def __init__(self, service_selector: Any = None, *, min_prob: float = 0.9,
                 review_prob: float = 0.3, l2: float = 0.001,
                 classifier: EmergencyClassifier | None = None) -> None:
        if not 0.0 <= review_prob <= min_prob <= 1.0:
            raise ValueError("emergency thresholds need 0 <= review_prob <= min_prob <= 1")
        self.service_selector = service_selector
        self.min_prob = float(min_prob)
        self.review_prob = float(review_prob)
        self.l2 = float(l2)
        self._classifier = classifier
        self._lock = threading.Lock()

    def _ensure_classifier(self) -> EmergencyClassifier | None:
        if self._classifier is not None:
            return self._classifier
        selector = self.service_selector
        if selector is None or not getattr(selector, "examples", None):
            return None
        # Inside a guest turn an unbuilt index is never built (see
        # ServiceSelector._ready_or_build); the regex tier still protects the turn.
        if not selector._ready_or_build():
            return None
        with self._lock:
            if self._classifier is None:
                vectors = selector._ensure_example_index()
                labels = [1 if example.label == "emergency" else 0 for example in selector.examples]
                if not any(labels) or all(labels):
                    return None
                self._classifier = LogisticClassifier.fit(vectors, labels, l2=self.l2)
        return self._classifier

    def warm(self) -> None:
        """Train ahead of the first guest turn (startup, outside any turn)."""
        self._ensure_classifier()

    def probability(self, query: str, query_vector: Sequence[float] | None = None) -> float | None:
        if not query or not query.strip():
            return None
        classifier = self._ensure_classifier()
        if classifier is None:
            return None
        if query_vector is None:
            if self.service_selector is None:
                return None
            query_vector = self.service_selector._query_vector(query)
        return classifier.probability(query_vector)

    def evaluate(self, query: str, query_vector: Sequence[float] | None = None) -> RouteDecision | None:
        """Return an emergency or emergency_check decision, else ``None``."""
        p = self.probability(query, query_vector=query_vector)
        if p is None:
            return None
        if p >= self.min_prob:
            return RouteDecision("emergency", True)
        if p >= self.review_prob:
            return RouteDecision("emergency_check", True)
        return None
