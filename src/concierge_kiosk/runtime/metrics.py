"""Privacy-minimal, bounded latency histograms for a single-device kiosk.

Each *_le_* bucket stores the number of observations falling INTO that bucket,
not cumulative Prometheus histogram counts. Percentiles are upper bounds, not
raw samples; no guest text, session identifiers, or per-turn traces are stored.
"""
from __future__ import annotations

import math
import re

LATENCY_BUCKETS_MS = (50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000, 120000)
CLIENT_STAGES = frozenset({
    'client.vad_end', 'client.stt', 'client.ask', 'client.tts_first_audio',
    'client.e2e_first_audio', 'client.playback_total',
    'client.barge_pause', 'client.barge_false_pause',
})
_BUCKET_RE = re.compile(r'^(?P<stage>(?:voice\.[a-z0-9_]+|agent\.node\.[a-z0-9_]+|client\.[a-z0-9_]+))\.latency_le_(?P<bound>\d+|over_\d+)ms$')


def latency_bucket(stage: str, ms: float) -> str:
    if not math.isfinite(ms) or ms < 0 or ms > 120000:
        raise ValueError('Invalid latency measurement')
    upper = next((value for value in LATENCY_BUCKETS_MS if ms <= value), None)
    label = str(upper) if upper is not None else 'over_120000'
    return f'{stage}.latency_le_{label}ms'


def aggregate_histograms(rows: list[dict]) -> list[dict]:
    """Summarize aggregated count buckets (no individual samples retained)."""
    groups: dict[tuple[str, str], dict[int | None, int]] = {}
    for row in rows:
        match = _BUCKET_RE.fullmatch(row['metric'])
        if not match:
            continue
        raw = match.group('bound')
        bound = None if raw.startswith('over_') else int(raw)
        key = (match.group('stage'), row['language'])
        group = groups.setdefault(key, {})
        group[bound] = group.get(bound, 0) + int(row['count'])
    output = []
    for (stage, language), buckets in sorted(groups.items()):
        total = sum(buckets.values())
        if not total:
            continue
        ordered = sorted(buckets.items(), key=lambda item: math.inf if item[0] is None else item[0])
        def percentile(p: float) -> int | None:
            rank = math.ceil(p * total)
            accumulated = 0
            for bound, count in ordered:
                accumulated += count
                if accumulated >= rank:
                    return bound
            return None
        output.append({
            'stage': stage, 'language': language, 'samples': total,
            'p50_upper_ms': percentile(.50), 'p95_upper_ms': percentile(.95),
            'p99_upper_ms': percentile(.99),
            'origin': 'browser' if stage.startswith('client.') else 'server',
            'approximate': True,
        })
    return output


def aggregate_slm_throughput(rows: list[dict]) -> list[dict]:
    """Only provider-reported eval_count/eval_duration, never estimated TPS."""
    groups: dict[str, dict[int, int]] = {}
    marker = re.compile(r'^voice\.slm_tokens_per_sec\.le_(\d+)$')
    for row in rows:
        match = marker.fullmatch(row['metric'])
        if match:
            bound = int(match.group(1))
            language = row['language']
            bucket = groups.setdefault(language, {})
            bucket[bound] = bucket.get(bound, 0) + int(row['count'])
    result=[]
    for language, buckets in sorted(groups.items()):
        total=sum(buckets.values())
        if total<=0: continue
        def percentile(p):
            rank=math.ceil(total*p)
            cumulative=0
            for upper, count in sorted(buckets.items()):
                cumulative+=count
                if cumulative>=rank: return upper
            return None
        result.append({'language':language,'samples':total,
                       'p50_upper_tokens_per_sec':percentile(.5),
                       'p95_upper_tokens_per_sec':percentile(.95),
                       'p99_upper_tokens_per_sec':percentile(.99),
                       'origin':'model_reported','approximate':True})
    return result
