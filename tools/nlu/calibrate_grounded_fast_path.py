"""Calibrate the grounded service fast path on the reviewed training split.

Leave-one-situation-out: each training example is routed by its nearest examples from
*other* situations (the runtime ``nearest_label`` rule), then the runtime
``GroundedServiceResolver.ground`` checks (plain request, verbatim slots, WP13 evidence
gate, no competing service) run on its own utterance.  The fast path "fires" when the
router label is a service, clears ``min_score``/``min_margin`` and ``ground`` accepts it.

A fire is correct only when the example's reviewed commands are exactly one
``StartGoal`` of that service; firing on a multi-intent, information, status, change or
social example is an error.  The grid keeps the thresholds that maximise correct fires
subject to precision (overall and Vietnamese) and a bound on fires over non-service turns.

Vectors come from the runtime selector cache (``<db dir>/service-selector-cache``); with a
warm cache no model is called.  Evaluation data is never read.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / 'src', ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import numpy as np  # noqa: E402

from concierge_kiosk.agent.understanding.grounded_service import GroundedServiceResolver  # noqa: E402
from concierge_kiosk.agent.understanding.service_selector import example_eligible, nearest_label  # noqa: E402
from concierge_kiosk.core.domain_profile import get_domain_profile, nlu_policy  # noqa: E402
from concierge_kiosk.core.settings import Settings  # noqa: E402


def wilson_lower(correct: int, total: int, z: float = 1.96) -> float:
    if total == 0:
        return 0.0
    p = correct / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - spread) / (1 + z * z / total)


def single_service(example) -> str | None:
    commands = example.commands
    if len(commands) == 1 and commands[0].get('type') == 'StartGoal':
        return str(commands[0].get('goal') or '') or None
    return None


def load_selector():
    from concierge_kiosk.main import _build_service_selector
    from concierge_kiosk.rag.embedding.local import LocalEmbedder

    cfg = Settings()
    embedder = LocalEmbedder(cfg.embedding_model_path, cfg.embedding_manifest_path, num_gpu=cfg.slm_num_gpu)
    selector = _build_service_selector(cfg, embedder)
    if selector is None or not selector._ready_or_build():
        raise SystemExit('service selector index is not available')
    return selector


def predictions(selector) -> list[dict]:
    examples = selector.examples
    matrix = np.asarray(selector._example_vectors, dtype=np.float32)
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    similarity = matrix @ matrix.T
    kinds = frozenset(service.request_kind for service in get_domain_profile().services)
    resolver = GroundedServiceResolver(None, min_score=0.0, min_margin=0.0)
    rows = []
    for index, example in enumerate(examples):
        if example.pending_field or example.context_topic:
            continue  # the fast path runs only on a fresh turn without context
        order = np.argsort(-similarity[index])
        scored = [(float(similarity[index, j]), examples[j]) for j in order
                  if j != index and (example.group is None or examples[j].group != example.group)
                  and example_eligible(examples[j], None)]
        label, best, runner_up = nearest_label(scored)
        goal = label.split(':', 1)[1] if label and label.startswith('service:') else None
        grounded = (resolver.ground(goal, example.utterance, example.language, enabled_request_kinds=kinds)
                    if goal else None)
        direct = (resolver.ground(goal, example.utterance, example.language, enabled_request_kinds=kinds,
                                  require_direct=True) if goal else None)
        rows.append({
            'language': example.language, 'truth': example.label, 'truth_goal': single_service(example),
            'pred_goal': goal, 'best': best, 'margin': best - runner_up if runner_up > -1.0 else best,
            'grounded': grounded is not None, 'direct': direct is not None,
            'utterance': example.utterance,
        })
    return rows


def evaluate(rows: list[dict], min_score: float, min_margin: float, *, evidence_path: bool = True) -> dict:
    """Runtime decision: the similarity path, or (``evidence_path``) direct gate evidence."""
    fired = [row for row in rows
             if (row['grounded'] and row['best'] >= min_score and row['margin'] >= min_margin)
             or (evidence_path and row['direct'])]
    correct = [row for row in fired if row['pred_goal'] == row['truth_goal']]
    vi_fired = [row for row in fired if row['language'] == 'vi']
    vi_correct = [row for row in vi_fired if row['pred_goal'] == row['truth_goal']]
    services = [row for row in rows if row['truth_goal']]
    non_service = [row for row in rows if not row['truth_goal']]
    non_service_fired = [row for row in fired if not row['truth_goal']]
    return {
        'min_score': min_score, 'min_margin': min_margin,
        'fired': len(fired), 'correct': len(correct),
        'precision': len(correct) / len(fired) if fired else None,
        'precision_wilson_lower': wilson_lower(len(correct), len(fired)),
        'vi_fired': len(vi_fired), 'vi_precision': len(vi_correct) / len(vi_fired) if vi_fired else None,
        'coverage': len(correct) / len(services) if services else 0.0,
        'vi_coverage': (len(vi_correct) / max(1, sum(1 for row in services if row['language'] == 'vi'))),
        'non_service_fire_rate': len(non_service_fired) / len(non_service) if non_service else 0.0,
        'errors': Counter(f"{row['truth']}->{row['pred_goal']}" for row in fired
                          if row['pred_goal'] != row['truth_goal']).most_common(10),
        'error_examples': [f"[{row['language']}] {row['truth']}->{row['pred_goal']}: {row['utterance']}"
                           for row in fired if row['pred_goal'] != row['truth_goal']][:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', type=Path, default=ROOT / 'reports' / 'nlu' / 'fast-path-calibration.json')
    parser.add_argument('--min-precision', type=float, default=0.99)
    parser.add_argument('--max-non-service-fire-rate', type=float, default=0.005)
    parser.add_argument('--min-fired', type=int, default=30)
    args = parser.parse_args()

    rows = predictions(load_selector())
    grid = [evaluate(rows, round(score / 100, 2), round(margin / 100, 2))
            for score in range(55, 96) for margin in range(0, 16)]
    eligible = [item for item in grid
                if item['fired'] >= args.min_fired
                and (item['precision'] or 0.0) >= args.min_precision
                and (item['vi_precision'] is None or item['vi_precision'] >= args.min_precision)
                and item['non_service_fire_rate'] <= args.max_non_service_fire_rate]
    chosen = max(eligible, key=lambda item: (item['correct'], item['min_score'], item['min_margin']), default=None)
    report = {
        'examples': len(rows),
        'service_examples': sum(1 for row in rows if row['truth_goal']),
        'grounded_by_gate': sum(1 for row in rows if row['grounded']),
        'targets': {'min_precision': args.min_precision,
                    'max_non_service_fire_rate': args.max_non_service_fire_rate,
                    'min_fired': args.min_fired},
        'chosen': chosen,
        'by_language': ({language: evaluate([row for row in rows if row['language'] == language],
                                            chosen['min_score'], chosen['min_margin'])
                         for language in sorted({row['language'] for row in rows})} if chosen else {}),
        'current_config': evaluate(rows, *(float(nlu_policy().service_selector[key])
                                           for key in ('fast_path_min_score', 'fast_path_min_margin'))),
        'current_config_similarity_only': evaluate(
            rows, *(float(nlu_policy().service_selector[key])
                    for key in ('fast_path_min_score', 'fast_path_min_margin')), evidence_path=False),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('examples', 'service_examples', 'grounded_by_gate',
                                                   'current_config_similarity_only', 'current_config')},
                     ensure_ascii=False, indent=1))
    return 0 if chosen else 1


if __name__ == '__main__':
    os.environ.setdefault('CONCIERGE_ENV', 'development')
    raise SystemExit(main())
