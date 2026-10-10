"""Bounded CPU/API router audit with a locked holdout and before/after journal.

No confirmation is sent. Only a private SQLite copy is opened for service work.
Holdout outcomes are withheld until the run finishes; they must not drive fixes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def score_case(dataset, row, body, intents, status, writes):
    """Use the repository's published route oracle, unchanged evaluation labels."""
    if dataset == 'vi_hard_negatives':
        from tools.evaluation.run_human_review_probe import oracle
        if row['expected_route'] == 'non_action':
            return (status == 200 and writes == 0 and body.get('tool_route') not in
                    {'service', 'handoff', 'emergency', 'multi_task'}), 'non_action_boundary'
        return oracle(row, body, status)
    goals = [c['goal'] for c in intents if c.get('type') == 'StartGoal']
    reads = sum(c.get('type') in {'AskInfo', 'CheckAvailability', 'Navigate'} for c in intents)
    if not intents and body.get('tool_route') in {'knowledge', 'check_schedule', 'navigation'}:
        reads = 1  # A deterministic read has no understanding command stream.
    return (status == 200 and Counter(goals) == Counter(row['expected_goals'])
            and reads == row['expected_reads']
            and not any(c.get('type') in {'Handoff', 'Modify', 'Cancel'} for c in intents)), 'intent_coverage'


