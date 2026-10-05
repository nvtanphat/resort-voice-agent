"""Bounded local WAV cache for source-backed kiosk speech.

Only callers that have already established the text is public/source-backed should
use this cache. It never replaces proof checks; playback endpoints revalidate live
evidence immediately before playing cached bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.voice.runtime.adapters import (
    MAX_TTS_BYTES, MAX_TTS_SECONDS, _tts_manifest_path, _tts_model_paths, _wav_properties, speech_rendering,
)
from concierge_kiosk.core.domain_profile import voice_policy

MAX_CACHE_BYTES = 256 * 1024 * 1024
TTS_CACHE_FORMAT_VERSION = "2"


def _cache_dir(cfg: Settings) -> Path:
    return Path(cfg.db_path).parent / "tts-cache"


def _voice_fingerprint(cfg: Settings, language: str) -> str | None:
    paths = _tts_model_paths(cfg, language)
    if paths is None:
        return None
    model, meta = paths
    try:
        m, j = model.stat(), meta.stat()
        executable = shutil.which(cfg.piper_executable) or cfg.piper_executable
        policy = json.dumps(voice_policy(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        policy_digest = hashlib.sha256(policy.encode('utf-8')).hexdigest()
        manifest = _tts_manifest_path(cfg, language)
        manifest_digest = ''
        if manifest:
            manifest_path = Path(manifest)
            if not manifest_path.is_file() or manifest_path.is_symlink():
                return None
            manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        return "|".join((str(model.resolve()), str(m.st_size), str(m.st_mtime_ns),
                         str(meta.resolve()), str(j.st_size), str(j.st_mtime_ns),
                         str(executable), manifest_digest, TTS_CACHE_FORMAT_VERSION, policy_digest))
    except OSError:
        return None


def cache_key(cfg: Settings, text: str, language: str) -> str | None:
    fingerprint = _voice_fingerprint(cfg, language)
    if fingerprint is None:
        return None
    rendered = speech_rendering(text, language)
    payload = f"{language}\0{fingerprint}\0{rendered}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_cached_wav(cfg: Settings, text: str, language: str) -> bytes | None:
    key = cache_key(cfg, text, language)
    if key is None:
        return None
    path = _cache_dir(cfg) / f"{key}.wav"
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_TTS_BYTES:
            return None
        data = path.read_bytes()
        _wav_properties(data, max_seconds=MAX_TTS_SECONDS)
        try:
            os.utime(path, None)
        except OSError:
            pass
        return data
    except (OSError, ValueError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _prune(folder: Path) -> None:
    try:
        files = [p for p in folder.glob("*.wav") if p.is_file() and not p.is_symlink()]
        total = sum(p.stat().st_size for p in files)
        if total <= MAX_CACHE_BYTES:
            return
        for path in sorted(files, key=lambda p: p.stat().st_mtime_ns):
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size
            if total <= MAX_CACHE_BYTES:
                break
    except OSError:
        return


def store_cached_wav(cfg: Settings, text: str, language: str, data: bytes) -> bool:
    key = cache_key(cfg, text, language)
    if key is None or not data or len(data) > MAX_TTS_BYTES:
        return False
    try:
        _wav_properties(data, max_seconds=MAX_TTS_SECONDS)
    except ValueError:
        return False
    folder = _cache_dir(cfg)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{key}.wav"
        if target.is_symlink():
            return False
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".tts-", suffix=".wav", delete=False) as handle:
            handle.write(data)
            temp = Path(handle.name)
        os.replace(temp, target)
        _prune(folder)
        return True
    except OSError:
        try:
            temp.unlink(missing_ok=True)  # type: ignore[name-defined]
        except (OSError, UnboundLocalError):
            pass
        return False
