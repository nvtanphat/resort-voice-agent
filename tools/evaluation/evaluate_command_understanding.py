"""Measure command-mode service understanding against the legacy router.

For every labelled service utterance this reports two stages separately:

* selector recall: whether the semantic ``ServiceSelector`` puts the expected
  registry service into the bounded candidate set given to the model;
* command accuracy: whether the local SLM, constrained to that set, proposes a
  ``StartGoal`` (or ``Handoff`` for human assistance) for the expected service;
* fallback accuracy: the model-free nearest-example fallback used when the SLM
  is unavailable (``ServiceSelector.fallback_commands`` with the configured
  thresholds). ``--fallback-only`` skips the SLM and measures just this.

Requires the pinned dev environment
(``.env.example``) and a running loopback Ollama with the embedding and SLM
models. Accuracy runs may use the GPU; pass ``--num-gpu 0`` for kiosk latency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.evaluation.command_results import MeasuredCases, OUTCOMES, fingerprint, write_report

from concierge_kiosk.agent.understanding import commands as command_module  # noqa: E402
from concierge_kiosk.agent.understanding.service_selector import (  # noqa: E402
    ServiceSelector,
    load_command_examples,
)
from concierge_kiosk.core.domain_profile import nlu_policy  # noqa: E402
from concierge_kiosk.core.dataset_layout import (  # noqa: E402
    SERVICE_CATALOG,
    TRAIN_AGENT_CANDIDATES,
    TRAIN_AGENT_MULTILINGUAL,
    TRAIN_AGENT_VI_GOLD,
    dataset_path,
)
from concierge_kiosk.domain.service_registry import (  # noqa: E402
    default_service_for,
    service_definition,
)
from concierge_kiosk.rag.embedding.local import LocalEmbedder  # noqa: E402


def _enabled_request_kinds() -> frozenset[str]:
    profile = json.loads((ROOT / "releases/property-profile.json").read_text(encoding="utf-8"))
    return frozenset(str(item["request_kind"]) for item in profile.get("service_catalog", ())
                     if isinstance(item, dict) and item.get("request_kind"))


def _rows(path: Path, languages: set[str], limit: int,
          enabled: frozenset[str]) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("expected_route") != "service" or not row.get("service_code"):
            continue
        if languages and row.get("language") not in languages:
            continue
        definition = service_definition(str(row["service_code"]))
        # A service the property has not enabled is unreachable for every
        # router; scoring it would only measure the property configuration.
        if definition is None or definition.request_kind not in enabled:
            continue
        rows.append(row)
    return rows[:limit] if limit else rows


def _predicted_goal(proposal) -> str | None:
    if not proposal:
        return None
    for command in proposal:
        if command.type == "StartGoal" and command.goal:
            return command.goal
    for command in proposal:
        if command.type == "Handoff":
            return default_service_for("human")
    return None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 1)


def _evaluation_inputs(args, rows: list[dict], enabled: frozenset[str]) -> dict:
    parsed = urlsplit(args.base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'}):
        raise ValueError('Evaluation model endpoint must be loopback')
    with urlopen(args.base_url.rstrip('/') + '/api/tags', timeout=15) as response:
        models = json.load(response).get('models', [])
    wanted = {args.model, args.embedding_model.removeprefix('ollama://')}
    digests = {item['name']: item['digest'] for item in models
               if item.get('name') in wanted or item.get('name', '').removesuffix(':latest') in wanted}
    files = [args.dataset, args.embedding_manifest, dataset_path(SERVICE_CATALOG),
             dataset_path(TRAIN_AGENT_VI_GOLD), dataset_path(TRAIN_AGENT_MULTILINGUAL),
             dataset_path(TRAIN_AGENT_CANDIDATES), ROOT / 'config/agent-domain.json',
             ROOT / 'releases/property-profile.json',
             ROOT / 'tools/evaluation/evaluate_command_understanding.py',
             ROOT / 'tools/evaluation/command_results.py',
             ROOT / 'src/concierge_kiosk/agent/understanding/commands.py',
             ROOT / 'src/concierge_kiosk/agent/understanding/service_selector.py',
             ROOT / 'src/concierge_kiosk/agent/understanding/semantic.py',
             ROOT / 'src/concierge_kiosk/runtime/local_http.py',
             ROOT / 'src/concierge_kiosk/core/settings.py']
    return {'files': {str(Path(file).resolve()): hashlib.sha256(Path(file).read_bytes()).hexdigest()
                      for file in files},
            'scope_hash': fingerprint({'rows': rows}), 'enabled': sorted(enabled),
            'model': args.model, 'embedding_model': args.embedding_model, 'model_digests': digests,
            'top_k': args.top_k, 'example_k': args.example_k, 'languages': sorted(args.languages or ()),
            'fallback_only': args.fallback_only, 'timeout': args.timeout, 'num_gpu': args.num_gpu,
            'max_failures': args.max_failures, 'endpoint': args.base_url}


def _measure_case(args, selector, policy, enabled, row, index) -> dict:
    query, language = str(row['utterance']), str(row['language'])
    expected = str(row['service_code'])
    expected_kind = service_definition(expected).request_kind
    candidates, shots = selector.understand(query, language=language,
                                            enabled_request_kinds=enabled)
    modes = [str(item.get('service_mode')) for item in candidates]
    in_candidates = expected in modes
    fallback_items = selector.fallback_commands(
        query, language=language, enabled_request_kinds=enabled,
        min_score=float(policy['fallback_min_score']), min_margin=float(policy['fallback_min_margin']))
    fallback_proposal = command_module.parse_commands(
        json.dumps({'commands': list(fallback_items or ())}), query=query,
        language=language, enabled_request_kinds=enabled)
    fallback = _predicted_goal(fallback_proposal)
    proposal, latency = None, None
    if not args.fallback_only:
        started = time.perf_counter()
        proposal = command_module.model_commands(
            query=query, language=language, base_url=args.base_url, model=args.model,
            enabled_request_kinds=enabled, service_candidates=candidates, examples=shots,
            timeout_seconds=args.timeout)
        latency = (time.perf_counter() - started) * 1000
    goal = _predicted_goal(proposal)
    goal_kind = service_definition(goal).request_kind if goal and service_definition(goal) else None
    return {'index': index, 'id': row.get('case_id') or row.get('scenario_id'), 'language': language,
            'utterance': query, 'expected': expected, 'predicted': goal,
            'selector_rank': modes.index(expected) + 1 if in_candidates else None,
            'candidates': modes, 'commands': [command.public() for command in proposal] if proposal else None,
            'fallback': fallback, 'latency_ms': latency,
            'outcome': {'selector_hit': in_candidates, 'parsed': proposal is not None,
                        'command_mode': goal == expected, 'command_kind': goal_kind == expected_kind,
                        'fallback_mode': fallback == expected, 'fallback_answered': fallback is not None}}


def _report(args, journal, counts, latencies, failures, resumed) -> dict:
    def rate(prefix, key):
        total = counts[f'{prefix}:total']
        return round(counts[f'{prefix}:{key}'] / total, 4) if total else None

    return {
        'complete': journal.complete,
        'config': {'dataset': str(args.dataset.relative_to(ROOT) if args.dataset.is_relative_to(ROOT)
                                  else args.dataset),
                   'rows': journal.expected, 'measured_rows': len(journal.records),
                   'resumed_cases': resumed, 'input_fingerprint': journal.header['fingerprint'],
                   'model': args.model, 'embedding_model': args.embedding_model,
                   'top_k': args.top_k, 'example_k': args.example_k, 'fallback_only': args.fallback_only,
                   'timeout_seconds': args.timeout, 'num_gpu': args.num_gpu,
                   'machine': {'platform': platform.platform(), 'processor': platform.processor(),
                               'cpu_count': os.cpu_count()}},
        'rates': {prefix: {key: rate(prefix, key) for key in OUTCOMES}
                  for prefix in ('all', 'vi', 'en', 'zh', 'ko') if counts[f'{prefix}:total']},
        'latency_ms': {'p50': _percentile(latencies, .5), 'p95': _percentile(latencies, .95)},
        'counts': dict(counts), 'failures': failures,
    }


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    enabled = _enabled_request_kinds()
    rows = _rows(args.dataset, set(args.languages or ()), args.limit, enabled)
    inputs = _evaluation_inputs(args, rows, enabled)
    cases_path = args.cases_output or args.output.with_name(args.output.stem + '-cases.jsonl')
    journal = MeasuredCases(cases_path, inputs, len(rows), resume=args.resume)
    resumed = len(journal.records)
    print(json.dumps({'expected': len(rows), 'resumed': resumed, 'checkpoint': str(cases_path)}), flush=True)
    embedder = LocalEmbedder(args.embedding_model, str(args.embedding_manifest))
    examples = (load_command_examples([dataset_path(TRAIN_AGENT_VI_GOLD),
                                       dataset_path(TRAIN_AGENT_MULTILINGUAL),
                                       dataset_path(TRAIN_AGENT_CANDIDATES)]) if args.example_k else ())
    selector = ServiceSelector(dataset_path(SERVICE_CATALOG), embedder, top_k=args.top_k,
                               examples=examples, example_k=args.example_k)
    policy = nlu_policy().service_selector
    if args.num_gpu is not None:
        original_chat = command_module._chat

        def chat_with_options(base_url, payload, timeout, should_cancel):
            payload = dict(payload)
            payload['options'] = {**payload.get('options', {}), 'num_gpu': args.num_gpu}
            return original_chat(base_url, payload, timeout, should_cancel)

        command_module._chat = chat_with_options
    counts, latencies, failures = Counter(), [], []
    for index, row in enumerate(rows):
        record = journal.measured(index)
        if record is None:
            record = _measure_case(args, selector, policy, enabled, row, index)
            journal.append(record)
        if (record.get('language') != row['language'] or record.get('expected') != row['service_code']
                or record.get('id') != (row.get('case_id') or row.get('scenario_id'))):
            raise ValueError('Evaluation checkpoint case identity changed')
        outcome, language = record['outcome'], record['language']
        for prefix in ('all', language):
            counts[f'{prefix}:total'] += 1
            for key, passed in outcome.items():
                counts[f'{prefix}:{key}'] += int(passed)
        if record['latency_ms'] is not None:
            latencies.append(record['latency_ms'])
        passed = outcome['fallback_mode'] if args.fallback_only else outcome['command_mode']
        if not passed and len(failures) < args.max_failures:
            failures.append({key: value for key, value in record.items()
                             if key not in {'index', 'outcome', 'latency_ms'}})
        report = _report(args, journal, counts, latencies, failures, resumed)
        # Summaries are useful while running, but partial reports explicitly
        # remain incomplete. Completed cases are always flushed separately.
        report['complete'] = counts['all:total'] == journal.expected
        write_report(args.output, report)
        if counts['all:total'] % 25 == 0:
            print(json.dumps({'measured': counts['all:total'], 'total': len(rows),
                              'command_correct': counts['all:command_mode'],
                              'selector_correct': counts['all:selector_hit']}), flush=True)
    if _evaluation_inputs(args, rows, enabled) != inputs:
        report['complete'] = False
        write_report(args.output, report)
        raise ValueError('Evaluation inputs changed during measurement')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path,
                        default=dataset_path("evaluation/challenges/natural.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--language", action="append", dest="languages")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--example-k", type=int, default=4,
                        help="nearest reviewed training turns shown as few-shots (0 disables)")
    parser.add_argument("--base-url", default=os.environ.get("CONCIERGE_LLM_BASE_URL",
                                                             "http://127.0.0.1:11434"))
    parser.add_argument("--model", default=os.environ.get("CONCIERGE_LLM_MODEL", "qwen2.5:3b"))
    parser.add_argument("--embedding-model", default="ollama://bge-m3")
    parser.add_argument("--embedding-manifest", type=Path,
                        default=ROOT / "models/embeddings/bge-m3.ollama.manifest.json")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--num-gpu", type=int, default=None,
                        help="Ollama num_gpu override; 0 measures CPU-only kiosk latency")
    parser.add_argument("--max-failures", type=int, default=80)
    parser.add_argument("--fallback-only", action="store_true",
                        help="skip the SLM and measure only the model-free fallback")
    parser.add_argument('--cases-output', type=Path,
                        help='UTF-8 measured-case checkpoint; default beside the report')
    parser.add_argument('--resume', action='store_true',
                        help='continue an interrupted evaluation only when every input fingerprint matches')
    args = parser.parse_args()
    report = evaluate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": report["config"]["rows"], "rates": report["rates"],
                      "latency_ms": report["latency_ms"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