def summary(records):
    result = {}
    for group in sorted({(r['dataset'], r['language']) for r in records}):
        rows = [r for r in records if (r['dataset'], r['language']) == group]
        calls = [call for r in rows for call in r['model_calls']]
        result['/'.join(group)] = {
            'samples': len(rows), 'correct': sum(r['correct'] for r in rows),
            'safe': sum(r['safe'] for r in rows),
            'latency_seconds': {key: percentile([r['latency_seconds'] for r in rows], p)
                                for key, p in [('p50', .5), ('p95', .95)]},
            'model_calls': len(calls),
            'mean_output_characters': sum(c['output_characters'] for c in calls) / len(calls) if calls else None,
            'multi_command_few_shots': sum(c['multi_shots'] for c in calls),
            'few_shots': sum(c['shots'] for c in calls),
            'writes_before_confirmation': sum(r['writes'] for r in rows),
            'wrong_proposals': sum(r['wrong_proposal'] for r in rows),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('before', 'after'), required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/intent-router.json')
    parser.add_argument('--cases', type=Path, default=ROOT / 'reports/intent-router-cases.jsonl')
    parser.add_argument('--holdout', type=Path, default=ROOT / 'datasets/evaluation/holdout/intent_router.jsonl',
                        help='a locked holdout (its .sha256 must match); use separate --output/--cases per holdout')
    args = parser.parse_args()
    holdout = args.holdout if args.holdout.is_absolute() else ROOT / args.holdout
    if digest(holdout) != holdout.with_suffix('.sha256').read_text().strip():
        raise SystemExit('Locked holdout changed')
    sources = [ROOT / 'datasets/evaluation/gold/vi_hard_negatives.jsonl', holdout]
    rows = [(path.stem, json.loads(line)) for path in sources
            for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    inputs = {p.as_posix(): digest(p) for p in [
        *sources, ROOT / 'config/agent-domain.json',
        *sorted((ROOT / 'src/concierge_kiosk/agent').rglob('*.py')),
        ROOT / 'src/concierge_kiosk/application/conversation/engine.py',
        ROOT / 'src/concierge_kiosk/runtime/admission.py',
        ROOT / 'src/concierge_kiosk/runtime/local_ai.py',
        *sorted((ROOT / 'datasets/training/agent').glob('*.jsonl')),
    ]}
    report = json.loads(args.output.read_text(encoding='utf-8')) if args.output.exists() else {}
    # Preserve the original journal; correct its initial route-only scoring in
    # the summary from the unchanged labels and complete saved API transcripts.
    if 'before' in report and not report['before'].get('oracle_rescored'):
        expected = {(dataset, row.get('case_id', row.get('scenario_id'))): row for dataset, row in rows}
        initial = [json.loads(line) for line in args.cases.read_text(encoding='utf-8').splitlines()
                   if line.strip() and json.loads(line)['stage'] == 'before']
        for item in initial:
            intents = item['understanding'][-1]['commands'] if item['understanding'] else []
            item['correct'], item['oracle_rule'] = score_case(item['dataset'],
                expected[item['dataset'], item['case_id']], item['body'], intents, item['http_status'], item['writes'])
        report['before']['summary'] = summary(initial)
        report['before']['oracle_rescored'] = {
            'reason': 'Initial scorer compared tool_route to labels and omitted evidence_status; original journal preserved.',
            'oracle': 'tools/evaluation/run_human_review_probe.py:oracle',
            'labels_unchanged': True,
            'rescored_cases': {item['case_id']: {'correct': item['correct'], 'rule': item['oracle_rule']}
                               for item in initial},
        }
    if args.stage in report:
        if report[args.stage].get('complete'):
            raise SystemExit('Stage already complete; preserve the existing measurement')
        interrupted = report.pop(args.stage)
        interrupted['stage'] = args.stage
        interrupted['disposition'] = 'Incomplete measurement excluded from before/after comparison; original journal preserved.'
        report.setdefault('incomplete_runs', []).append(interrupted)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.cases.parent.mkdir(parents=True, exist_ok=True)
    record = {'complete': False, 'expected_cases': len(rows), 'inputs': inputs,
              'model': 'qwen2.5:7b', 'embedding': 'bge-m3', 'num_gpu': 0,
              'nlu_budget_seconds': 30, 'confirmation_sent': False,
              'scope': 'NLU and real API/workflow; planner, generation and startup excluded'}
    record['run_id'] = uuid.uuid4().hex
    record['evaluator_sha256'] = digest(Path(__file__))
    record['output_characters_definition'] = 'Length of returned model text, including zero on an unavailable/aborted return; not provider token count.'
    record['intent_correctness_definition'] = 'Unchanged repository oracle for hard negatives; exact StartGoal multiset and read count for locked holdout, without verifying all operational slots.'
    report[args.stage] = record
    measured = []

    def persist():
        record['summary'] = summary(measured)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    os.environ.update(CONCIERGE_ENV='test', CONCIERGE_RUNTIME_PROFILE='test', LANGFUSE_ENABLED='false')
    logging.disable(logging.CRITICAL)
    from concierge_kiosk.agent.understanding import commands
    from concierge_kiosk.agent.understanding.service_selector import ServiceSelector, load_configured_examples
    from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path
    from concierge_kiosk.core.domain_profile import nlu_policy
    from concierge_kiosk.core.settings import Settings
    from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS
    from concierge_kiosk.rag.embedding.local import LocalEmbedder
    from concierge_kiosk.runtime import local_http
    from concierge_kiosk.application.conversation.engine import _TurnRuntimeSupport
    from fastapi.testclient import TestClient
    from concierge_kiosk.runtime.local_ai import _ollama_models
    tags = _ollama_models('http://127.0.0.1:11434', '/api/tags', 10)
    if tags is None:
        raise SystemExit('Local Ollama inventory unavailable')
    record['model_digests'] = {m['name']: m['digest'] for m in tags
                               if m['name'] in {'qwen2.5:7b', 'bge-m3:latest'}}
    source = ROOT / 'data/concierge.sqlite3'
    record['tracked_db_sha256'] = digest(source)
    embedder = LocalEmbedder('ollama://bge-m3', str(ROOT / 'models/embeddings/bge-m3.ollama.manifest.json'), num_gpu=0)
    policy = nlu_policy().service_selector
    selector = ServiceSelector(dataset_path(SERVICE_CATALOG), embedder,
        examples=load_configured_examples(), top_k=policy['top_k'], example_k=policy['example_k'],
        cache_dir=ROOT / '.cache/service-selector-eval')
    cached = ROOT / 'data/service-selector-cache' / selector._cache_path.name
    if cached.is_file() and not selector._cache_path.exists():
        selector._cache_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, selector._cache_path)
    print('Warming BGE index (not scored)', flush=True)
    selector.warm()
    from concierge_kiosk.runtime.local_ai import warm_local_slm
    begun = time.perf_counter()
    preloaded = warm_local_slm('http://127.0.0.1:11434', 'qwen2.5:7b', num_gpu=0,
        messages=[commands.command_system_message(ACTION_REQUEST_KINDS)])
    record['scoring_readiness'] = {'prefix_preloaded': preloaded,
                                  'unscored_seconds': time.perf_counter() - begun}
    if not preloaded:
        persist()
        raise SystemExit('Cannot score steady-state NLU without a ready model')
    calls, understood = [], []
    original_chat = commands._chat
    original_understand = _TurnRuntimeSupport.understand_turn

    def traced_chat(base, payload, timeout, cancel):
        begun = time.perf_counter()
        raw = original_chat(base, payload, timeout, cancel)
        shots = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'assistant']
        def count(shot):
            return len(shot.get('commands', [])) if isinstance(shot, dict) else len(shot)
        calls.append({'latency_seconds': time.perf_counter() - begun,
            'output_characters': len(raw or ''), 'raw': raw,
            'shots': len(shots), 'multi_shots': sum(count(s) > 1 for s in shots),
            'prompt_bytes': sum(len(m['content'].encode('utf-8')) for m in payload['messages'])})
        return raw

    def traced_understand(self, *a, **kw):
        value = original_understand(self, *a, **kw)
        understood.append({'route': value[0].branch, 'commands': [c.public() for c in value[3] or ()]})
        return value

    with tempfile.TemporaryDirectory(prefix='intent-router-', ignore_cleanup_errors=True) as directory:
        db = Path(directory) / 'audit.sqlite3'
        shutil.copyfile(source, db)
        os.environ['CONCIERGE_DB_PATH'] = str(Path(directory) / 'bootstrap.sqlite3')
        from concierge_kiosk import main as composition
        cfg = Settings(db_path=db, environment='test', property_id='FURAMA_DANANG',
            property_name='Furama Resort Danang', property_timezone='Asia/Ho_Chi_Minh',
            property_profile_path='releases/property-profile.json',
            property_profile_sha256=Path('releases/property-profile.sha256').read_text().strip(),
            map_release_path='releases/map-release.json',
            map_release_sha256=Path('releases/map-release.sha256').read_text().strip(),
            llm_base_url='http://127.0.0.1:11434', llm_model='qwen2.5:7b', llm_fallback_model='',
            slm_num_gpu=0, intent_parser_timeout_seconds=30, slm_generation_timeout_seconds=30,
            embedding_model_path='', embedding_manifest_path='', rerank_model_path='', rerank_manifest_path='',
            semantic_generation_enabled=False, semantic_understanding_enabled=True, agent_planner_enabled=False)
        with patch.object(composition, '_build_service_selector', lambda *_: selector), \
                patch.object(commands, '_chat', traced_chat), \
                patch.object(_TurnRuntimeSupport, 'understand_turn', traced_understand):
            app = composition.create_app(cfg, embedder=embedder)
            app.state.conversation_engine.turn_support.emergency_gate.warm()
            # No lifespan: avoids unrelated voice work and excludes warm-up from accuracy.
            client = TestClient(app)
            try:
                for index, (dataset, row) in enumerate(rows):
                    local_http._reset_circuit()
                    calls.clear(); understood.clear()
                    session = client.post('/api/session').json()
                    with app.state.store.connection() as con:
                        before = con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0]
                    started = time.perf_counter()
                    response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']},
                        json={'query': row['utterance'], 'language': row['language']})
                    elapsed = time.perf_counter() - started
                    body = response.json()
                    with app.state.store.connection() as con:
                        writes = con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] - before
                    intents = understood[-1]['commands'] if understood else body.get('understanding_commands', [])
                    goals = [c['goal'] for c in intents if c.get('type') == 'StartGoal']
                    reads = sum(c.get('type') in {'AskInfo', 'CheckAvailability', 'Navigate'} for c in intents)
                    proposal = bool(body.get('suggested_action') or body.get('requires_guest_confirmation')
                                    or (body.get('review_state') or {}).get('requires_guest_confirmation'))
                    with app.state.store.connection() as con:
                        proposals = con.execute('SELECT COUNT(*) FROM proposals WHERE session_id=?',
                                                (session['session_id'],)).fetchone()[0]
                    proposal = proposal or proposals > 0
                    if dataset == 'vi_hard_negatives':
                        wrong_proposal = proposal
                    else:
                        wrong_proposal = any(goal not in row['expected_goals'] for goal in goals)
                    correct, rule = score_case(dataset, row, body, intents, response.status_code, writes)
                    item = {'stage': args.stage, 'run_id': record['run_id'],
                        'dataset': dataset, 'case_id': row.get('case_id', row.get('scenario_id')),
                        'language': row['language'], 'http_status': response.status_code,
                        'latency_seconds': elapsed, 'model_calls': list(calls),
                        'understanding': list(understood), 'body': body, 'proposal_rows': proposals, 'oracle_rule': rule,
                        'correct': bool(correct and response.status_code == 200),
                        'safe': writes == 0 and not wrong_proposal, 'wrong_proposal': wrong_proposal, 'writes': writes}
                    with args.cases.open('a', encoding='utf-8') as out:
                        out.write(json.dumps(item, ensure_ascii=False) + '\n')
                        out.flush(); os.fsync(out.fileno())
                    measured.append(item)
                    persist()
                    print(f'{args.stage}: completed {index + 1}/{len(rows)}', flush=True)
            finally:
                client.close()
    unchanged = all(digest(path) == value for path, value in inputs.items())
    record['inputs_unchanged'] = unchanged
    record['tracked_db_unchanged'] = digest(source) == record['tracked_db_sha256']
    record['complete'] = unchanged and len(measured) == len(rows) and record['tracked_db_unchanged']
    persist()
    print(json.dumps(record['summary'], ensure_ascii=False, indent=2))
    return 0 if record['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
