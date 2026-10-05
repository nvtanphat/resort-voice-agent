"""Offline-first, bounded speech adapters for a guest-facing kiosk.

No recording/transcript is retained here. STT input is inspected before decoder/model
initialization; caller-controlled MIME is never treated as proof of a valid container.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import wave
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass
from threading import BoundedSemaphore

from concierge_kiosk.core.dataset_layout import ALIASES, SERVICE_CATALOG, dataset_path
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.core.domain_profile import supported_languages, voice_policy
from concierge_kiosk.core.terminology import normalize_terminology

LANGUAGE_WHISPER = {language: language for language in supported_languages()}
MIME_FORMATS = {
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav",
    "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
}
MAX_TTS_BYTES = 4_000_000
MAX_TTS_SECONDS = 30
_stt_threads_max = 8
_stt_prompt_labels = 8
_stt_prompt_languages = frozenset({"vi", "ko", "zh"})
_STT_DECODE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='stt-decode')
_STT_DECODE_SLOT = BoundedSemaphore(1)
def configure_stt_runtime(*, threads_max: int, prompt_labels: int,
                          prompt_languages: tuple[str, ...]) -> None:
    global _stt_threads_max, _stt_prompt_labels, _stt_prompt_languages
    if not 1 <= int(threads_max) <= 64 or not 0 <= int(prompt_labels) <= 32:
        raise ValueError("Invalid STT runtime budget")
    _stt_threads_max = int(threads_max)
    _stt_prompt_labels = int(prompt_labels)
    _stt_prompt_languages = frozenset(prompt_languages)


@dataclass(frozen=True)
class DetectedTranscript:
    text: str
    detected_language: str | None
    language_probability: float | None
    confidence: float | None = None
    reject_reason: str | None = None



def speech_rendering(text: str, language: str) -> str:
    """Non-authoritative pronunciation hints for Piper, not response editing.

    The API authorizes the ORIGINAL answer text before calling this adapter;
    only the bytes sent to the speech engine are transformed. Keep every
    numerical value unchanged, and never transliterate unknown hotel names.
    Local property names must be provided by an appropriate licensed voice.
    """
    if language not in LANGUAGE_WHISPER:
        raise ValueError('Unsupported speech language')
    policy = voice_policy()
    steps = tuple((policy.get('normalization') or {}).get(language, ()))
    if not steps:
        steps = ('pronunciation_aliases', 'email_rendering', 'number_rendering',
                 'domain_money', 'quantity_rendering')
    result = normalize_terminology(_strip_markdown(text), language)
    # One pass, so a spoken range is never re-parsed as single clock times.
    if 'clock_rendering' in steps or not steps:
        result = _CLOCK_RANGE.sub(lambda m: _spoken_time(m, language), result)
    if 'pronunciation_aliases' in steps:
        for alias in (policy.get('pronunciation_aliases', {}) or {}).get(language, ()):
            result = re.sub(alias['pattern'], alias['replacement'], result, flags=re.I)
    if 'email_rendering' in steps:
        result = _render_emails(result, language)
    if 'number_rendering' in steps:
        result = _render_numbers(result, language, render_money='domain_money' in steps)
    elif 'domain_money' in steps:
        result = _render_money(result, language, (policy.get('number_rendering', {}).get('money') or {}))
    if 'quantity_rendering' in steps:
        result = _render_quantities(result, language, policy.get('quantity_units') or {})
    return result if len(result) <= 1200 else text


def _render_numbers(text: str, language: str, *, render_money: bool = True) -> str:
    """Render room/phone/extension identifiers without changing screen text."""
    rendering = voice_policy().get('number_rendering', {})
    digits = rendering.get('digits', {}).get(language)
    categories = set(rendering.get('digit_by_digit', ()))
    if not isinstance(digits, list) or len(digits) != 10:
        return text

    label_config = rendering.get('labels', {}).get(language, {})
    separators = (rendering.get('separators') or {}).get(language, {})
    label_to_key = {
        str(label).casefold(): key
        for key, labels in label_config.items()
        if key in categories and isinstance(labels, list)
        for label in labels
        if isinstance(label, str) and label.strip()
    }
    if not label_to_key:
        label_to_key = {
            'room': 'room_number', 'phone': 'phone', 'extension': 'extension'}
    label_pattern = '|'.join(re.escape(label) for label in sorted(label_to_key, key=len, reverse=True))

    def spelled(match: re.Match) -> str:
        label, number = match.group(1), match.group(2)
        key = label_to_key.get(label.casefold())
        if key is None:
            return match.group(0)
        if key not in categories:
            return match.group(0)
        plus = str(separators.get('plus', '')).strip()
        prefix = f'{plus} ' if number.strip().startswith('+') and plus else ''
        return f'{label} {prefix}' + ' '.join(digits[int(char)] for char in number if char.isdigit())

    # Keep the category marker in the spoken result; Piper then reads each
    # digit separately, which is safer for rooms and PBX extensions.
    labelled = re.compile(
        rf'(?<!\w)({label_pattern})\s*(?:number\s*)?(?:[:#]\s*)?'
        r'(\+?\d[\d\s./-]*\d)(?!\w)', re.I)
    result = labelled.sub(spelled, text)

    slash = str(separators.get('slash', '')).strip()
    if slash:
        def separated(match: re.Match) -> str:
            left, right = match.group(1), match.group(2)
            return (' '.join(digits[int(char)] for char in left) +
                    f' {slash} ' +
                    ' '.join(digits[int(char)] for char in right))

        result = re.sub(r'(?<!\w)(\d{3,6})/(\d{3,6})(?!\w)', separated, result)

    return _render_money(result, language, rendering.get('money') or {}) if render_money else result


def _render_quantities(text: str, language: str, units: dict) -> str:
    """Speak a catalog quantity marker naturally (``x2`` must not become 'ex two')."""
    unit = str(units.get(language, '')).strip()
    if not unit:
        return text
    return re.sub(r'(?<![\w])x\s*(\d+)(?!\w)', rf'\1 {unit}', text, flags=re.I)


_EMAIL_ADDRESS = re.compile(
    r'(?<![\w.+-])([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)(?![\w.-])')


def _render_emails(text: str, language: str) -> str:
    """Speak email separators without changing the visible answer."""
    separators = (voice_policy().get('number_rendering', {}).get('separators') or {}).get(language, {})
    at = str(separators.get('email_at', '')).strip()
    dot = str(separators.get('email_dot', '')).strip()
    if not at or not dot:
        return text

    def render(match: re.Match) -> str:
        domain = match.group(2).replace('.', f' {dot} ')
        return f'{match.group(1)} {at} {domain}'

    return _EMAIL_ADDRESS.sub(render, text)


_GROUPED_AMOUNT = re.compile(r'^\d{1,3}(?:[.,]\d{3})+$')
_DECIMAL_AMOUNT = re.compile(r'^(\d+)[.,](\d{1,2})$')


def _parse_amount(raw: str) -> tuple[int, str]:
    """Return (integer part, decimal digits) for "1,500,000", "1.500.000", "12.50"."""
    if _GROUPED_AMOUNT.match(raw):
        return int(re.sub(r'[.,]', '', raw)), ''
    decimal = _DECIMAL_AMOUNT.match(raw)
    if decimal:
        return int(decimal.group(1)), decimal.group(2)
    return int(re.sub(r'\D', '', raw)), ''


def _spoken_amount(number: int, groups: list, joiner: str) -> str:
    parts: list[str] = []
    remainder = number
    for size, word in groups:
        quotient, remainder = divmod(remainder, int(size))
        if quotient:
            parts.append(f'{quotient}{joiner}{word}')
    if remainder or not parts:
        parts.append(str(remainder))
    return ' '.join(parts)


def _render_money(text: str, language: str, money: dict) -> str:
    """Speak an amount in the guest's language, using the profile's unit words."""
    groups = (money.get('groups') or {}).get(language)
    currencies = (money.get('currencies') or {}).get(language) or {}
    if not groups or not currencies:
        return text
    point = (money.get('decimal_point') or {}).get(language, '.')
    joiner = str((voice_policy().get('word_separator') or {}).get(language, ' '))
    codes = '|'.join(re.escape(code) for code in sorted(currencies, key=len, reverse=True))

    def render(match: re.Match) -> str:
        number, cents = _parse_amount(match.group(1))
        spoken = _spoken_amount(number, groups, joiner)
        if cents:
            spoken += f' {point} ' + ' '.join(cents)
        return f'{spoken} {currencies[match.group(2).upper()]}'

    return re.sub(rf'(?<!\w)(\d[\d,.]*\d|\d)\s*({codes})(?!\w)', render, text, flags=re.I)


# Answers are rendered as Markdown for the screen. TTS engines read the markup
# literally ("asterisk asterisk schedule"), so speech drops it.
_MARKDOWN_LINE_PREFIX = re.compile(r'^\s*(?:[-*+•]\s+|#{1,6}\s+|>\s+|\d+[.)]\s+)', re.M)
_MARKDOWN_EMPHASIS = re.compile(r'(\*\*|__|\*|`)')
_CLOCK_RANGE = re.compile(
    r'(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)'
    r'(?:\s*(?:[–—-]|to|~)\s*([01]?\d|2[0-3]):([0-5]\d))?(?![\d:])')


def _strip_markdown(text: str) -> str:
    text = _MARKDOWN_LINE_PREFIX.sub('', text)
    text = _MARKDOWN_EMPHASIS.sub('', text)
    # One spoken pause per line instead of reading list structure.
    return re.sub(r'\s*\n+\s*', '. ', text).strip()


def _spoken_clock(hour: int, minute: int, language: str) -> str:
    """Speak a clock time naturally; the value itself is never changed."""
    spec = (voice_policy().get('time_rendering') or {}).get(language, {})
    twelve_hour = bool(spec.get('clock_12_hour', False))
    display_hour = hour % 12 or 12 if twelve_hour else hour
    hour_suffix = str(spec.get('hour_suffix', ''))
    minute_separator = str(spec.get('minute_separator', ':'))
    minute_suffix = str(spec.get('minute_suffix', ''))
    period_separator = str(spec.get('period_separator', ' '))
    periods = spec.get('periods') or {}
    period = ''
    if twelve_hour:
        period = period_separator + str(periods.get('am' if hour < 12 else 'pm', ''))
    if minute == 0:
        return f'{display_hour}{hour_suffix}{period}'
    return f'{display_hour}{hour_suffix}{minute_separator}{minute:02d}{minute_suffix}{period}'


def _spoken_time(match: re.Match, language: str) -> str:
    start = _spoken_clock(int(match.group(1)), int(match.group(2)), language)
    if match.group(3) is None:
        return start
    end = _spoken_clock(int(match.group(3)), int(match.group(4)), language)
    spec = (voice_policy().get('time_rendering') or {}).get(language, {})
    prefix = str(spec.get('range_prefix', ' - '))
    suffix = str(spec.get('range_suffix', ''))
    return f'{start}{prefix}{end}{suffix}'


def _format(audio: bytes) -> str | None:
    if len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return "wav"
    if audio.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if audio.startswith(b"OggS"):
        return "ogg"
    if audio.startswith(b"ID3") or (len(audio) >= 4 and audio[0] == 0xff and (audio[1] & 0xe0) == 0xe0):
        return "mp3"
    if len(audio) >= 12 and audio[4:8] == b"ftyp":
        return "m4a"
    return None


def _wav_properties(audio: bytes, *, max_seconds: float) -> tuple[int, int]:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            channels, width, rate, frames = (wav.getnchannels(), wav.getsampwidth(),
                                             wav.getframerate(), wav.getnframes())
            if (wav.getcomptype() != "NONE" or channels not in (1, 2) or
                    width not in (1, 2, 3, 4) or not 8_000 <= rate <= 96_000 or
                    frames <= 0 or frames / rate > max_seconds):
                raise ValueError("Invalid WAV properties or duration")
            # wave.open accepts truncated frame payloads. Read and verify the frame count.
            if len(wav.readframes(frames)) != frames * width * channels:
                raise ValueError("Truncated WAV payload")
            return rate, frames
    except (wave.Error, EOFError, OSError, OverflowError, ZeroDivisionError) as exc:
        raise ValueError("Invalid WAV payload") from exc


def validate_audio(audio: bytes, mime: str, *, max_bytes: int,
                   max_seconds: float = 25.0) -> str:
    """Return a decoder extension, rejecting mismatched/invalid headers before STT.

    Compressed-container duration must additionally be enforced by the decoder; this
    function only verifies its signature. A MIME parameter (codecs=...) is allowed.
    """
    declared = MIME_FORMATS.get((mime or "").split(";", 1)[0].strip().lower())
    if declared is None:
        raise ValueError("Unsupported audio content type")
    if not audio or len(audio) > max_bytes:
        raise ValueError("Audio size outside limit")
    actual = _format(audio)
    if actual != declared:
        raise ValueError("Audio content does not match declared format")
    if actual == "wav":
        _wav_properties(audio, max_seconds=max_seconds)
    elif len(audio) < 16:
        raise ValueError("Audio container is incomplete")
    else:
        _probe_compressed(audio, max_seconds=max_seconds)
    return actual


def _probe_compressed(audio: bytes, *, max_seconds: float) -> None:
    """Demux once with PyAV (installed with faster-whisper); reject unbounded media.

    Packet timestamps, not a user-controlled container duration field, determine
    the limit. No transcript or decoded samples are persisted.
    """
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("Local audio decoder unavailable") from exc
    try:
        with av.open(io.BytesIO(audio), mode="r") as container:
            streams = [item for item in container.streams if item.type == "audio"]
            if len(streams) != 1 or any(item.type == "video" for item in container.streams):
                raise ValueError("Expected one audio-only stream")
            stream = streams[0]
            if stream.duration is not None and stream.time_base is not None:
                if float(stream.duration * stream.time_base) > max_seconds:
                    raise ValueError("Audio duration outside limit")
            seen = 0
            last_end = None
            for packet in container.demux(stream):
                seen += 1
                if seen > 100_000:
                    raise ValueError("Audio contains too many packets")
                if packet.pts is not None and stream.time_base is not None:
                    position = float(packet.pts * stream.time_base)
                    duration = float(packet.duration * stream.time_base) if packet.duration else 0.0
                    last_end = max(last_end or 0.0, position + duration)
                    if last_end > max_seconds:
                        raise ValueError("Audio duration outside limit")
            if seen <= 0 or last_end is None:
                raise ValueError("Audio duration cannot be verified")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Invalid audio container") from exc


_PIPER_LOCK = threading.Lock()


def _inprocess_piper_available() -> bool:
    try:
        from importlib.util import find_spec
        return find_spec("piper") is not None
    except (ValueError, ImportError):
        return False


@lru_cache(maxsize=8)
def _piper_voice(model: str, mtime_ns: int):
    """One loaded voice per file revision (load ~1 s; synthesis ~0.1-0.2 s)."""
    from piper import PiperVoice
    return PiperVoice.load(model)


def _inprocess_synthesis(model: Path, text: str, cancelled=lambda: False,
                          synthesis=None) -> bytes:
    """Synthesize in-process: no per-chunk process spawn or model reload, and
    no dependence on the child's stdin codec (cp1252 on Windows mangles vi/zh/ko)."""
    voice = _piper_voice(str(model), model.stat().st_mtime_ns)
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
    _wav_properties(data, max_seconds=MAX_TTS_SECONDS)
    return data


