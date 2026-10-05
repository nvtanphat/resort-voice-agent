"""Benchmark *installed*, real local Ollama variants without downloading models.

Example:
  python -m tools.evaluation.benchmark_local_slm --dataset /secure/eval/slm.jsonl \
      --models my-quant-int4 my-quant-int8 --report /secure/eval/slm-report.json

Each JSONL row needs language, question, evidence=[{"content":"..."}], and an
optional expected_answer. Report contains aggregate timings/correctness ONLY,
not questions or answers. Tool use accuracy, actual GPU/RAM and human judgment
need separate acceptance; this measures the production constrained phrasing task.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from concierge_kiosk.agent.orchestration.grounding import (MAX_EVIDENCE_CONTEXT_CHARS, build_slm_payload,
                                        compact_evidence, is_supported_answer,
                                        verified_evidence_composition)


def percentile(values: list[float], part: float) -> float:
    data = sorted(values)
    return round(data[max(0, math.ceil(len(data)*part)-1)], 2)


def invoke(endpoint: str, model: str, item: dict, timeout: float = 40) -> dict:
    body = json.dumps(build_slm_payload(model, item['question'], item['evidence'],
                                        item['language']), ensure_ascii=False).encode()
    req = Request(endpoint.rstrip('/')+'/api/chat', data=body,
                  headers={'Content-Type': 'application/json'}, method='POST')
    start = time.perf_counter()
    first = None
    total_bytes = 0
    answer = []
    result = None
    with urlopen(req, timeout=timeout) as response:  # nosec B310  # scheme/host validated by caller
        if response.status != 200:
            raise RuntimeError('Local model inference returned an unsuccessful status')
        for index, raw in enumerate(response):
            total_bytes += len(raw)
            if index >= 256 or total_bytes > 40_000:
                raise RuntimeError('SLM response exceeded streaming bounds')
            event = json.loads(raw)
            if event.get('error'):
                raise RuntimeError('Local model returned an inference error')
            chunk = event.get('message', {}).get('content', '')
            if chunk:
                if first is None:
                    first = (time.perf_counter()-start)*1000
                answer.append(chunk)
                if sum(map(len, answer)) > 650:
                    raise RuntimeError('Model output exceeded safety limit')
            if event.get('done') is True:
                result = event
                break
    if result is None:
        raise RuntimeError('Incomplete SLM stream')
    elapsed = (time.perf_counter()-start)*1000
    joined = ''.join(answer).strip()
    compact = compact_evidence(item['evidence'], question=item['question'])
    approved = (verified_evidence_composition(joined, compact)
                if joined.lstrip().startswith('{') else
                joined if is_supported_answer(joined, compact) else None)
    ground = approved is not None
    expected = item.get('expected_answer')
    return {'first_token_ms': first, 'response_ms': elapsed, 'grounded': ground,
            'matches_expected': (approved == expected) if expected is not None else None,
            'prompt_tokens': result.get('prompt_eval_count'),
            'generated_tokens': result.get('eval_count'),
            'model_load_ms': result.get('load_duration', 0)/1e6,
            'prefill_ms': result.get('prompt_eval_duration', 0)/1e6,
            'decode_ms': result.get('eval_duration', 0)/1e6}


@contextmanager
def sample_process_rss(pid: int | None):
    """Sample actual model-serving process RSS; never pretend API timing is RAM."""
    result = {'peak_rss_bytes': None}
    if pid is None:
        yield result
        return
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError('pip install psutil to measure actual model process RSS') from exc
    process = psutil.Process(pid)  # explicit deployment operator supplied PID
    stopped = threading.Event()
    def measure():
        while not stopped.wait(.02):
            try:
                current = process.memory_info().rss
                result['peak_rss_bytes'] = max(result['peak_rss_bytes'] or 0, current)
            except psutil.Error:
                break
    result['peak_rss_bytes'] = process.memory_info().rss
    observer = threading.Thread(target=measure, daemon=True)
    observer.start()
    try:
        yield result
    finally:
        stopped.set();observer.join(timeout=1)


def benchmark(dataset: Path, models: list[str], endpoint: str, *, timeout: float = 40,
              warmup: int = 0, model_pid: int | None = None,
              quantization: dict[str, str] | None = None) -> dict:
    url = urlsplit(endpoint)
    if url.scheme != 'http' or url.hostname not in {'localhost','127.0.0.1','::1'} or url.username:
        raise ValueError('Only a local loopback Ollama endpoint may be used')
    if warmup < 0 or warmup > 10:
        raise ValueError('Warm-up count outside supported bounds')
    if quantization and (set(quantization) - set(models) or
                         set(quantization.values()) - {'INT8', 'INT4', 'other'}):
        raise ValueError('Quantization labels must describe tested variants')
    items = [json.loads(line) for line in dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not items:
        raise ValueError('An evaluation dataset is required; do not invent model scores')
    for item in items:
        if item.get('language') not in {'vi','en','zh','ko'} or not item.get('question') or not item.get('evidence'):
            raise ValueError('Evaluation rows require language, question, evidence')
        if not isinstance(item['evidence'], list) or any(not isinstance(e,dict) or
           not isinstance(e.get('content'),str) for e in item['evidence']):
            raise ValueError('Invalid evidence rows')
        if sum(len(e['content']) for e in compact_evidence(item['evidence'])) > MAX_EVIDENCE_CONTEXT_CHARS:
            raise ValueError('Evidence exceeded production budget')
    report = {'type':'real_local_slm_inference','samples':len(items),
              'languages': sorted({row['language'] for row in items}),
              'note':'Model inference only. No raw query/output persisted; optional supplied process RSS is measured; total system RAM, GPU/VRAM and human-rated quality are not measured.',
              'models': {}}
    for model in models:
        # Same production payload, one warm-up per requested iteration; exclude
        # the warm-up from accuracy and latency statistics.
        for _ in range(warmup):
            invoke(endpoint, model, items[0], timeout=timeout)
        with sample_process_rss(model_pid) as resources:
            measured = [invoke(endpoint,model,row,timeout=timeout) for row in items]
        latencies = [m['response_ms'] for m in measured]
        firsts = [m['first_token_ms'] for m in measured if m['first_token_ms'] is not None]
        expected = [m['matches_expected'] for m in measured if m['matches_expected'] is not None]
        tokens = sum(m['generated_tokens'] for m in measured if isinstance(m['generated_tokens'], int))
        decode_ms = sum(m['decode_ms'] for m in measured)
        report['models'][model] = {
            'quantization_operator_label': (quantization or {}).get(model),
            'warmup_inferences_excluded': warmup,
            'model_process_peak_rss_bytes': resources['peak_rss_bytes'],
            'memory_scope': 'supplied process RSS only; not total system RAM, VRAM or model-only allocation',
            'sample_count':len(measured), 'first_token_p50_ms':statistics.median(firsts) if firsts else None,
            'first_token_p95_ms':percentile(firsts,.95) if firsts else None,
            'first_token_p99_ms':percentile(firsts,.99) if firsts else None,
            'response_p50_ms':round(statistics.median(latencies),2),
            'response_p95_ms':percentile(latencies,.95),
            'response_p99_ms':percentile(latencies,.99),
            'grounded_fraction':round(sum(m['grounded'] for m in measured)/len(measured),4),
            'expected_exact_match_fraction':round(sum(expected)/len(expected),4) if expected else None,
            'decode_tokens_per_sec':round(tokens/(decode_ms/1000),2) if decode_ms else None,
            'prefill_p50_ms':round(statistics.median(m['prefill_ms'] for m in measured),2),
            'prefill_p95_ms':percentile([m['prefill_ms'] for m in measured],.95),
            'prefill_p99_ms':percentile([m['prefill_ms'] for m in measured],.99),
            'model_load_p50_ms':round(statistics.median(m['model_load_ms'] for m in measured),2),
        }
        language_groups = {}
        for language in sorted({item['language'] for item in items}):
            group=[m for item,m in zip(items,measured) if item['language']==language]
            starts=[m['first_token_ms'] for m in group if m['first_token_ms'] is not None]
            expected_group=[m['matches_expected'] for m in group if m['matches_expected'] is not None]
            group_tokens=sum(m['generated_tokens'] for m in group if isinstance(m['generated_tokens'],int))
            group_decode=sum(m['decode_ms'] for m in group)
            language_groups[language]={
                'sample_count':len(group),
                'ttft_p50_ms':round(statistics.median(starts),2) if starts else None,
                'ttft_p95_ms':percentile(starts,.95) if starts else None,
                'ttft_p99_ms':percentile(starts,.99) if starts else None,
                'prefill_p50_ms':round(statistics.median(m['prefill_ms'] for m in group),2),
                'prefill_p95_ms':percentile([m['prefill_ms'] for m in group],.95),
                'prefill_p99_ms':percentile([m['prefill_ms'] for m in group],.99),
                'decode_tokens_per_sec':round(group_tokens/(group_decode/1000),2) if group_decode else None,
                'grounded_fraction':round(sum(m['grounded'] for m in group)/len(group),4),
                'expected_exact_match_fraction':round(sum(expected_group)/len(expected_group),4) if expected_group else None,
            }
        report['models'][model]['by_language']=language_groups
    return report


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--models',nargs='+',required=True)
    p.add_argument('--endpoint',default='http://127.0.0.1:11434')
    p.add_argument('--timeout',type=float,default=40)
    p.add_argument('--warmup',type=int,default=1)
    p.add_argument('--model-pid',type=int,help='Actual Ollama/server process PID for RSS sampling')
    p.add_argument('--quantization',action='append',default=[],metavar='MODEL=INT4|INT8|other',
                   help='Operator-provided quantization labels; never inferred from a model name')
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    if not args.dataset.is_file() or args.timeout<=0:
        p.error('Provide a real local evaluation dataset and positive timeout')
    labels={}
    for entry in args.quantization:
        if '=' not in entry:
            p.error('--quantization requires MODEL=INT4, MODEL=INT8, or MODEL=other')
        model,label=entry.rsplit('=',1)
        if not model or model not in args.models or label not in {'INT4','INT8','other'}:
            p.error('--quantization needs a requested MODEL=INT4, MODEL=INT8 or MODEL=other')
        labels[model]=label
    result=benchmark(args.dataset,args.models,args.endpoint,timeout=args.timeout,
                     warmup=args.warmup,model_pid=args.model_pid,quantization=labels)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
