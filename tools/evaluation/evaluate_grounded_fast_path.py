"""Independent evaluation of the grounded service fast path on ``datasets/evaluation``.

Measurement only: nothing here tunes a threshold or an ontology term.  Each labelled
single-turn row runs the runtime order for a fresh turn -- Tier 1 emergency grammar,
the learned emergency gate, the fast router, then ``GroundedServiceResolver.resolve`` --
with the real selector and real embeddings (no SLM).  Rows that declare conversation
context requirements cannot be judged without that state and are reported as excluded.

A fast-path decision is scored three ways:

* intent correct: the proposed service equals the labelled service;
* dialogue action under review: the label is a clarification (the fast path drafts the
  service and asks for missing slots, the label asks first) -- reported, not counted
  as correct;
* unsafe proposal: any other fire (another service, a multi-intent turn, a change,
  cancel, status, information or emergency turn);
* unscored: the row's route is ``service`` but it names no registry service code.

Precision excludes unscored rows from the denominator.

Query vectors are cached in ``--vector-cache`` so a re-run needs no model call.

Acceptance runs (docs/AGENT-FASTPATH-ACCEPTANCE-SPEC.md): ``--datasets`` names the frozen
holdout, ``--write-lock`` records code/config/data/model/threshold fingerprints before the
run, and ``--require-lock`` refuses to score if any fingerprint changed since.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / 'src', ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from concierge_kiosk.agent.understanding.emergency_gate import EmergencyGate  # noqa: E402
from concierge_kiosk.agent.understanding.fast_router import FastRouter, TurnContext  # noqa: E402
from concierge_kiosk.agent.understanding.grounded_service import GroundedServiceResolver  # noqa: E402
from concierge_kiosk.agent.understanding.routing import classify_dialogue  # noqa: E402
from concierge_kiosk.core.domain_profile import get_domain_profile, nlu_policy  # noqa: E402
from concierge_kiosk.domain.service_registry import service_definition  # noqa: E402

DATASETS = (
    'datasets/evaluation/gold/vi_core.jsonl',
    'datasets/evaluation/gold/vi_dev.jsonl',
    'datasets/evaluation/gold/vi_test.jsonl',
    'datasets/evaluation/gold/vi_hard_negatives.jsonl',
    'datasets/evaluation/holdout/service_workflow.jsonl',
    'datasets/evaluation/challenges/natural.jsonl',
    'datasets/evaluation/end_to_end/scenarios/production.jsonl',
)


def wilson(correct: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 1.0
    p = correct / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - spread) / (1 + z * z / total), (centre + spread) / (1 + z * z / total)


def truth(row: dict) -> tuple[str | None, str]:
    route = str(row.get('expected_route') or '')
    code = row.get('service_code')
    if route in {'service', 'handoff'} and code and service_definition(str(code)) is not None:
        return str(code), 'service'
    return None, route or 'unlabelled'


def fingerprint(datasets: list[str], policy) -> dict:
    """Everything an acceptance decision depends on, hashed before the run."""
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    tracked = subprocess.run(['git', 'diff', 'HEAD', '--', 'src', 'config', 'tools'], cwd=ROOT,
                             capture_output=True).stdout
    untracked = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard', 'src', 'tools'], cwd=ROOT,
                               capture_output=True, text=True).stdout.split()
    extra = hashlib.sha256()
    for name in sorted(untracked):
        extra.update(name.encode() + (ROOT / name).read_bytes())
    from concierge_kiosk.core.settings import Settings
    cfg = Settings()
    manifest = Path(cfg.embedding_manifest_path) if cfg.embedding_manifest_path else None
    return {
        'git_head': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                                   text=True).stdout.strip(),
        'working_tree_sha256': hashlib.sha256(tracked).hexdigest(),
        'untracked_sources_sha256': extra.hexdigest(),
        'agent_domain_sha256': sha(ROOT / 'config' / 'agent-domain.json'),
        'datasets': {name: sha(ROOT / name) for name in datasets},
        'embedding_model': str(cfg.embedding_model_path),
        'embedding_manifest_sha256': sha(manifest) if manifest and manifest.is_file() else None,
        'thresholds': {key: policy[key] for key in ('fast_path_min_score', 'fast_path_min_margin')},
        'fast_path_evidence_enabled_in_profile': bool(policy.get('fast_path_evidence_enabled', False)),
        'near_duplicates_with_training': near_duplicates(datasets),
    }


def _tokens(text: str) -> frozenset[str]:
    import re
    import unicodedata
    return frozenset(re.sub(r'[\W_]+', ' ', unicodedata.normalize('NFKC', text).casefold()).split())


def near_duplicates(datasets: list[str], threshold: float = 0.85) -> dict:
    """Holdout rows whose wording nearly repeats a training example (token Jaccard >= threshold)."""
    from concierge_kiosk.core.dataset_layout import training_agent_paths

    training = [_tokens(json.loads(line)['utterance']) for path in training_agent_paths(None)
                for line in Path(path).read_text(encoding='utf-8').splitlines()
                if line.strip() and isinstance(json.loads(line).get('utterance'), str)]
    found = {}
    for name in datasets:
        ids = []
        for line in (ROOT / name).read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            words = _tokens(row.get('utterance') or '')
            if words and any(len(words & other) / len(words | other) >= threshold for other in training if other):
                ids.append(row.get('scenario_id') or row.get('case_id') or row.get('id'))
        found[name] = {'count': len(ids), 'ids': ids[:50]}
    return found


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def load_runtime():
    from concierge_kiosk.core.settings import Settings
    from concierge_kiosk.main import _build_service_selector
    from concierge_kiosk.rag.embedding.local import LocalEmbedder

    cfg = Settings()
    embedder = LocalEmbedder(cfg.embedding_model_path, cfg.embedding_manifest_path, num_gpu=cfg.slm_num_gpu)
    selector = _build_service_selector(cfg, embedder)
    if selector is None or not selector._ready_or_build():
        raise SystemExit('service selector index is not available')
    return selector, embedder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', type=Path, default=ROOT / 'reports' / 'nlu' / 'fast-path-evaluation.json')
    parser.add_argument('--vector-cache', type=Path, default=ROOT / 'reports' / 'nlu' / 'fast-path-eval-vectors.json')
    parser.add_argument('--datasets', nargs='*', default=list(DATASETS),
                        help='JSONL files (repo-relative) to score; acceptance uses the frozen holdout only')
    parser.add_argument('--write-lock', type=Path, help='record fingerprints and exit without scoring')
    parser.add_argument('--require-lock', type=Path, help='refuse to score unless fingerprints match this lock')
    args = parser.parse_args()

    policy = nlu_policy().service_selector
    lock = fingerprint(args.datasets, policy)
    if args.write_lock:
        args.write_lock.parent.mkdir(parents=True, exist_ok=True)
        args.write_lock.write_text(json.dumps(lock, indent=2), encoding='utf-8')
        print('lock written', args.write_lock)
        return 0
    if args.require_lock:
        expected = json.loads(args.require_lock.read_text(encoding='utf-8'))
        changed = sorted(key for key in expected if expected[key] != lock.get(key))
        if changed:
            raise SystemExit(f'lock mismatch, refusing to score: {changed}')

    # The SLM must never be reached by this measurement; count any attempt.
    from concierge_kiosk.runtime import local_http
    qwen_calls = []
    original_open = local_http._OPENER.open

    def guarded(request, *pos, **kw):
        if getattr(request, 'full_url', '').endswith('/api/chat'):
            qwen_calls.append(request.full_url)
            raise ConnectionRefusedError('SLM is not part of the fast-path measurement')
        return original_open(request, *pos, **kw)

    local_http._OPENER.open = guarded
    selector, embedder = load_runtime()
    rows = []
    for name in args.datasets:
        for line in (ROOT / name).read_text(encoding='utf-8').splitlines():
            if line.strip():
                row = json.loads(line)
                if isinstance(row.get('utterance'), str) and row.get('language'):
                    rows.append((name, row))

    cache = json.loads(args.vector_cache.read_text(encoding='utf-8')) if args.vector_cache.is_file() else {}
    missing = sorted({row['utterance'].strip()[:500] for _, row in rows} - set(cache))
    batch = getattr(embedder, 'encode_many', None)
    for start in range(0, len(missing), 32):
        chunk = missing[start:start + 32]
        vectors = list(batch(chunk)) if batch else [embedder.encode_query(text) for text in chunk]
        cache.update(dict(zip(chunk, vectors)))
    if missing:
        args.vector_cache.parent.mkdir(parents=True, exist_ok=True)
        args.vector_cache.write_text(json.dumps(cache), encoding='utf-8')
    selector._query_vector = lambda query: cache[query.strip()[:500]]

    gate = EmergencyGate(selector, min_prob=float(policy['emergency_min_prob']),
                         review_prob=float(policy['emergency_review_prob']), l2=float(policy['emergency_l2']))
    gate.warm()
    router = FastRouter(selector, min_score=float(policy['router_min_score']),
                        min_margin=float(policy['router_min_margin']))
    # Both paths are measured whatever the deployed flag says; the report is per path.
    resolver = GroundedServiceResolver(selector, min_score=float(policy['fast_path_min_score']),
                                       min_margin=float(policy['fast_path_min_margin']), evidence_path=True)
    kinds = frozenset(service.request_kind for service in get_domain_profile().services)

    totals = Counter()
    by = defaultdict(Counter)
    errors = []
    decision_ms: dict[str, list[float]] = defaultdict(list)
    for name, row in rows:
        query, language = row['utterance'], row['language']
        goal, route = truth(row)
        if row.get('context_requirements'):
            totals['excluded_context'] += 1
            continue
        part = str(row.get('holdout_part') or 'unsplit')
        totals['evaluated'] += 1
        totals['labelled_service'] += goal is not None
        totals[f'labelled_service:{part}'] += goal is not None
        if classify_dialogue(query, language).branch in {'emergency', 'emergency_check'}:
            totals['preempted_tier1'] += 1
            continue
        if gate.evaluate(query, query_vector=cache[query.strip()[:500]]) is not None:
            totals['preempted_tier2'] += 1
            continue
        if router.route(query, language, TurnContext()) is not None:
            totals['preempted_router'] += 1
            continue
        confident = selector.nearest(query, min_score=resolver.min_score, min_margin=resolver.min_margin)
        started = time.perf_counter()
        commands = resolver.resolve(query, language, enabled_request_kinds=kinds)
        decision_ms['all_turns'].append((time.perf_counter() - started) * 1000)
        if not commands:
            continue
        decision_ms['fired'].append(decision_ms['all_turns'][-1])
        path = 'similarity' if confident is not None and resolver._goal(confident) == commands[0].goal else 'evidence'
        predicted = commands[0].goal
        outcome = ('correct' if predicted == goal
                   else 'dialogue_review' if route in {'clarify', 'clarification'}
                   else 'unscored_service' if route == 'service' and goal is None
                   else 'unsafe')
        expected = {k: str(v).casefold() for k, v in (row.get('expected_slots') or {}).items()}
        filled = {slot.name: slot.text.casefold() for slot in commands[0].slots}
        slot_mismatch = outcome == 'correct' and any(
            name in expected and value not in expected[name] and expected[name] not in value
            for name, value in filled.items())
        for key in ('all', f'path:{path}', f'lang:{language}', f'service:{predicted}', f'set:{name.split("/", 2)[-1]}',
                    f'part:{part}', f'part:{part}:path:{path}'):
            by[key]['fired'] += 1
            by[key][outcome] += 1
            by[key]['slot_mismatch'] += slot_mismatch
        if slot_mismatch:
            errors.append({'dataset': name, 'id': row.get('scenario_id') or row.get('case_id') or row.get('id'),
                           'language': language, 'label': goal, 'predicted': predicted, 'path': path,
                           'outcome': 'slot_mismatch', 'filled': filled, 'expected': expected,
                           'utterance': query})
        if outcome != 'correct':
            errors.append({'dataset': name, 'id': row.get('scenario_id') or row.get('case_id') or row.get('id'),
                           'language': language, 'label': goal or route, 'predicted': predicted,
                           'path': path, 'outcome': outcome, 'utterance': query})

    summary = {}
    for key, counts in sorted(by.items()):
        fired, correct, unsafe = counts['fired'], counts['correct'], counts['unsafe']
        scored = fired - counts['unscored_service']
        summary[key] = {'fired': fired, 'correct': correct, 'dialogue_review': counts['dialogue_review'],
                        'unsafe': unsafe, 'unscored_service': counts['unscored_service'],
                        'slot_mismatch': counts['slot_mismatch'],
                        'precision': correct / scored if scored else None,
                        'precision_wilson95': wilson(correct, scored),
                        'unsafe_rate': unsafe / scored if scored else None,
                        'unsafe_rate_wilson95_upper': wilson(unsafe, scored)[1]}
    report = {'lock': lock, 'qwen_calls': len(qwen_calls),
              'decision_latency_ms': {key: {'n': len(values), 'p50': percentile(values, 0.5),
                                            'p95': percentile(values, 0.95)}
                                      for key, values in decision_ms.items()},
              'latency_scope': 'resolver decision only; query embedding is cached and not included',
              'thresholds': {key: policy[key] for key in ('fast_path_min_score', 'fast_path_min_margin')},
              'totals': dict(totals), 'coverage_of_labelled_service_rows':
                  (by['all']['correct'] / totals['labelled_service']) if totals['labelled_service'] else None,
              # Precision is read on the trigger part; coverage only on natural traffic.
              'coverage_on_traffic': ((by['part:traffic']['correct'] / totals['labelled_service:traffic'])
                                      if totals['labelled_service:traffic'] else None),
              'summary': summary, 'non_correct': errors}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'totals': report['totals'], 'coverage': report['coverage_of_labelled_service_rows'],
                      'coverage_on_traffic': report['coverage_on_traffic'],
                      'qwen_calls': report['qwen_calls'], 'decision_latency_ms': report['decision_latency_ms'],
                      'all': summary.get('all'), 'similarity': summary.get('path:similarity'),
                      'evidence': summary.get('path:evidence')}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    os.environ.setdefault('CONCIERGE_ENV', 'development')
    raise SystemExit(main())
