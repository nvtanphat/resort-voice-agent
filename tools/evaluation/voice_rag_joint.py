"""Fail-closed joint field evaluation: microphone -> STT -> grounded RAG -> audible TTS.

No transcripts/audio/guest IDs in input or output. All observations must be made
on real target hardware against staff-reviewed knowledge; a reviewer must still
audit source evidence. The script does not independently attest supplied labels.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

LANGUAGES = ('vi', 'en', 'zh', 'ko')
CONDITIONS = ('quiet', 'noisy')
REQUIRED_BOOL = ('consent', 'observed_on_device', 'approved_knowledge', 'stt_correct',
                 'retrieval_correct', 'answer_grounded', 'task_completed',
                 'stale_audio_emitted', 'unconfirmed_business_action',
                 'interruption_attempted', 'interruption_succeeded')


def nearest_rank(values: list[float], percentile: float) -> float:
    values = sorted(values)
    return round(values[math.ceil(percentile * len(values)) - 1], 2)


def evaluate(path: Path, *, min_per_condition: int = 20) -> dict:
    if not 1 <= min_per_condition <= 10000:
        raise ValueError('Invalid minimum cohort size')
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    seen: set[str] = set()
    if not path.is_file():
        raise ValueError('Field observations are missing')
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError) as exc:
            raise ValueError(f'Invalid JSON at row {number}') from exc
        if not isinstance(row, dict) or set(row) != ({'case_id', 'language', 'condition', 'split',
                     'audible_first_response_ms', 'interruption_latency_ms'} | set(REQUIRED_BOOL)):
            raise ValueError(f'Unexpected/missing fields at row {number}')
        case_id = row['case_id']
        if not isinstance(case_id, str) or not 1 <= len(case_id) <= 80 or case_id in seen:
            raise ValueError(f'Invalid/duplicate case at row {number}')
        seen.add(case_id)
        if row['language'] not in LANGUAGES or row['condition'] not in CONDITIONS or row['split'] != 'test':
            raise ValueError(f'Not a supported held-out group at row {number}')
        if any(type(row[key]) is not bool for key in REQUIRED_BOOL):
            raise ValueError(f'Boolean observation missing/invalid at row {number}')
        if not (row['consent'] and row['observed_on_device'] and row['approved_knowledge']):
            raise ValueError(f'Not consented, on-device, reviewed evidence at row {number}')
        if row['interruption_succeeded'] and not row['interruption_attempted']:
            raise ValueError(f'Impossible interruption labels at row {number}')
        if (row['interruption_attempted'] and row['interruption_latency_ms'] is None):
            raise ValueError(f'Missing interruption latency at row {number}')
        if (not row['interruption_attempted'] and row['interruption_latency_ms'] is not None):
            raise ValueError(f'Extraneous interruption latency at row {number}')
        for field in ('audible_first_response_ms', 'interruption_latency_ms'):
            value = row[field]
            if value is not None and (type(value) not in (float, int) or
                                     not math.isfinite(value) or not 0 <= value <= 120000):
                raise ValueError(f'Invalid latency at row {number}')
        if row['audible_first_response_ms'] is None:
            raise ValueError(f'Missing audible timing at row {number}')
        groups[(row['language'], row['condition'])].append(row)
    counts = {f'{language}/{condition}': len(groups[(language, condition)])
              for language in LANGUAGES for condition in CONDITIONS}
    if any(value < min_per_condition for value in counts.values()):
        raise ValueError(f'Insufficient held-out on-device coverage: {counts}')
    all_rows = [row for rows in groups.values() for row in rows]
    attempted = [row for row in all_rows if row['interruption_attempted']]
    if len(attempted) < 1:
        raise ValueError('No on-device interruption attempt was observed')
    def fraction(rows, field):
        return round(sum(row[field] for row in rows) / len(rows), 4)
    summary = {
        'status': 'MEASURED_REVIEW_REQUIRED', 'dataset': 'held_out_real_device_labels',
        'counts': counts, 'total': len(all_rows), 'interruption_attempts': len(attempted),
        'stt_correct_rate': fraction(all_rows, 'stt_correct'),
        'retrieval_correct_rate': fraction(all_rows, 'retrieval_correct'),
        'grounded_answer_rate': fraction(all_rows, 'answer_grounded'),
        'task_completion_rate': fraction(all_rows, 'task_completed'),
        'stale_audio_count': sum(row['stale_audio_emitted'] for row in all_rows),
        'unconfirmed_business_actions': sum(row['unconfirmed_business_action'] for row in all_rows),
        'audible_first_response_p95_ms': nearest_rank([row['audible_first_response_ms'] for row in all_rows], .95),
        'interruption_success_rate': fraction(attempted, 'interruption_succeeded'),
        'interruption_p95_ms': nearest_rank([row['interruption_latency_ms'] for row in attempted], .95),
    }
    # Illustrative targets requiring site agreement; a green report is not an
    # independent proof that human labels, measurements or approval are genuine.
    summary['illustrative_targets_met'] = (
        summary['stt_correct_rate'] >= .90 and summary['retrieval_correct_rate'] >= .95
        and summary['grounded_answer_rate'] >= .98 and summary['task_completion_rate'] >= .95
        and summary['stale_audio_count'] == summary['unconfirmed_business_actions'] == 0
        and summary['audible_first_response_p95_ms'] <= 3000
        and summary['interruption_success_rate'] >= .95 and summary['interruption_p95_ms'] <= 300)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observations', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--min-per-condition', type=int, default=20)
    args = parser.parse_args()
    try:
        result = evaluate(args.observations, min_per_condition=args.min_per_condition)
    except (ValueError, OSError) as exc:
        print(f'JOINT FIELD GATE BLOCKED: {exc}')
        return 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('JOINT FIELD EVALUATION: evidence recorded; human/site sign-off still required')
    print(json.dumps(result, indent=2))
    return 0 if result['illustrative_targets_met'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
