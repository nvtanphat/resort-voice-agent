"""Measured, consented ON-DEVICE speech evaluation (never synthetic model scores).

Example: python -m tools.evaluation.voice --dataset /secure/eval/voice.jsonl \
  --report /secure/eval/results.json --require-languages vi en zh ko \
  --min-per-language 20 --max-cer .20 --max-p95-ms 4000

WAV, OGG, WebM, MP3 and M4A samples are supported. Input reference text and
recognition output are NEVER serialized to the report. Keep consented raw media
outside the source repository. Chinese WER is intentionally omitted because a
whitespace tokenizer is not a word segmenter; use CER.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.voice.runtime.adapters import transcribe, validate_audio

SUFFIX_MIME = {'.wav': 'audio/wav', '.webm': 'audio/webm', '.ogg': 'audio/ogg',
               '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4'}
LANGUAGES = {'vi', 'en', 'zh', 'ko'}


def normalize(text: str) -> str:
    text = unicodedata.normalize('NFC', text).casefold()
    return ' '.join(''.join(' ' if unicodedata.category(c)[0] in {'P', 'S'} else c
                            for c in text).split())


def distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def error_counts(reference: str, hypothesis: str, *, characters=False) -> tuple[int, int]:
    ref, hyp = normalize(reference), normalize(hypothesis)
    # CER measures characters, not tokenization-induced spaces (especially CJK).
    a = list(ref.replace(' ', '')) if characters else ref.split()
    b = list(hyp.replace(' ', '')) if characters else hyp.split()
    if not a:
        raise ValueError('Reference cannot be empty after normalization')
    return distance(a, b), len(a)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError('No measurements')
    sorted_values = sorted(values)
    return sorted_values[max(0, math.ceil(len(sorted_values) * fraction) - 1)]


def evaluate(dataset: Path, cfg, *, require_languages: set[str] = frozenset(),
             min_per_language: int = 1, max_cer: float | None = None,
             max_wer: float | None = None, max_p95_ms: float | None = None) -> dict:
    root = dataset.resolve().parent
    measurements = defaultdict(lambda: {'sample_count': 0, 'cer_errors': 0, 'cer_units': 0,
                                        'wer_errors': 0, 'wer_units': 0, 'latency_ms': []})
    conditions = defaultdict(lambda: defaultdict(lambda: {'sample_count': 0, 'cer_errors': 0,
                                        'cer_units': 0, 'wer_errors': 0, 'wer_units': 0,
                                        'latency_ms': []}))
    for line in dataset.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        if sample.get('split') not in {'calibration', 'test'}:
            raise ValueError('Every field audio sample must identify calibration or held-out test split')
        if sample['split'] == 'calibration':
            continue  # Never inflate held-out accuracy with tuning/development recordings.
        lang = sample['language']
        condition = sample.get('condition', 'unspecified')
        if condition not in {'quiet', 'noisy', 'unspecified'}:
            raise ValueError('Condition must be quiet, noisy or unspecified')
        if lang not in LANGUAGES or sample.get('consent') is not True:
            raise ValueError('Each evaluation sample needs a supported language and explicit consent')
        path = root / sample['audio_path']
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError('Audio must be a local, non-symlink dataset file')
        with path.open('rb') as source:
            raw = source.read(cfg.max_audio_bytes + 1)
        mime = SUFFIX_MIME.get(path.suffix.lower())
        if not mime:
            raise ValueError('Unsupported sample extension')
        validate_audio(raw, mime, max_bytes=cfg.max_audio_bytes,
                       max_seconds=cfg.max_audio_seconds)
        start = time.perf_counter()
        recognized = transcribe(cfg, raw, lang)
        duration_ms = (time.perf_counter() - start) * 1000
        ce, cn = error_counts(sample['reference'], recognized, characters=True)
        for row in (measurements[lang], conditions[lang][condition]):
            row['sample_count'] += 1
            row['cer_errors'] += ce
            row['cer_units'] += cn
            if lang != 'zh':
                we, wn = error_counts(sample['reference'], recognized)
                row['wer_errors'] += we
                row['wer_units'] += wn
            row['latency_ms'].append(duration_ms)
    if not measurements:
        raise ValueError('Empty dataset: no measured performance')
    if not require_languages.issubset(measurements):
        raise ValueError('Required language absent from evaluation data')
    output = {'type': 'real_audio_inference', 'reference_tokenizer': 'Unicode NFC/whitespace',
              'note': 'CER is reported for all languages; Chinese WER omitted without a certified word segmenter.',
              'split': 'test', 'languages': {}}
    for lang, data in sorted(measurements.items()):
        latency = data['latency_ms']
        metrics = {'sample_count': data['sample_count'],
                   'cer': round(data['cer_errors'] / data['cer_units'], 6),
                   'wer': (round(data['wer_errors'] / data['wer_units'], 6)
                           if data['wer_units'] else None),
                   'latency_p50_ms': round(statistics.median(latency), 2),
                   'latency_p95_ms': round(percentile(latency, 0.95), 2),
                   'latency_p99_ms': round(percentile(latency, 0.99), 2)}
        metrics['conditions'] = {}
        for condition, group in sorted(conditions[lang].items()):
            values = group['latency_ms']
            metrics['conditions'][condition] = {
                'sample_count': group['sample_count'],
                'cer': round(group['cer_errors']/group['cer_units'], 6),
                'wer': round(group['wer_errors']/group['wer_units'], 6) if group['wer_units'] else None,
                'latency_p50_ms': round(statistics.median(values), 2),
                'latency_p95_ms': round(percentile(values, .95), 2),
                'latency_p99_ms': round(percentile(values, .99), 2),
            }
        output['languages'][lang] = metrics
        if lang in require_languages and data['sample_count'] < min_per_language:
            raise ValueError(f'Not enough measured samples for {lang}')
        if max_cer is not None and metrics['cer'] > max_cer:
            raise ValueError(f'CER acceptance threshold failed for {lang}')
        if max_wer is not None and metrics['wer'] is not None and metrics['wer'] > max_wer:
            raise ValueError(f'WER acceptance threshold failed for {lang}')
        if max_p95_ms is not None and metrics['latency_p95_ms'] > max_p95_ms:
            raise ValueError(f'Latency acceptance threshold failed for {lang}')
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--require-languages', nargs='*', choices=sorted(LANGUAGES), default=[])
    parser.add_argument('--min-per-language', type=int, default=1)
    parser.add_argument('--max-cer', type=float)
    parser.add_argument('--max-wer', type=float)
    parser.add_argument('--max-p95-ms', type=float)
    args = parser.parse_args()
    if not args.dataset.is_file() or args.min_per_language <= 0:
        parser.error('A nonempty evaluation dataset and positive sample minimum are required')
    report = evaluate(args.dataset, load_settings(), require_languages=set(args.require_languages),
                      min_per_language=args.min_per_language, max_cer=args.max_cer,
                      max_wer=args.max_wer, max_p95_ms=args.max_p95_ms)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
