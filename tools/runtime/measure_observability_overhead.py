"""Offline same-input WP14 adapter overhead; never opens a model/Cloud socket."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import secrets
import statistics
import time
from types import SimpleNamespace

from concierge_kiosk.runtime.observability import Observability
from concierge_kiosk.agent.understanding.commands import parse_commands
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS


class CountingBackend:
    def __init__(self):
        self.count = 0

    def start(self, name, trace_id, parent_id, metadata):
        self.count += 1
        return SimpleNamespace(id=secrets.token_hex(8), trace_id=trace_id or secrets.token_hex(16))

    def finish(self, handle, metadata):
        pass


def measure(iterations=500, batches=5):
    raw = '{"commands":[{"type":"StartGoal","goal":"amenity_delivery","slots":[]}]}'
    def parse():
        assert parse_commands(raw, query='Please bring water', language='en',
                              enabled_request_kinds=ACTION_REQUEST_KINDS)
    disabled = Observability()
    sink = CountingBackend()
    enabled = Observability(sink)
    samples = {key: [] for key in ('no_active_trace', 'disabled', 'mock_enabled')}
    for _ in range(10):
        parse()
    for batch in range(batches):
        # Alternate order to reduce systematic cache/CPU ordering bias.
        order = list(samples) if batch % 2 == 0 else list(reversed(samples))
        for mode in order:
            start = time.perf_counter_ns()
            for _ in range(iterations):
                if mode == 'no_active_trace':
                    parse()
                else:
                    with (disabled if mode == 'disabled' else enabled).turn('synthetic-benchmark-session'):
                        parse()
            samples[mode].append((time.perf_counter_ns() - start) / iterations / 1e6)
    medians = {key: statistics.median(value) for key, value in samples.items()}
    return {'mode': 'offline_mock', 'iterations_per_batch': iterations, 'batches': batches,
            'batch_mean_ms': samples, 'median_batch_mean_ms': medians,
            'disabled_scope_overhead_ms': medians['disabled'] - medians['no_active_trace'],
            'enabled_minus_disabled_ms': medians['mock_enabled'] - medians['disabled'],
            'mock_observations': sink.count, 'real_qwen_http_calls': 0, 'cloud_requests': 0,
            'limitations': 'No SDK/network/export latency or CPU inference measurement. Same current parser, differing trace scopes.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 10000:
        parser.error('iterations must be between 1 and 10000')
    result = measure(args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['median_batch_mean_ms']))


if __name__ == '__main__':
    main()

