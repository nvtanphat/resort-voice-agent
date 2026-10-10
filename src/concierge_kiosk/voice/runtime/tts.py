"""In-process Piper synthesis, pinned TTS manifests and cancellable synthesis."""
from __future__ import annotations

import io
import atexit
import json
import multiprocessing
import os
import subprocess
import tempfile
import threading
import time
import wave
from functools import lru_cache
from pathlib import Path

from concierge_kiosk.core.domain_profile import supported_languages, voice_policy
from concierge_kiosk.core.settings import Settings

from .audio import MAX_TTS_BYTES, MAX_TTS_SECONDS, wav_properties
from .languages import LANGUAGE_WHISPER
from .rendering import speech_rendering


_PIPER_LOCK = threading.Lock()
_PIPER_WORKER_LOCK = threading.Lock()
_PIPER_WORKER = None
_PIPER_CONNECTION = None


def _piper_worker(connection):
    """Keep voice models warm in a process that the parent can terminate."""
    try:
        while True:
            model, text, synthesis = connection.recv()
            try:
                data = _inprocess_synthesis(Path(model), text, synthesis=synthesis)
                connection.send((True, data))
            except Exception:
                connection.send((False, None))
    except (EOFError, OSError):
        pass
    finally:
        connection.close()


def _stop_piper_worker():
    global _PIPER_WORKER, _PIPER_CONNECTION
    worker, connection = _PIPER_WORKER, _PIPER_CONNECTION
    _PIPER_WORKER = _PIPER_CONNECTION = None
    if connection is not None:
        connection.close()
    if worker is not None:
        if worker.is_alive():
            worker.terminate()
        if worker.pid is not None:
            worker.join(timeout=1)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
        worker.close()


atexit.register(_stop_piper_worker)


def _bounded_piper_synthesis(model: Path, text: str, *, timeout: float,
                             cancelled=lambda: False, synthesis=None) -> bytes:
    """Bound lock wait, model load and synthesis; discard timed-out workers."""
    global _PIPER_WORKER, _PIPER_CONNECTION
    deadline = time.monotonic() + timeout
    while not _PIPER_WORKER_LOCK.acquire(timeout=min(0.04, max(0, deadline - time.monotonic()))):
        if cancelled() or time.monotonic() >= deadline:
            raise RuntimeError('Local TTS cancelled or timed out')
    try:
        if cancelled() or time.monotonic() >= deadline:
            raise RuntimeError('Local TTS cancelled or timed out')
        if _PIPER_WORKER is None or not _PIPER_WORKER.is_alive():
            _stop_piper_worker()
            context = multiprocessing.get_context('spawn')
            parent, child = context.Pipe()
            _PIPER_CONNECTION = parent
            _PIPER_WORKER = context.Process(target=_piper_worker, args=(child,), daemon=True)
            try:
                _PIPER_WORKER.start()
            finally:
                child.close()
        _PIPER_CONNECTION.send((str(model), text, synthesis))
        while True:
            if cancelled() or time.monotonic() >= deadline:
                _stop_piper_worker()
                raise RuntimeError('Local TTS cancelled or timed out')
            if _PIPER_CONNECTION.poll(min(0.04, max(0, deadline - time.monotonic()))):
                ok, data = _PIPER_CONNECTION.recv()
                if cancelled() or time.monotonic() >= deadline:
                    _stop_piper_worker()
                    raise RuntimeError('Local TTS cancelled or timed out')
                if not ok:
                    raise RuntimeError('Local TTS unavailable')
                return data
            if not _PIPER_WORKER.is_alive():
                raise RuntimeError('Local TTS worker exited')
    except (OSError, EOFError, ValueError):
        _stop_piper_worker()
        raise RuntimeError('Local TTS worker unavailable') from None
    finally:
        _PIPER_WORKER_LOCK.release()


def inprocess_piper_available() -> bool:
    try:
        from importlib.util import find_spec
        return find_spec("piper") is not None
    except (ValueError, ImportError):
        return False


