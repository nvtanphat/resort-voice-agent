"""Offline comparison of real command payloads; never opens model transport."""
from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from unittest.mock import patch


def compare():
    os.environ.setdefault('CONCIERGE_ENV', 'test')
    os.environ.setdefault('CONCIERGE_RUNTIME_PROFILE', 'test')
    from jsonschema import Draft202012Validator
    from concierge_kiosk.agent.understanding import commands
    from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS
    from concierge_kiosk.runtime import local_http
    from tools.runtime.agent_stabilization_smoke import prompt_diagnostics

    schema_builder = commands.command_schema
    results = {}
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline comparison forbids model HTTP')
    with patch.object(local_http._OPENER, 'open', forbidden):
        for label, compact in (('baseline', False), ('compact', True)):
            payloads = []
            def schema(*args, **kwargs):
                return schema_builder(*args, **{**kwargs, 'compact': compact})
            with patch.object(commands, 'command_schema', schema), patch.object(
                    commands, '_chat', lambda b, p, t, c: payloads.append(p) or None):
                commands.model_commands(query='cho toi 3 chai nuoc suoi phong 502', language='vi',
                    enabled_request_kinds=ACTION_REQUEST_KINDS,
                    base_url='http://127.0.0.1:11434', model='qwen2.5:3b', num_gpu=0)
            payload = payloads[0]
            Draft202012Validator.check_schema(payload['format'])
            services = json.loads(payload['messages'][-1]['content'])['available_services']
            contracts = {s['service_mode']: s['accepted_slots'] for s in services}
            timings = []
            for _ in range(5):
                start = perf_counter_ns()
                for _ in range(10):
                    schema_builder(contracts, slot_reply=False, compact=compact)
                timings.append((perf_counter_ns() - start) / 10 / 1e6)
            results[label] = {**prompt_diagnostics(payload),
                'schema_validation': 'PASS_OFFLINE_JSON_SCHEMA',
                'python_schema_build_ms_median': median(timings),
                'python_schema_build_ms_samples': timings,
                'payload': payload}
    return {'qwen_http_calls': 0, 'measurement': 'Python schema construction only; not model latency',
            **results}


if __name__ == '__main__':
    result = compare()
    destination = Path('reports/command-schema-comparison.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: {name: value for name, value in item.items() if name != 'payload'}
                     if isinstance(item, dict) else item for k, item in result.items()}, indent=2))
