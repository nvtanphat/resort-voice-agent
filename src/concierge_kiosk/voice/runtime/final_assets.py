"""Pinned final STT/TTS asset inventory and one-time production probes."""
from __future__ import annotations

import hashlib
import json
import shutil
from functools import lru_cache
from pathlib import Path

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.voice.runtime.adapters import LANGUAGE_WHISPER, synthesize

_FORMAT = 'concierge-final-voice'


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _tree(root: Path, *, max_files: int = 4096) -> dict[str, str]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Voice model directory unavailable')
    files: dict[str, str] = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Voice model tree must not contain symlinks')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('Unexpected voice model entry')
        name = path.relative_to(root).as_posix()
        if not name or len(name) > 320 or len(files) >= max_files:
            raise ValueError('Voice model inventory exceeds limits')
        files[name] = _digest(path)
    if not files:
        raise ValueError('Voice model directory is empty')
    return files


def final_voice_manifest(cfg: Settings) -> dict:
    whisper = Path(cfg.whisper_model_path)
    piper = Path(cfg.piper_models_dir)
    executable_name = shutil.which(cfg.piper_executable)
    if not executable_name:
        raise ValueError('Piper executable unavailable')
    executable = Path(executable_name)
    if executable.is_symlink():
        executable = executable.resolve()
    if not executable.is_file():
        raise ValueError('Piper executable unavailable')
    piper_files = _tree(piper, max_files=256)
    required = {f'{language}.onnx' for language in LANGUAGE_WHISPER} | {
        f'{language}.onnx.json' for language in LANGUAGE_WHISPER}
    if not required.issubset(piper_files):
        raise ValueError('Required multilingual Piper assets unavailable')
    return {
        'format': _FORMAT,
        'whisper_files': _tree(whisper),
        'piper_files': piper_files,
        'piper_executable': {'name': executable.name, 'sha256': _digest(executable)},
    }


def verify_final_voice_manifest(cfg: Settings, manifest_path: str) -> bool:
    try:
        path = Path(manifest_path)
        if (not manifest_path or path.is_symlink() or not path.is_file()
                or path.stat().st_size > 1_000_000):
            return False
        expected = json.loads(path.read_text(encoding='utf-8'))
        return expected == final_voice_manifest(cfg)
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return False


def final_voice_identity(cfg: Settings, manifest_path: str) -> tuple | None:
    """Cheap metadata identity used only to invalidate a previously verified pin."""
    try:
        paths: list[Path] = [Path(manifest_path)]
        executable_name = shutil.which(cfg.piper_executable)
        if not executable_name:
            return None
        paths.append(Path(executable_name).resolve())
        for root_name in (cfg.whisper_model_path, cfg.piper_models_dir):
            root = Path(root_name)
            if not root.is_dir() or root.is_symlink():
                return None
            paths.extend(path for path in sorted(root.rglob('*')) if path.is_file())
        if len(paths) > 5000 or any(path.is_symlink() for path in paths):
            return None
        return tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths)
    except OSError:
        return None


@lru_cache(maxsize=4)
def _check_cached(cfg: Settings, manifest_path: str, identity: tuple) -> bool:
    return verify_final_voice_manifest(cfg, manifest_path)


def check_final_voice_manifest(cfg: Settings, manifest_path: str) -> bool:
    identity = final_voice_identity(cfg, manifest_path)
    return bool(identity and _check_cached(cfg, manifest_path, identity))


def probe_final_tts(cfg: Settings) -> None:
    """Exercise every configured final voice and validate the resulting WAV."""
    for language in sorted(LANGUAGE_WHISPER):
        data = synthesize(cfg, i18n_text('voice.probe_ready', language), language)
        if not data.startswith(b'RIFF') or data[8:12] != b'WAVE':
            raise RuntimeError(f'Invalid Piper readiness output for {language}')
