"""Measure real local Piper output timing and audio validity for all four languages.

This measures latency and real-time factor, not pronunciation, MOS or human-rated
naturalness. It fails when any required licensed voice/model/runtime is missing.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.voice.runtime.audio import MAX_TTS_SECONDS, wav_properties
from concierge_kiosk.voice.runtime.tts import synthesize

PROMPTS = {
    'vi': 'Xin chào, tôi có thể giúp gì cho quý khách?',
    'en': 'Hello, how may I help you today?',
    'zh': '您好，请问有什么可以帮您？',
    'ko': '안녕하세요. 무엇을 도와드릴까요?',
}


def evaluate(cfg, *, repeats=3, max_p95_ms=None, max_rtf=None):
    if repeats < 1:
        raise ValueError('At least one measurement is required')
    report = {'type': 'real_tts_inference', 'note': 'Latency/RTF only; human listening evaluation is still required',
              'languages': {}}
    for lang, sentence in PROMPTS.items():
        readings = []
        factors = []
        for _ in range(repeats):
            start = time.perf_counter()
            wav = synthesize(cfg, sentence, lang)
            elapsed = (time.perf_counter() - start) * 1000
            rate, frames = wav_properties(wav, max_seconds=MAX_TTS_SECONDS)
            duration_ms = 1000 * frames / rate
            readings.append(elapsed)
            factors.append(elapsed / duration_ms)
        p95 = sorted(readings)[math.ceil(len(readings)*.95)-1]
        row = {'sample_count': repeats, 'latency_p50_ms': round(statistics.median(readings), 2),
               'latency_p95_ms': round(p95, 2),
               'latency_p99_ms': round(sorted(readings)[math.ceil(len(readings)*.99)-1], 2),
               'rtf_p50': round(statistics.median(factors), 3),
               'rtf_p95': round(sorted(factors)[math.ceil(len(factors)*.95)-1], 3),
               'rtf_p99': round(sorted(factors)[math.ceil(len(factors)*.99)-1], 3),
               'rtf_mean': round(statistics.mean(factors), 3)}
        report['languages'][lang] = row
        if max_p95_ms is not None and row['latency_p95_ms'] > max_p95_ms:
            raise ValueError(f'TTS latency threshold failed for {lang}')
        if max_rtf is not None and row['rtf_mean'] > max_rtf:
            raise ValueError(f'TTS RTF threshold failed for {lang}')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--max-p95-ms', type=float)
    parser.add_argument('--max-rtf', type=float)
    args = parser.parse_args()
    result = evaluate(load_settings(), repeats=args.repeats,
                      max_p95_ms=args.max_p95_ms, max_rtf=args.max_rtf)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
