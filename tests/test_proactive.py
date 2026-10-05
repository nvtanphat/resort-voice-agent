from __future__ import annotations

from concierge_kiosk.agent.proactive import ProactiveEngine


def test_proactive_suggestions_are_opt_in_read_only_and_deduplicated():
    engine = ProactiveEngine(enabled=True, ttl_seconds=120, max_suggestions=2)
    signal = {
        'kind': 'sla', 'entity_id': 'request-1', 'text': 'Your request is taking longer than expected.',
        'capability': 'request_status', 'expires_at': 1120, 'active': True,
    }
    assert engine.suggest(session='s1', language='en', signals=[signal], now=1000) == ()
    first = engine.suggest(session='s1', language='en', signals=[signal], now=1000, consent=True)
    assert len(first) == 1
    assert first[0].public()['write_authority'] is False
    assert engine.suggest(session='s1', language='en', signals=[signal], now=1001, consent=True) == ()


def test_proactive_rejects_write_capabilities_and_expired_signals():
    engine = ProactiveEngine(enabled=True, ttl_seconds=120)
    signals = [
        {'kind': 'closing', 'entity_id': 'pool', 'text': 'Closing soon',
         'capability': 'service_action', 'expires_at': 1100},
        {'kind': 'checkout', 'entity_id': 'stay', 'text': 'Checkout reminder',
         'capability': 'knowledge', 'expires_at': 999},
    ]
    assert engine.suggest(session='s1', language='en', signals=signals, now=1000, consent=True) == ()
