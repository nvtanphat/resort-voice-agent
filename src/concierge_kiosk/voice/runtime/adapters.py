"""Offline-first, bounded speech adapters for a guest-facing kiosk.

No recording/transcript is retained here. STT input is inspected before decoder/model
initialization; caller-controlled MIME is never treated as proof of a valid container.

Speech-to-text lives here; rendering, audio and TTS live in sibling modules and are
re-exported below so existing ``adapters`` imports keep working.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
import time
import unicodedata
import wave
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore

from concierge_kiosk.core.dataset_layout import ALIASES, SERVICE_CATALOG, dataset_path
from concierge_kiosk.core.domain_profile import voice_policy
from concierge_kiosk.core.settings import Settings

from .audio import MAX_TTS_BYTES, MAX_TTS_SECONDS, MIME_FORMATS, audio_format, validate_audio
from .languages import LANGUAGE_WHISPER
from .rendering import speech_rendering
from .tts import inprocess_piper_available, piper_voice, synthesize, synthesize_cancellable, tts_model_paths


_stt_threads_max = 8


_stt_prompt_labels = 8


_STT_DECODE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='stt-decode')


_STT_DECODE_SLOT = BoundedSemaphore(1)


def configure_stt_runtime(*, threads_max: int, prompt_labels: int) -> None:
    global _stt_threads_max, _stt_prompt_labels
    if not 1 <= int(threads_max) <= 64 or not 0 <= int(prompt_labels) <= 32:
        raise ValueError("Invalid STT runtime budget")
    _stt_threads_max = int(threads_max)
    _stt_prompt_labels = int(prompt_labels)


@dataclass(frozen=True)
class DetectedTranscript:
    text: str
    detected_language: str | None
    language_probability: float | None
    confidence: float | None = None
    reject_reason: str | None = None


def voice_assets(cfg: Settings) -> dict:
    """Cheap provisioning check, not a substitute for actual inference/quality gates."""
    model = Path(cfg.whisper_model_path) if cfg.whisper_model_path else None
    stt = bool(model and model.is_dir() and (model / "config.json").is_file() and
               (model / "model.bin").is_file())
    try:
        from importlib.util import find_spec
        stt = stt and find_spec("faster_whisper") is not None
    except (ValueError, ImportError):
        stt = False
    binary = bool(shutil.which(cfg.piper_executable)) or inprocess_piper_available()
    languages = [lang for lang in LANGUAGE_WHISPER if binary and
                 tts_model_paths(cfg, lang) is not None]
    return {"stt_available": stt, "tts_languages": languages}


@lru_cache(maxsize=2)
def _whisper(path: str):
    from faster_whisper import WhisperModel
    if not Path(path).is_dir():
        raise FileNotFoundError("Local Whisper model not installed")
    # CTranslate2's default thread count leaves most cores idle; beyond ~8
    # threads there is no gain and the local SLM needs the remaining cores.
    threads = max(1, min(_stt_threads_max, os.cpu_count() or 4))
    return WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=threads,
                        local_files_only=True)


def warm_voice_models(cfg: Settings) -> list[str]:
    """Load configured STT/TTS models ahead of the first guest turn (~1 s each)."""
    warmed: list[str] = []
    if cfg.whisper_model_path and Path(cfg.whisper_model_path).is_dir():
        _whisper(cfg.whisper_model_path)
        warmed.append("whisper")
    if cfg.piper_models_dir and inprocess_piper_available():
        for language in LANGUAGE_WHISPER:
            paths = tts_model_paths(cfg, language)
            if paths is not None:
                model, _metadata = paths
                piper_voice(str(model), model.stat().st_mtime_ns)
                warmed.append(f"piper:{language}")
    return warmed


def _append_hotword(words: list[str], seen: set[str], value: object) -> None:
    if not isinstance(value, str):
        return
    word = " ".join(value.split())
    key = _speech_key(word)
    if word and key and key not in seen:
        seen.add(key)
        words.append(word)


def _configured_dataset_file(dataset: str, relative: str) -> Path:
    """Resolve a configured dataset path, including isolated test fixtures.

    Production always supplies the canonical ``datasets/`` layout.  The direct
    file fallback keeps this small adapter usable with an isolated dataset
    directory used by config-contract tests without reintroducing a property
    specific path into runtime code.
    """
    root = Path(dataset)
    canonical = dataset_path(relative, root)
    if canonical.is_file() and not canonical.is_symlink():
        return canonical
    direct = root / Path(relative).name
    if direct.is_file() and not direct.is_symlink():
        return direct
    # Contract fixtures may use the human-readable legacy separator while the
    # canonical release keeps the schema filename with underscores.
    return root / Path(relative).name.replace("_", "-")


@lru_cache(maxsize=32)
def _stt_prompt(map_path: str, map_sha256: str, language: str,
                structured_dataset_dir: str = "") -> str | None:
    """Build non-authoritative STT vocabulary from pinned property data.

    The source list and term budget live in the domain profile. This prompt is
    only a decoder hint: it never becomes evidence, an entity match, or an
    authorization decision.
    """
    policy = voice_policy().get("stt_hotwords") or {}
    sources = policy.get("sources") if isinstance(policy.get("sources"), list) else ()
    max_terms = policy.get("max_terms", _stt_prompt_labels)
    max_terms = int(max_terms) if isinstance(max_terms, int) and not isinstance(max_terms, bool) else _stt_prompt_labels
    prefix = str((voice_policy().get("stt_prompt_prefix") or {}).get(language, ""))
    words: list[str] = []
    seen: set[str] = set()

    try:
        if "map_labels" in sources and map_path:
            raw = Path(map_path).read_bytes()
            if raw and hashlib.sha256(raw).hexdigest() == map_sha256:
                payload = json.loads(raw)
                for place in payload.get("places", []):
                    _append_hotword(words, seen, (place.get("labels") or {}).get(language))
    except (OSError, ValueError, AttributeError, TypeError):
        pass

    dataset = structured_dataset_dir or None
    try:
        if dataset and "service_names" in sources:
            payload = json.loads(_configured_dataset_file(dataset, SERVICE_CATALOG).read_text(encoding="utf-8"))
            for service in payload if isinstance(payload, list) else ():
                names = service.get("names_by_locale") or {}
                _append_hotword(words, seen, names.get(language))
    except (OSError, ValueError, AttributeError, TypeError):
        pass

    try:
        if dataset and "aliases" in sources:
            payload = json.loads(_configured_dataset_file(dataset, ALIASES).read_text(encoding="utf-8"))
            for aliases in (payload.get("aliases_by_entity") or {}).values():
                _append_hotword(words, seen, (aliases or {}).get(language))
    except (OSError, ValueError, AttributeError, TypeError):
        pass

    extras = (policy.get("extra") or {}).get(language, [])
    for value in extras:
        _append_hotword(words, seen, value)
    if max_terms <= 0:
        return prefix or None
    return (prefix + ", ".join(words[:max_terms])).strip() or None


_LEGACY_STT_DECODE = {
    "beam_size": 1,
    "vad_filter": True,
    "condition_on_previous_text": False,
    "no_speech_threshold": 0.6,
    "log_prob_threshold": -1.0,
    "compression_ratio_threshold": 2.4,
    "temperature": (0.0, 0.2, 0.4),
}


@lru_cache(maxsize=16)
def _read_stt_manifest(path: str) -> tuple[dict, dict]:
    if not path:
        return dict(_LEGACY_STT_DECODE), {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Configured STT manifest is unavailable") from exc
    if not isinstance(payload, dict) or payload.get("format") != "concierge-whisper":
        raise RuntimeError("Configured STT manifest has an unsupported format")
    decode = payload.get("decode")
    acceptance = payload.get("acceptance") or {}
    if not isinstance(decode, dict) or not isinstance(acceptance, dict):
        raise RuntimeError("Configured STT manifest is incomplete")
    values = dict(_LEGACY_STT_DECODE)
    values.update(decode)
    return values, dict(acceptance)


def _stt_configuration(cfg: Settings, language: str | None) -> tuple[str, dict, dict]:
    configured = cfg.voice_stt_models or {}
    spec = configured.get(language or "") or configured.get("default") or {}
    # A development-only CONCIERGE_WHISPER_MODEL_PATH override remains useful
    # for local experiments; the pinned profile supplies the normal path.
    model_path = str(cfg.whisper_model_path or spec.get("model_path") or "")
    manifest_path = str(spec.get("manifest_path") or "")
    decode, acceptance = _read_stt_manifest(manifest_path)
    return model_path, decode, acceptance


def _speech_key(value: object) -> str:
    """Case/punctuation-insensitive key: "Thank you." == "thank you"."""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join("".join(ch if unicodedata.category(ch)[0] not in "PS" else " " for ch in text).split())


def _is_repetition_loop(text: str) -> bool:
    """True when one word makes up most of a long-ish transcript (Whisper loop)."""
    words = re.findall(r"\w+", text.casefold())
    if len(words) < 5:
        return False
    for size in (1, 2, 3):
        grams = [tuple(words[index:index + size]) for index in range(len(words) - size + 1)]
        if grams and max(grams.count(gram) for gram in set(grams)) * size >= 0.6 * len(words) and                 max(grams.count(gram) for gram in set(grams)) >= 3:
            return True
    return False


def _script_language(text: str, fallback: str | None) -> tuple[str | None, float | None]:
    """Detect an obvious CJK script mismatch after a UI-language decode.

    Final decoding is intentionally bounded to the UI language. Script checks
    only suggest a language switch for unambiguous Hangul or Han output; they do
    not replace speech recognition or change the decoded text.
    """
    hangul = 0
    han = 0
    script_languages = voice_policy().get('script_languages', {})
    hangul_language = script_languages.get('hangul')
    han_language = script_languages.get('han')
    for char in text:
        name = unicodedata.name(char, '')
        hangul += name.startswith('HANGUL ')
        han += name.startswith('CJK UNIFIED IDEOGRAPH') or name == 'IDEOGRAPHIC ITERATION MARK'
    if hangul >= 2 and hangul >= han and isinstance(hangul_language, str):
        return hangul_language, 1.0
    if han >= 2 and isinstance(han_language, str):
        return han_language, 1.0
    return fallback, None


def _transcribe(cfg: Settings, audio: bytes, language: str | None,
                fallback_language: str | None = None) -> DetectedTranscript:
    if language is not None and language not in LANGUAGE_WHISPER:
        raise ValueError("Unsupported language")
    model_path, decode, acceptance = _stt_configuration(cfg, language or fallback_language)
    if not model_path:
        raise RuntimeError("No local STT model configured")
    if not audio or len(audio) > cfg.max_audio_bytes:
        raise ValueError("Audio empty or too large")
    extension = audio_format(audio)
    if extension is None:
        raise ValueError("Unknown audio format")
    validate_audio(audio, next(m for m, ext in MIME_FORMATS.items() if ext == extension),
                   max_bytes=cfg.max_audio_bytes, max_seconds=cfg.max_audio_seconds)
    decode_deadline = time.monotonic() + max(0.1, float(cfg.stt_timeout_seconds))
    with tempfile.TemporaryDirectory(prefix="concierge-audio-") as tmp:
        file = Path(tmp) / f"utterance.{extension}"
        file.write_bytes(audio)
        try:
            if time.monotonic() >= decode_deadline:
                raise TimeoutError('STT decode deadline reached before model load')
            model = _whisper(model_path)
            source = str(file)
            probability = None
            decode_language = language or fallback_language
            no_speech_threshold = float(acceptance.get(
                "no_speech_threshold", decode.get("no_speech_threshold", 0.6)))
            log_prob_threshold = float(acceptance.get(
                "log_prob_threshold", decode.get("log_prob_threshold", -1.0)))
            compression_threshold = float(acceptance.get(
                "compression_ratio_threshold",
                decode.get("compression_ratio_threshold", 2.4)))
            min_confidence = float(acceptance.get("min_confidence", 0.0))
            transcribe_options = {
                key: value for key, value in decode.items()
                if key in {"beam_size", "vad_filter", "condition_on_previous_text",
                           "no_speech_threshold", "log_prob_threshold",
                           "compression_ratio_threshold", "temperature"}
            }
            try:
                with wave.open(io.BytesIO(audio), 'rb') as wav:
                    duration = wav.getnframes() / max(1, wav.getframerate())
            except (OSError, wave.Error, ZeroDivisionError):
                duration = None
            if duration is not None and duration <= cfg.stt_short_audio_seconds:
                transcribe_options['temperature'] = (float(cfg.stt_short_audio_temperature),)
            transcribe_options["vad_parameters"] = {
                "max_speech_duration_s": cfg.max_audio_seconds}
            # A timed-out faster-whisper future may still be unwinding its
            # lazy generator in the single worker.  Wait briefly for that
            # worker to release the slot instead of turning a harmless
            # scheduling race into a false permanent STT outage.  The wait is
            # still bounded by the new turn's decode deadline.
            slot_wait = min(0.25, max(0.001, decode_deadline - time.monotonic()))
            if not _STT_DECODE_SLOT.acquire(timeout=slot_wait):
                raise TimeoutError('STT decoder occupied by an earlier timed-out request')

            def decode_segments():
                decoded_segments, decode_info = model.transcribe(
                    source, language=(LANGUAGE_WHISPER[decode_language] if decode_language is not None else None),
                    initial_prompt=(_stt_prompt(cfg.map_release_path, cfg.map_release_sha256,
                                                decode_language, cfg.structured_dataset_dir)
                                    if decode_language is not None else None),
                    **transcribe_options)
                # faster-whisper returns a lazy generator; the expensive work
                # may happen on the first ``next`` instead of in transcribe().
                materialized = []
                for index, segment in enumerate(decoded_segments):
                    materialized.append(segment)
                    if index >= 128:
                        break
                return materialized, decode_info

            try:
                decode_future = _STT_DECODE_EXECUTOR.submit(decode_segments)
            except BaseException:
                _STT_DECODE_SLOT.release()
                raise
            decode_future.add_done_callback(lambda _done: _STT_DECODE_SLOT.release())
            try:
                segments, info = decode_future.result(
                    timeout=max(0.001, decode_deadline - time.monotonic()))
            except FutureTimeout as exc:
                decode_future.cancel()
                raise TimeoutError('STT decoder response deadline exceeded') from exc
            chunks = []
            confidence_scores: list[float] = []
            hallucination_phrases = frozenset(
                _speech_key(value) for value in cfg.stt_hallucination_phrases if _speech_key(value))
            length = 0
            reject_reasons: set[str] = set()
            for n, segment in enumerate(segments):
                if time.monotonic() >= decode_deadline:
                    raise TimeoutError('STT decode deadline exceeded while reading segments')
                if n >= 128 or length >= 1000:
                    break
                part = unicodedata.normalize("NFC", str(segment.text))
                part = re.sub(r"[\x00-\x1f\x7f]", " ", part).strip()
                no_speech = getattr(segment, "no_speech_prob", None)
                avg_logprob = getattr(segment, "avg_logprob", None)
                if (isinstance(no_speech, (int, float)) and no_speech > no_speech_threshold and
                        isinstance(avg_logprob, (int, float)) and avg_logprob < log_prob_threshold):
                    reject_reasons.add("no_speech")
                    continue
                compression = getattr(segment, "compression_ratio", None)
                if ((isinstance(compression, (int, float)) and compression > compression_threshold) or
                        _is_repetition_loop(part)):
                    reject_reasons.add("decoder_loop")
                    continue  # decoder loop ("tất cả tất cả tất cả ..."), not speech
                if _speech_key(part) in hallucination_phrases and (
                        not isinstance(no_speech, (int, float)) or no_speech > 0.3 or
                        (isinstance(avg_logprob, (int, float)) and avg_logprob < -0.8)):
                    # A stock outro ("Thank you.") only counts as a hallucination
                    # when the decoder itself doubts there was speech; a guest who
                    # really says "thank you" keeps the transcript.
                    reject_reasons.add("hallucination")
                    continue
                # exp(avg_logprob) tracks real recognition quality; 1-no_speech_prob
                # stays near 1.0 even for a badly misheard sentence.
                if isinstance(avg_logprob, (int, float)):
                    confidence_scores.append(max(0.0, min(1.0, math.exp(float(avg_logprob)))))
                elif isinstance(no_speech, (int, float)):
                    confidence_scores.append(max(0.0, min(1.0, 1.0 - float(no_speech))))
                if part:
                    chunks.append(part)
                    length += len(part) + 1
            detected = str(getattr(info, "language", "") or "").lower() or None
            if detected not in LANGUAGE_WHISPER:
                detected = None
            if decode_language is not None:
                detected = decode_language
                script_language, script_probability = _script_language(" ".join(chunks), decode_language)
                if script_language != decode_language:
                    detected, probability = script_language, script_probability
            if probability is None:
                probability = getattr(info, "language_probability", None)
            probability = float(probability) if isinstance(probability, (int, float)) else None
            confidence = (sum(confidence_scores) / len(confidence_scores)
                          if confidence_scores else None)
            text = " ".join(chunks)[:1000].strip()
            reject_reason = None
            if not text:
                reject_reason = next((reason for reason in
                                      ("no_speech", "hallucination", "decoder_loop")
                                      if reason in reject_reasons), "empty")
            elif confidence is not None and confidence < min_confidence:
                reject_reason = "low_confidence"
            return DetectedTranscript(text, detected, probability, confidence, reject_reason)
        except Exception as exc:
            raise RuntimeError("Local speech decoding failed") from exc


def transcribe(cfg: Settings, audio: bytes, language: str) -> str:
    """UI-language constrained STT used only for provisional/partial previews."""
    return _transcribe(cfg, audio, language).text


def transcribe_detected(cfg: Settings, audio: bytes, language: str) -> DetectedTranscript:
    """Final STT with Whisper language detection independent of the UI language."""
    if language not in LANGUAGE_WHISPER:
        raise ValueError("Unsupported language")
    return _transcribe(cfg, audio, None, fallback_language=language)


__all__ = [
    'DetectedTranscript',
    'LANGUAGE_WHISPER',
    'MAX_TTS_BYTES',
    'MAX_TTS_SECONDS',
    'MIME_FORMATS',
    'configure_stt_runtime',
    'speech_rendering',
    'synthesize',
    'synthesize_cancellable',
    'transcribe',
    'transcribe_detected',
    'validate_audio',
    'voice_assets',
    'warm_voice_models',
]
