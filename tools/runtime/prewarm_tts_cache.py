#!/usr/bin/env python3
"""Pre-synthesize bounded public knowledge sentences into the local TTS cache.

This tool is optional and intentionally fails if local licensed voice assets are
not provisioned. Runtime proof/citation checks remain authoritative; cached WAV
bytes only replace synthesis work.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.voice.runtime.adapters import synthesize_cancellable
from concierge_kiosk.voice.runtime.tts_cache import store_cached_wav

LANGUAGES = {'vi', 'en', 'zh', 'ko'}


def sentences(path: Path):
    text = path.read_text(encoding='utf-8')
    for raw in re.split(r'(?<=[.!?。！？])\s+|\n+', text):
        line = raw.strip()
        if (not line or line.startswith(('#', '-', '*', '```')) or
                line.lower().startswith(('source:', 'revision:', 'effective_'))):
            continue
        if 20 <= len(line) <= 300:
            yield line


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='knowledge/compiled')
    parser.add_argument('--limit', type=int, default=2000)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    cfg = load_settings()
    root = Path(args.root)
    count = 0
    for path in sorted(root.rglob('*.md')):
        lang = next((part for part in path.parts if part in LANGUAGES), None)
        if lang is None:
            continue
        for text in sentences(path):
            if count >= args.limit:
                print(f'Prepared {count} cache candidates (limit reached).')
                return 0
            if args.dry_run:
                count += 1
                continue
            audio = synthesize_cancellable(cfg, text, lang, lambda: False)
            if store_cached_wav(cfg, text, lang, audio):
                count += 1
    print(f'Prepared {count} source-backed TTS cache entries.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
