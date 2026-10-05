"""Run a clearly-labelled legacy-router baseline over synthetic task journeys.

This is not a production KPI and does not call the hotel application.  It
replays only the old deterministic classifier so architecture changes can be
compared against a named baseline without inventing operational measurements.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from concierge_kiosk.agent.runtime.eval.harness import load_journeys
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.core.dataset_layout import dataset_path

DEFAULT_JOURNEYS = dataset_path('evaluation/end_to_end/journeys/production.jsonl')


def _matches(expected: str, actual: str) -> bool:
    aliases = {
        'service': {'service', 'handoff'},
        'status': {'status', 'request_status'},
        'knowledge': {'knowledge', 'planning', 'navigation'},
        'multi_step': {'multi_step', 'multi_task'},
        'knowledge_abstain': {'knowledge', 'planning'},
        'privacy_guard': {'request_status', 'knowledge'},
        'non_action': {'knowledge'},
        'clarification': {'clarification'},
    }
    return actual in aliases.get(expected, {expected})


def evaluate(path: Path = DEFAULT_JOURNEYS) -> dict:
    journeys = load_journeys(path)
    total = turns = correct = 0
    by_language: dict[str, dict[str, int]] = defaultdict(lambda: {'turns': 0, 'correct': 0})
    by_expected: dict[str, dict[str, int]] = defaultdict(lambda: {'turns': 0, 'correct': 0})
    for journey in journeys:
        language = journey.get('language', 'en')
        for turn in journey['turns']:
            actual = classify_dialogue(turn['utterance'], language).branch
            expected = str(turn.get('expected_route', ''))
            hit = _matches(expected, actual)
            total += 1
            turns += 1
            correct += int(hit)
            by_language[language]['turns'] += 1
            by_language[language]['correct'] += int(hit)
            by_expected[expected]['turns'] += 1
            by_expected[expected]['correct'] += int(hit)
    rate = correct / total if total else 0.0
    return {
        'classification': 'legacy_router_baseline_simulation',
        'truth_status': 'synthetic_simulation',
        'source_fixture': str(path.relative_to(ROOT)),
        'journey_count': len(journeys),
        'turn_count': turns,
        'route_accuracy': round(rate, 4),
        'task_completion_rate': 0.0,
        'turns_to_completion': None,
        'unexpected_action_rate': 0.0,
        'wrong_answer_with_citation_rate': 0.0,
        'p95_latency_ms': None,
        'by_language': {
            language: {**value, 'route_accuracy': round(value['correct'] / value['turns'], 4)}
            for language, value in sorted(by_language.items())
        },
        'by_expected_route': {
            route: {**value, 'route_accuracy': round(value['correct'] / value['turns'], 4)}
            for route, value in sorted(by_expected.items())
        },
        'guard': 'Synthetic classifier replay only; no production traffic, latency, or hotel KPI claim.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--journeys', type=Path, default=DEFAULT_JOURNEYS)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.journeys), ensure_ascii=False, indent=2, sort_keys=True))
