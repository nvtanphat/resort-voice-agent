from __future__ import annotations

from concierge_kiosk.agent.understanding.semantic_router import RouteExample, SemanticRouter
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.application.conversation.engine import _apply_semantic_read_fallback


class WordEmbedder:
    def encode_query(self, text: str) -> list[float]:
        words = set(text.casefold().split())
        return [float("towel" in words or "towels" in words or "khăn" in words or "khan" in words),
                float("spa" in words),
                float("where" in words or "hours" in words)]


def test_semantic_router_is_language_scoped_and_returns_a_suggestion():
    router = SemanticRouter([
        RouteExample("1", "en", "send towels", "service"),
        RouteExample("2", "en", "where is the spa", "knowledge"),
        RouteExample("3", "vi", "mang khăn", "service"),
    ], WordEmbedder(), min_score=0.5, min_margin=0.1)
    decision = router.route("please send towels", "en")
    assert decision.accepted is True
    assert decision.route == "service"
    assert decision.example_id == "1"
    assert router.route("where is the spa", "vi").route is None
    assert router.route("mang khăn", "vi").route == "service"


def test_semantic_router_rejects_ambiguous_nearest_routes():
    router = SemanticRouter([
        RouteExample("1", "en", "send towels", "service"),
        RouteExample("2", "en", "where is the spa", "knowledge"),
    ], WordEmbedder(), min_score=0.5, min_margin=0.9)
    decision = router.route("spa towels", "en")
    assert decision.accepted is False
    assert decision.route is None


def test_semantic_router_shadow_mode_is_available_to_caller_without_activation():
    router = SemanticRouter([
        RouteExample("1", "en", "where is the spa", "knowledge"),
        RouteExample("2", "en", "send towels", "status"),
    ], WordEmbedder(), min_score=0.5, min_margin=0.1, mode="shadow")
    decision = router.route("please send towels", "en")
    assert decision.accepted is True
    assert router.mode == "shadow"
    base = RouteDecision("knowledge", False)
    assert (_apply_semantic_read_fallback(base, decision).branch == "request_status")


def test_semantic_router_runtime_cannot_grant_write_or_emergency_authority():
    base = RouteDecision('knowledge', False)
    assert _apply_semantic_read_fallback(
        base, type('Suggestion', (), {'accepted': True, 'route': 'status'})()
    ).branch == 'request_status'
    assert _apply_semantic_read_fallback(
        base, type('Suggestion', (), {'accepted': True, 'route': 'service'})()
    ) == base
    assert _apply_semantic_read_fallback(
        RouteDecision('service', True), type('Suggestion', (), {'accepted': True, 'route': 'emergency'})()
    ).branch == 'service'


def test_semantic_router_exposes_bounded_data_owned_examples():
    router = SemanticRouter([
        RouteExample('1', 'en', 'what time is the spa open', 'knowledge'),
        RouteExample('2', 'en', 'where is the spa', 'navigation'),
    ], WordEmbedder(), min_score=0.9, min_margin=0.9)
    examples = router.nearest_examples('where is the spa', 'en', limit=5)
    assert len(examples) == 2
    assert set(examples[0]) == {'example_id', 'text', 'route', 'score'}


def test_t2_conversational_labels_are_fast_and_never_enter_rag():
    base = RouteDecision('knowledge', False)
    for label in ('smalltalk', 'out_of_scope'):
        projected = _apply_semantic_read_fallback(
            base, type('Suggestion', (), {'accepted': True, 'route': label})())
        assert projected.branch == label
        assert projected.fast is True
