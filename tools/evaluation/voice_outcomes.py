"""Summarize human-observed physical-device interruption and service outcomes.

No speech, names or guest details are read or emitted. This evaluator requires
actual operator/observer labels; a mocked-browser run is NOT a valid dataset.

python -m tools.evaluation.voice_outcomes --dataset /secure/voice/outcomes.jsonl \
    --report /secure/reports/outcomes.json --min-per-condition 20
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from tools.evaluation.voice import percentile

LANGUAGES = {'vi', 'en', 'zh', 'ko'}
CONDITIONS = {'quiet', 'noisy'}


def evaluate(dataset: Path, *, min_per_condition: int = 1) -> dict:
    if min_per_condition < 1:
        raise ValueError('Minimum must be positive')
    groups = defaultdict(list)
    for line in dataset.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get('consent') is not True or row.get('observed_on_device') is not True:
            raise ValueError('Requires consented physical-device human observations')
        if row.get('language') not in LANGUAGES or row.get('condition') not in CONDITIONS:
            raise ValueError('Invalid language or environment condition')
        for field in ('interruption_attempted', 'interruption_succeeded', 'task_completed'):
            if type(row.get(field)) is not bool:
                raise ValueError('Human-observed outcome must be a boolean: ' + field)
        if row['interruption_succeeded'] and not row['interruption_attempted']:
            raise ValueError('Cannot succeed without a measured interruption attempt')
        latency = row.get('interruption_latency_ms')
        if row['interruption_succeeded']:
            if type(latency) not in (float, int) or not 0 <= latency <= 30000:
                raise ValueError('A successful interruption needs bounded measured latency')
        elif latency is not None:
            raise ValueError('Latency applies only to observed successful interruption')
        groups[(row['language'], row['condition'])].append(row)
    if not groups:
        raise ValueError('Empty observed outcome dataset')
    output = {'type': 'physical_device_human_observations', 'conditions': {}}
    for lang in sorted(LANGUAGES):
        for condition in sorted(CONDITIONS):
            rows = groups.get((lang, condition), [])
            if len(rows) < min_per_condition:
                raise ValueError(f'Not enough human observations: {lang}/{condition}')
            attempts = sum(row['interruption_attempted'] for row in rows)
            successful = sum(row['interruption_succeeded'] for row in rows)
            timings = [float(row['interruption_latency_ms']) for row in rows
                       if row['interruption_succeeded']]
            item = {'sample_count': len(rows), 'interruption_attempt_count': attempts,
                    'interruption_success_rate': round(successful / attempts, 6) if attempts else None,
                    'task_completion_rate': round(sum(r['task_completed'] for r in rows) / len(rows), 6),
                    'interruption_latency_p50_ms': round(statistics.median(timings), 2) if timings else None,
                    'interruption_latency_p95_ms': round(percentile(timings, .95), 2) if timings else None,
                    'interruption_latency_p99_ms': round(percentile(timings, .99), 2) if timings else None}
            output['conditions'].setdefault(lang, {})[condition] = item
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--min-per-condition', type=int, default=1)
    args = parser.parse_args()
    report = evaluate(args.dataset, min_per_condition=args.min_per_condition)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'type': report['type'], 'measured_groups': len(report['conditions'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
