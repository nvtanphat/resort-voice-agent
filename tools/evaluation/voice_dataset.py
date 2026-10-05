"""Fail-closed preflight for private, consented four-language field audio.

This is DATASET READINESS ONLY; no STT inference, speaker playback or quality
measurement is performed. Never include recordings or transcripts in output.

python -m tools.evaluation.voice_dataset --manifest /secure/voice/manifest.jsonl \
  --report /secure/reports/voice-preflight.json --min-test-per-condition 20
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

LANGUAGES = ('vi', 'en', 'zh', 'ko')
CONDITIONS = ('quiet', 'noisy')
SPLITS = ('calibration', 'test')
ALLOWED = {'.wav', '.ogg', '.webm', '.mp3', '.m4a'}


def evaluate(manifest: Path, *, min_test_per_condition: int = 20) -> dict:
    if min_test_per_condition < 1:
        raise ValueError('min_test_per_condition must be positive')
    root = manifest.resolve().parent
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError('A real, non-symlink manifest is required')
    raw = manifest.read_bytes()
    rows = raw.decode('utf-8').splitlines()
    counts = defaultdict(int)
    speakers = defaultdict(set)
    seen = set()
    for number, line in enumerate(rows, 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get('consent') is not True:
            raise ValueError(f'Row {number}: explicit consent required')
        lang, condition, split = (row.get(key) for key in ('language', 'condition', 'split'))
        if lang not in LANGUAGES or condition not in CONDITIONS or split not in SPLITS:
            raise ValueError(f'Row {number}: invalid language, environment or split')
        if not isinstance(row.get('reference'), str) or not row['reference'].strip():
            raise ValueError(f'Row {number}: reference transcript is required')
        if not isinstance(row.get('speaker_id'), str) or not row['speaker_id'].strip():
            raise ValueError(f'Row {number}: private speaker label is required')
        name = row.get('audio_path')
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise ValueError(f'Row {number}: audio_path must be relative')
        path = root / name
        if (path.is_symlink() or not path.resolve().is_relative_to(root)
                or path.suffix.lower() not in ALLOWED or not path.is_file()
                or path.stat().st_size < 44):
            raise ValueError(f'Row {number}: invalid or missing audio asset')
        resolved = path.resolve()
        if resolved in seen:
            raise ValueError(f'Row {number}: repeated audio asset')
        seen.add(resolved)
        speakers[split].add(row['speaker_id'])
        counts[(lang, condition, split)] += 1
    if not seen:
        raise ValueError('Empty data is not measurable')
    overlap = speakers['calibration'] & speakers['test']
    if overlap:
        raise ValueError('Calibration and test speakers must be disjoint')
    for lang in LANGUAGES:
        for condition in CONDITIONS:
            if counts[(lang, condition, 'test')] < min_test_per_condition:
                raise ValueError(f'Not enough held-out real audio: {lang}/{condition}')
    return {
        'type': 'consented_audio_dataset_preflight',
        'result': 'READY_TO_MEASURE_NOT_QUALITY_CERTIFIED',
        'manifest_sha256': hashlib.sha256(raw).hexdigest(),
        'sample_count': len(seen),
        'test_conditions': {lang: {condition: counts[(lang, condition, 'test')]
                                  for condition in CONDITIONS} for lang in LANGUAGES},
        'calibration_sample_count': sum(v for (lang, condition, split), v in counts.items()
                                        if split == 'calibration'),
        'heldout_sample_count': sum(v for (lang, condition, split), v in counts.items()
                                     if split == 'test'),
        'identity_and_transcript_exported': False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--min-test-per-condition', type=int, default=20)
    args = parser.parse_args()
    result = evaluate(args.manifest, min_test_per_condition=args.min_test_per_condition)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
