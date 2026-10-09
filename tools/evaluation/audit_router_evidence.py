"""Offline audit adapter over existing labels and production gate functions.

No model/embedder, app, evaluation engine, dataset creation or network request.
Oracle goal probes test authorization coverage, never model accuracy.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

from tools.evaluation.evaluate_command_understanding import _enabled_request_kinds, _rows
from concierge_kiosk.agent.understanding.commands import Command, CommandSlot
from concierge_kiosk.agent.understanding.intent_evidence import command_supported
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.agent.understanding.service_selector import load_configured_examples
from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path
from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS

ROOT = Path(__file__).resolve().parents[2]


def inventory(path):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return rows, {'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'rows': len(rows), 'languages': dict(Counter(r.get('language', 'unspecified') for r in rows)),
                  'unique_utterances': len({(r.get('language'), r['utterance'].strip().casefold())
                                            for r in rows if isinstance(r.get('utterance'), str)}),
                  'labels': sorted({k for r in rows for k in r if k.startswith(('expected_', 'gold_', 'holdout_', 'split', 'truth_', 'review_'))})}


def oracle_command(row):
    """Label-supplied goal with only verbatim textual slot evidence, not extraction."""
    query = row['utterance']
    slots = tuple(CommandSlot(k, str(v)) for k, v in row.get('expected_slots', {}).items()
                  if type(v) in (str, int) and str(v).casefold() in query.casefold())
    return Command('StartGoal', goal=row['service_code'], slots=slots)


def audit_dataset(path, enabled):
    rows, manifest = inventory(path)
    cases = _rows(path, set(), 0, enabled)  # Existing evaluator eligibility contract.
    counters = Counter()
    languages = {}
    rejected = []
    gate_times = []
    safety = Counter()
    safety_confusion = Counter()
    for row in rows:
        if not row.get('utterance') or row.get('expected_route') is None:
            continue
        predicted = classify_dialogue(row['utterance'], row['language']).branch == 'emergency'
        expected = row['expected_route'] == 'emergency'
        safety['samples'] += 1
        safety['emergency_labels'] += expected
        safety['emergency_predictions'] += predicted
        safety['false_positive'] += predicted and not expected
        safety['missed_by_tier1'] += expected and not predicted
        safety_confusion[(str(expected), str(predicted))] += 1
    for row in cases:
        lang = row['language']
        counts = languages.setdefault(lang, Counter())
        # Any declared requirements are unresolved here: don't score absence
        # of server context as a proven false rejection.
        contextual = bool(row.get('context_requirements'))
        counts['eligible'] += 1
        counts['context_required'] += contextual
        command = oracle_command(row)
        started = time.perf_counter_ns()
        accepted = command_supported(command, row['utterance'], lang)
        gate_times.append((time.perf_counter_ns() - started) / 1e6)
        counts['accepted'] += bool(accepted)
        counts['rejected'] += not accepted
        if not contextual:
            counts['context_free'] += 1
            counts['context_free_accepted'] += bool(accepted)
            counts['context_free_rejected'] += not accepted
        if not accepted:
            rejected.append({'id': row.get('scenario_id', row.get('case_id')), 'language': lang,
                             'goal': command.goal, 'context_required': contextual})
    for counts in languages.values():
        counters.update(counts)
    return {**manifest, 'mode': 'oracle_goal_authorization_not_model_prediction',
            'eligible_enabled_services': len(cases), 'gate_counts': dict(counters),
            'gate_by_language': {k: dict(v) for k, v in languages.items()},
            'rejected_label_ids': rejected,
            'tier1_safety_counts': dict(safety),
            'tier1_confusion': [{'expected_emergency': a == 'True', 'predicted_emergency': b == 'True', 'count': n}
                                for (a, b), n in sorted(safety_confusion.items())],
            'gate_latency_ms': {'samples': len(gate_times), 'p50': percentile(gate_times, .5),
                                'p95': percentile(gate_times, .95)}}


def percentile(values, p):
    if not values:
        return None
    # Nearest-rank percentile, same meaning as journey harness order statistics.
    import math
    return sorted(values)[max(0, math.ceil(p * len(values)) - 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    enabled = _enabled_request_kinds()
    examples = load_configured_examples()
    catalog = json.loads(dataset_path(SERVICE_CATALOG).read_text(encoding='utf-8'))
    from concierge_kiosk.agent.understanding.service_selector import ServiceSelector, _catalog_entry
    offered = {ServiceSelector._mode_for_entry(entry, enabled) for row in catalog
               if (entry := _catalog_entry(row)) is not None}
    labels = Counter(e.goal for e in examples if e.goal)
    datasets = ['gold/vi_core.jsonl', 'gold/vi_dev.jsonl', 'gold/vi_test.jsonl',
                'gold/vi_hard_negatives.jsonl', 'holdout/service_workflow.jsonl',
                'challenges/natural.jsonl', 'end_to_end/scenarios/production.jsonl']
    manifests = [inventory(p)[1] for p in sorted((ROOT/'datasets/evaluation').rglob('*.jsonl'))]
    report = {'schema_version': 1, 'real_qwen_calls': 0, 'bge_calls': 0, 'cloud_requests': 0,
              'real_model_accuracy': 'REAL_MODEL_ACCURACY_NOT_MEASURED',
              'selector_recall_at_k': 'NOT_RUN', 'fast_router_accuracy': 'NOT_RUN',
              'inventory': manifests,
              'datasets': [audit_dataset(ROOT/'datasets/evaluation'/name, enabled) for name in datasets],
              'selector_structural_coverage': {
                  'reviewed_training_examples': len(examples),
                  'examples_by_language': dict(Counter(e.language for e in examples)),
                  'enabled_goals': [{'goal': goal, 'catalog_mapped': goal in offered,
                                     'training_examples': labels[goal]}
                                    for goal, definition in SERVICE_DEFINITIONS.items()
                                    if definition.request_kind in enabled]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({r['path']: r['gate_counts'] for r in report['datasets']}))


if __name__ == '__main__':
    main()