def _piper_child_env() -> dict:
    """The Piper CLI is a Python program: force UTF-8 stdin on Windows."""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


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
    directory = Path(cfg.piper_models_dir) if cfg.piper_models_dir else None
    binary = bool(shutil.which(cfg.piper_executable)) or _inprocess_piper_available()
    languages = [lang for lang in LANGUAGE_WHISPER if binary and
                 _tts_model_paths(cfg, lang) is not None]
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
    if cfg.piper_models_dir and _inprocess_piper_available():
        for language in LANGUAGE_WHISPER:
            paths = _tts_model_paths(cfg, language)
            if paths is not None:
                model, _metadata = paths
                _piper_voice(str(model), model.stat().st_mtime_ns)
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
    extension = _format(audio)
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
            if not _STT_DECODE_SLOT.acquire(blocking=False):
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


def _tts_model_paths(cfg: Settings, language: str) -> tuple[Path, Path] | None:
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


def _tts_manifest_path(cfg: Settings, language: str) -> str:
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
    _manifest, values = _read_tts_manifest(_tts_manifest_path(cfg, language))
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
    paths = _tts_model_paths(cfg, language)
    if paths is None:
        raise RuntimeError("Licensed voice model or metadata for language not installed")
    model, _metadata = paths
    synthesis, synthesis_values = _tts_synthesis_config(cfg, language)
    if _inprocess_piper_available():
        try:
            return _inprocess_synthesis(model, speech_rendering(text, language),
                                        synthesis=synthesis)
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
            _wav_properties(data, max_seconds=MAX_TTS_SECONDS)
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
    paths = _tts_model_paths(cfg, language)
    if paths is None:
        raise RuntimeError('Licensed voice model or metadata for language not installed')
    model, _metadata = paths
    if cancelled():
        raise RuntimeError('Speech turn cancelled')
    synthesis, synthesis_values = _tts_synthesis_config(cfg, language)
    rendered = speech_rendering(text, language)
    if _inprocess_piper_available():
        try:
            return _inprocess_synthesis(model, rendered, cancelled, synthesis=synthesis)
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
            _wav_properties(data, max_seconds=MAX_TTS_SECONDS)
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