@lru_cache(maxsize=8)
def piper_voice(model: str, mtime_ns: int):
    """One loaded voice per file revision (load ~1 s; synthesis ~0.1-0.2 s)."""
    from piper import PiperVoice
    return PiperVoice.load(model)


def _inprocess_synthesis(model: Path, text: str, cancelled=lambda: False,
                          synthesis=None) -> bytes:
    """Synthesize in-process: no per-chunk process spawn or model reload, and
    no dependence on the child's stdin codec (cp1252 on Windows mangles vi/zh/ko)."""
    voice = piper_voice(str(model), model.stat().st_mtime_ns)
    output = io.BytesIO()
    with _PIPER_LOCK, wave.open(output, "wb") as wav:
        configured = False
        for chunk in voice.synthesize(text, syn_config=synthesis):
            if cancelled():
                raise RuntimeError("Speech turn cancelled")
            if not configured:
                wav.setnchannels(chunk.sample_channels)
                wav.setsampwidth(chunk.sample_width)
                wav.setframerate(chunk.sample_rate)
                configured = True
            wav.writeframes(chunk.audio_int16_bytes)
        if not configured:
            raise RuntimeError("Local TTS produced no audio")
    data = output.getvalue()
    if len(data) > MAX_TTS_BYTES:
        raise RuntimeError("Speech output exceeds limit")
    wav_properties(data, max_seconds=MAX_TTS_SECONDS)
    return data


def _piper_child_env() -> dict:
    """The Piper CLI is a Python program: force UTF-8 stdin on Windows."""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def tts_model_paths(cfg: Settings, language: str) -> tuple[Path, Path] | None:
    spec = (cfg.voice_tts or {}).get(language) or (cfg.voice_tts or {}).get('default') or {}
    engine = str(spec.get('engine') or 'piper')
    if engine != 'piper':
        return None
    configured = str(spec.get('model') or '')
    if not configured and not cfg.piper_models_dir:
        return None
    model = Path(configured) if configured else Path(cfg.piper_models_dir) / f'{language}.onnx'
    metadata = model.with_name(model.name + '.json')
    if not model.is_file() or model.is_symlink() or not metadata.is_file() or metadata.is_symlink():
        return None
    return model, metadata


def tts_manifest_path(cfg: Settings, language: str) -> str:
    spec = (cfg.voice_tts or {}).get(language) or (cfg.voice_tts or {}).get('default') or {}
    return str(spec.get('manifest_path') or '')


@lru_cache(maxsize=16)
def _read_tts_manifest(path: str) -> tuple[dict, dict]:
    if not path:
        return {}, {}
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('Configured TTS manifest is unavailable') from exc
    if not isinstance(payload, dict) or payload.get('format') != 'concierge-piper':
        raise RuntimeError('Configured TTS manifest has an unsupported format')
    synthesis = payload.get('synthesis') or {}
    if not isinstance(synthesis, dict):
        raise RuntimeError('Configured TTS manifest is incomplete')
    return payload, dict(synthesis)


def _tts_synthesis_config(cfg: Settings, language: str):
    _manifest, values = _read_tts_manifest(tts_manifest_path(cfg, language))
    try:
        from piper.config import SynthesisConfig
    except ImportError:
        return None, values
    allowed = {'speaker_id', 'length_scale', 'noise_scale', 'noise_w_scale',
               'normalize_audio', 'volume'}
    return SynthesisConfig(**{key: value for key, value in values.items() if key in allowed}), values


