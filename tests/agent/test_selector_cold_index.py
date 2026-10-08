"""A guest turn must never wait for the service-selector index to be built.

Regression: ``_ready_or_build`` took the lock that the background builder holds for the
whole embedding pass, so the first guest turn after start-up blocked for about 80 s, its
turn expired (HTTP 409) and the SLM budget was gone before the model was called.
"""
from __future__ import annotations

import threading
import time

from concierge_kiosk.agent.understanding.service_selector import CommandExample, ServiceSelector
from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path
from concierge_kiosk.runtime.local_http import slm_turn_budget


class _SlowEmbedder:
    """Embeds instantly for queries, but blocks the bulk (index) path until released."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()

    def encode_query(self, text):
        return [1.0, 0.0]

    encode_passage = encode_query

    def encode_many(self, texts):
        self.started.set()
        self.release.wait(timeout=3)
        return [[1.0, 0.0] for _ in texts]


def test_a_guest_turn_does_not_wait_while_the_index_is_being_built():
    embedder = _SlowEmbedder()
    example = CommandExample("en", "bring towels", ({"type": "AskInfo", "query": "bring towels"},), None)
    selector = ServiceSelector(dataset_path(SERVICE_CATALOG), embedder, examples=[example])
    try:
        # First guest turn: starts the background build and must return at once.
        with slm_turn_budget(5.0):
            first = selector.understand("bring towels", language="en", enabled_request_kinds=frozenset())
        assert first == ((), ())
        assert embedder.started.wait(timeout=5), "background build did not start"
        # While the build holds the build lock, further guest turns still return immediately.
        started = time.perf_counter()
        for _ in range(3):
            with slm_turn_budget(5.0):
                again = selector.understand("bring towels", language="en", enabled_request_kinds=frozenset())
            assert again == ((), ())
        assert time.perf_counter() - started < 1.0
    finally:
        embedder.release.set()
