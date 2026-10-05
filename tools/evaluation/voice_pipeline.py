"""Measure a RUNNING kiosk with real consented audio and actually installed models.

This is an HTTP first-WAV-byte measurement, NOT microphone-to-speaker or
physical playback latency. No transcript, guest recording or answer is written.
A nonzero exit means the hardware evaluation was not completed; no synthetic scores.

python -m tools.evaluation.voice_pipeline --dataset /secure/voice/manifest.jsonl \\
  --endpoint http://127.0.0.1:8000 --report /secure/reports/pipeline.json \\
  --min-per-language 20 --require-conditions quiet noisy
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.voice.runtime.adapters import _wav_properties, validate_audio, MAX_TTS_SECONDS
from tools.evaluation.voice import SUFFIX_MIME, percentile

LANGUAGES={'vi','en','zh','ko'}
STAGES=('stt_ms','ask_ms','tts_first_byte_ms','tts_total_ms','e2e_to_first_byte_ms')


def local_or_https(endpoint: str) -> str:
    url=urlsplit(endpoint)
    if not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('Use an origin URL with no path, credentials or query')
    if url.scheme=='https' or (url.scheme=='http' and url.hostname in {'localhost','127.0.0.1','::1'}):
        return endpoint.rstrip('/')
    raise ValueError('Non-loopback deployment benchmark requires HTTPS')


def load_dataset(path: Path, cfg) -> list[dict]:
    root=path.resolve().parent
    rows=[]
    for raw in path.read_text(encoding='utf-8').splitlines():
        if not raw.strip():continue
        row=json.loads(raw)
        if row.get('split') not in {'calibration','test'}:
            raise ValueError('Field pipeline requires an explicit held-out test split')
        if row['split']=='calibration':continue
        if row.get('consent') is not True or row.get('language') not in LANGUAGES or row.get('condition') not in {'quiet','noisy'}:
            raise ValueError('Each sample requires consent, supported language and quiet/noisy condition')
        relative=row.get('audio_path')
        if not isinstance(relative,str):raise ValueError('Audio path is required')
        file=root/relative
        if file.is_symlink() or not file.resolve().is_relative_to(root) or not file.is_file():
            raise ValueError('Only consented files inside the dataset directory are allowed')
        mime=SUFFIX_MIME.get(file.suffix.lower())
        if not mime:raise ValueError('Unsupported sample audio format')
        audio=file.read_bytes()
        validate_audio(audio,mime,max_bytes=cfg.max_audio_bytes,max_seconds=cfg.max_audio_seconds)
        rows.append({'language':row['language'],'condition':row['condition'],
                     'mime':mime,'audio':audio,'suffix':file.suffix})
    if not rows:raise ValueError('No real audio samples supplied')
    return rows


def summarize(samples: list[dict]) -> dict:
    if not samples:raise ValueError('No actual measurements')
    groups=defaultdict(list)
    for item in samples:
        groups[item['language'],item['condition']].append(item)
    report={'type':'real_http_voice_pipeline',
      'scope':'Real audio -> STT -> /ask -> TTS first response WAV byte. Not audible playback.',
      'split':'test', 'conditions':{}}
    for (language,condition),items in sorted(groups.items()):
        row={'sample_count':len(items)}
        for field in STAGES:
            values=[item[field] for item in items]
            row[field.replace('_ms','')+'_p50_ms']=round(statistics.median(values),2)
            row[field.replace('_ms','')+'_p95_ms']=round(percentile(values,.95),2)
            row[field.replace('_ms','')+'_p99_ms']=round(percentile(values,.99),2)
        report['conditions'].setdefault(language,{})[condition]=row
    return report


def evaluate(dataset: Path, cfg, *, endpoint: str, min_per_language: int=1,
             require_conditions: set[str]=frozenset()) -> dict:
    import httpx
    if min_per_language<1 or not require_conditions.issubset({'quiet','noisy'}):
        raise ValueError('Invalid evaluation threshold/condition')
    endpoint=local_or_https(endpoint)
    items=load_dataset(dataset,cfg)
    for language in LANGUAGES:
        if sum(row['language']==language for row in items)<min_per_language:
            raise ValueError('Missing minimum real audio for '+language)
        for condition in require_conditions:
            if not any(row['language']==language and row['condition']==condition for row in items):
                raise ValueError('Missing '+condition+' real audio for '+language)
    measurements=[]
    with httpx.Client(base_url=endpoint,timeout=cfg.stt_timeout_seconds+15,
                      trust_env=False,follow_redirects=False) as client:
        for row in items:
            session=client.post('/api/session')
            session.raise_for_status()
            csrf=session.json()['csrf_token']
            headers={'X-CSRF-Token':csrf}
            try:
                start=time.perf_counter()
                result=client.post('/api/audio/transcribe',headers=headers,
                    params={'language':row['language']},
                    files={'file':('sample'+row['suffix'],row['audio'],row['mime'])})
                result.raise_for_status()
                stt_end=time.perf_counter()
                text=result.json()['text']
                if not text:raise RuntimeError('Actual STT returned empty transcript')
                ask=client.post('/api/ask',headers=headers,
                    json={'query':text[:500],'language':row['language']})
                ask.raise_for_status()
                ask_end=time.perf_counter()
                answer=ask.json()['answer']
                if not answer:raise RuntimeError('Actual /ask returned no response')
                # The backend has already performed source validation. No partial
                # token or model-generated unverified statement is synthesized.
                audio_start=time.perf_counter();first_byte=None;parts=[];size=0
                with client.stream('POST','/api/audio/speak',headers=headers,
                        json={'text':answer[:750],'language':row['language']}) as speech:
                    speech.raise_for_status()
                    for chunk in speech.iter_bytes():
                        if not chunk:continue
                        if first_byte is None:first_byte=time.perf_counter()
                        size+=len(chunk)
                        if size>4_000_000:raise ValueError('TTS response exceeded production limit')
                        parts.append(chunk)
                audio_end=time.perf_counter()
                if first_byte is None:raise RuntimeError('No audio bytes produced')
                _wav_properties(b''.join(parts),max_seconds=MAX_TTS_SECONDS)
                measurements.append({'language':row['language'],'condition':row['condition'],
                    'stt_ms':(stt_end-start)*1000,
                    'ask_ms':(ask_end-stt_end)*1000,
                    'tts_first_byte_ms':(first_byte-audio_start)*1000,
                    'tts_total_ms':(audio_end-audio_start)*1000,
                    'e2e_to_first_byte_ms':(first_byte-start)*1000})
            finally:
                client.post('/api/session/end',headers=headers)
    return summarize(measurements)


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--endpoint',default='http://127.0.0.1:8000')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--min-per-language',type=int,default=1)
    parser.add_argument('--require-conditions',nargs='*',default=[])
    args=parser.parse_args()
    report=evaluate(args.dataset,load_settings(),endpoint=args.endpoint,
                    min_per_language=args.min_per_language,
                    require_conditions=set(args.require_conditions))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