def synthesize(cfg: Settings, text: str, language: str) -> bytes:
    if language not in LANGUAGE_WHISPER:
        raise ValueError("Unsupported language")
    max_chars = int((cfg.voice_speech_plan or voice_policy()['speech_plan'])['max_chars'])
    if not 0 < len(text) <= max_chars or not text.strip():
        raise ValueError("TTS text length outside limit")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ValueError("Control characters in speech text")
    paths = tts_model_paths(cfg, language)
    if paths is None:
        raise RuntimeError("Licensed voice model or metadata for language not installed")
    model, _metadata = paths
    synthesis, synthesis_values = _tts_synthesis_config(cfg, language)
    if inprocess_piper_available():
        try:
            return _bounded_piper_synthesis(model, speech_rendering(text, language),
                                            timeout=cfg.tts_timeout_seconds, synthesis=synthesis)
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            raise RuntimeError("Local TTS unavailable") from exc
    with tempfile.TemporaryDirectory(prefix="concierge-tts-") as tmp:
        output = Path(tmp) / "speech.wav"
        try:
            command = [cfg.piper_executable, "--model", str(model), "--output_file", str(output)]
            for key, value in synthesis_values.items():
                if key in {'length_scale', 'noise_scale', 'noise_w_scale', 'volume'}:
                    command.extend([f'--{key}', str(value)])
                elif key == 'normalize_audio' and value is False:
                    command.append('--no-normalize')
            subprocess.run(command,
                           input=speech_rendering(text, language).encode("utf-8"), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=cfg.tts_timeout_seconds, check=True,
                           env=_piper_child_env())
        except (subprocess.TimeoutExpired, OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("Local TTS unavailable") from exc
        if not output.is_file() or output.is_symlink() or output.stat().st_size > MAX_TTS_BYTES:
            raise RuntimeError("Speech output missing or exceeds limit")
        data = output.read_bytes()
        try:
            wav_properties(data, max_seconds=MAX_TTS_SECONDS)
        except ValueError as exc:
            raise RuntimeError("Invalid generated audio") from exc
        return data


def synthesize_cancellable(cfg: Settings, text: str, language: str,
                           cancelled) -> bytes:
    """Terminate a Piper child when the owning guest turn is revoked.

    Used only for token-bound spoken replies. The legacy bounded adapter stays
    compatible with existing clients. No unverified/unsanctioned reply can use
    this endpoint: API validates the authorized source span first and after.
    """
    import time
    max_chars = int((cfg.voice_speech_plan or voice_policy()['speech_plan'])['max_chars'])
    if language not in LANGUAGE_WHISPER or not 0 < len(text) <= max_chars or not text.strip():
        raise ValueError('Invalid speech language or text')
    if any(ord(char) < 32 and char not in '\n\t' for char in text):
        raise ValueError('Control characters in speech text')
    paths = tts_model_paths(cfg, language)
    if paths is None:
        raise RuntimeError('Licensed voice model or metadata for language not installed')
    model, _metadata = paths
    if cancelled():
        raise RuntimeError('Speech turn cancelled')
    synthesis, synthesis_values = _tts_synthesis_config(cfg, language)
    rendered = speech_rendering(text, language)
    if inprocess_piper_available():
        try:
            return _bounded_piper_synthesis(model, rendered, cancelled=cancelled,
                                            timeout=cfg.tts_timeout_seconds, synthesis=synthesis)
        except (OSError, ValueError, ImportError) as exc:
            raise RuntimeError('Local TTS unavailable') from exc
    with tempfile.TemporaryDirectory(prefix='concierge-tts-') as tmp:
        output = Path(tmp) / 'speech.wav'
        process = None
        try:
            process = subprocess.Popen(
                [cfg.piper_executable, '--model', str(model), '--output_file', str(output)] +
                [item for key, value in synthesis_values.items()
                 for item in ((f'--{key}', str(value)) if key in {'length_scale', 'noise_scale', 'noise_w_scale', 'volume'} else ())],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=_piper_child_env())
            assert process.stdin is not None
            process.stdin.write(rendered.encode('utf-8'))
            process.stdin.close()
            deadline = time.monotonic() + cfg.tts_timeout_seconds
            while process.poll() is None:
                if cancelled() or time.monotonic() >= deadline:
                    raise RuntimeError('Local TTS cancelled or timed out')
                time.sleep(0.04)
            if cancelled() or process.returncode != 0:
                raise RuntimeError('Local TTS unavailable or cancelled')
            if (not output.is_file() or output.is_symlink() or
                    output.stat().st_size > MAX_TTS_BYTES):
                raise RuntimeError('Speech output missing or exceeds limit')
            data = output.read_bytes()
            wav_properties(data, max_seconds=MAX_TTS_SECONDS)
            return data
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise RuntimeError('Local TTS unavailable') from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
