"""Pinned, operator-owned property presentation and voice policy.

The profile controls branding and bounded UX tuning only. It cannot widen
security, consent or resource limits enforced by application code.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from concierge_kiosk.domain.service_registry import LANGUAGES, REQUEST_KINDS


@dataclass(frozen=True)
class SessionProfile:
    idle_timeout_seconds: int
    warning_seconds: int


@dataclass(frozen=True)
class VadProfile:
    engine: str = "energy"
    noise_floor_initial: float = 0.002
    positive_threshold: float = 0.006
    negative_threshold: float = 0.004
    redemption_ms: int = 96
    pre_speech_pad_ms: int = 420
    min_speech_ms: int = 320
    playback_positive_threshold: float = 0.065
    continuation_extra_ms: int = 620


@dataclass(frozen=True)
class VoiceProfile:
    protocol: int = 2
    sample_rate: int = 16000
    max_frame_bytes: int = 131072
    max_windowed_audio_bytes: int = 6_000_000
    queue_bytes: int = 1_000_000
    credit_bytes: int = 262144
    vad_start_ms: int = 180
    vad_end_silence_ms: int = 650
    barge_preview_ms: int = 160
    barge_confirm_ms: int = 480
    false_interruption_recovery_ms: int = 500
    backpressure_timeout_ms: int = 3000
    vad: VadProfile = VadProfile()


@dataclass(frozen=True)
class EmergencyProfile:
    escalation_after_seconds: int = 60
    default_kiosk_location: str = ''


@dataclass(frozen=True)
class PropertyProfile:
    property_id: str
    property_name: str
    property_timezone: str
    default_language: str
    enabled_languages: tuple[str, ...]
    session: SessionProfile
    voice: VoiceProfile
    service_catalog: tuple[dict[str, Any], ...]
    low_risk_requires_verified_room: bool = False
    hitl_mode: str = 'legacy_policy'
    emergency: EmergencyProfile = EmergencyProfile()

    def public_config(self) -> dict[str, Any]:
        return {
            'property_id': self.property_id,
            'property_name': self.property_name,
            'property_timezone': self.property_timezone,
            'default_language': self.default_language,
            'enabled_languages': list(self.enabled_languages),
            'session_policy': {
                'idle_timeout_seconds': self.session.idle_timeout_seconds,
                'warning_seconds': self.session.warning_seconds,
            },
            'low_risk_requires_verified_room': self.low_risk_requires_verified_room,
            'hitl_mode': self.hitl_mode,
            'emergency_policy': {
                'escalation_after_seconds': self.emergency.escalation_after_seconds,
                'default_kiosk_location': self.emergency.default_kiosk_location,
            },
            'voice_policy': {
                'protocol': self.voice.protocol,
                'sample_rate': self.voice.sample_rate,
                'max_frame_bytes': self.voice.max_frame_bytes,
                'max_windowed_audio_bytes': self.voice.max_windowed_audio_bytes,
                'queue_bytes': self.voice.queue_bytes,
                'credit_bytes': self.voice.credit_bytes,
                'vad_start_ms': self.voice.vad_start_ms,
                'vad_end_silence_ms': self.voice.vad_end_silence_ms,
                'barge_preview_ms': self.voice.barge_preview_ms,
                'barge_confirm_ms': self.voice.barge_confirm_ms,
                'false_interruption_recovery_ms': self.voice.false_interruption_recovery_ms,
                'backpressure_timeout_ms': self.voice.backpressure_timeout_ms,
                'vad': {
                    'engine': self.voice.vad.engine,
                    'noise_floor_initial': self.voice.vad.noise_floor_initial,
                    'positive_threshold': self.voice.vad.positive_threshold,
                    'negative_threshold': self.voice.vad.negative_threshold,
                    'redemption_ms': self.voice.vad.redemption_ms,
                    'pre_speech_pad_ms': self.voice.vad.pre_speech_pad_ms,
                    'min_speech_ms': self.voice.vad.min_speech_ms,
                    'playback_positive_threshold': self.voice.vad.playback_positive_threshold,
                    'continuation_extra_ms': self.voice.vad.continuation_extra_ms,
                },
            },
        }


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'Invalid property profile {name}')
    return value


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f'Invalid property profile {name}')
    return float(value)


def _catalog(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError('Invalid property profile service_catalog')
    seen: set[str] = set()
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'id', 'request_kind', 'title', 'question'}:
            raise ValueError('Invalid property profile service item')
        ident = item['id']
        kind = item['request_kind']
        if not isinstance(ident, str) or not ident or len(ident) > 48 or ident in seen:
            raise ValueError('Invalid or duplicate property profile service id')
        if kind is not None and kind not in REQUEST_KINDS:
            raise ValueError('Invalid property profile request kind')
        seen.add(ident)
        clean = {'id': ident, 'request_kind': kind}
        for field in ('title', 'question'):
            translations = item[field]
            if not isinstance(translations, dict) or set(translations) != LANGUAGES:
                raise ValueError(f'Property profile {field} must cover all languages')
            if any(not isinstance(text, str) or not text.strip() or len(text) > 240
                   for text in translations.values()):
                raise ValueError(f'Invalid property profile {field}')
            clean[field] = {language: translations[language].strip() for language in sorted(LANGUAGES)}
        result.append(clean)
    return tuple(result)


def parse_property_profile(payload: dict[str, Any], *, max_session_ttl: int) -> PropertyProfile:
    if not isinstance(payload, dict):
        raise ValueError('Property profile must be an object')
    required = {'property_id', 'property_name', 'property_timezone', 'default_language',
                'enabled_languages', 'session_policy', 'voice_policy', 'service_catalog'}
    optional = {'low_risk_requires_verified_room', 'hitl_mode', 'emergency_policy'}
    if not required.issubset(payload) or set(payload) - required - optional:
        raise ValueError('Unexpected property profile fields')
    property_id = payload['property_id']
    if (not isinstance(property_id, str) or not property_id.strip() or len(property_id) > 64
            or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for ch in property_id)):
        raise ValueError('Invalid property profile property_id')
    name = payload['property_name']
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError('Invalid property profile property_name')
    timezone = payload['property_timezone']
    if not isinstance(timezone, str) or not timezone.strip() or len(timezone) > 80:
        raise ValueError('Invalid property profile property_timezone')
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(timezone)
    except Exception as exc:
        raise ValueError('Invalid property profile property_timezone') from exc
    enabled = payload['enabled_languages']
    if (not isinstance(enabled, list) or not enabled or len(enabled) > len(LANGUAGES)
            or len(set(enabled)) != len(enabled) or any(item not in LANGUAGES for item in enabled)):
        raise ValueError('Invalid property profile enabled_languages')
    default = payload['default_language']
    if default not in enabled:
        raise ValueError('Default language must be enabled')
    hitl_mode = payload.get('hitl_mode', 'legacy_policy')
    if hitl_mode not in {'legacy_policy', 'guest_confirm_all'}:
        raise ValueError('Invalid property profile hitl_mode')
    session = payload['session_policy']
    if not isinstance(session, dict) or set(session) != {'idle_timeout_seconds', 'warning_seconds'}:
        raise ValueError('Invalid property profile session_policy')
    idle = _integer(session['idle_timeout_seconds'], 'idle_timeout_seconds', 30, max_session_ttl)
    warning = _integer(session['warning_seconds'], 'warning_seconds', 5, idle - 1)
    voice = payload['voice_policy']
    required_voice = {
        'protocol', 'sample_rate', 'max_frame_bytes', 'queue_bytes', 'credit_bytes',
        'vad_start_ms', 'vad_end_silence_ms', 'barge_preview_ms', 'barge_confirm_ms',
        'false_interruption_recovery_ms', 'backpressure_timeout_ms',
    }
    allowed_voice = required_voice | {'max_windowed_audio_bytes', 'vad'}
    if (not isinstance(voice, dict) or not required_voice.issubset(voice)
            or not set(voice).issubset(allowed_voice)):
        raise ValueError('Invalid property profile voice_policy')
    if voice['protocol'] != 2 or voice['sample_rate'] != 16000:
        raise ValueError('Property profile cannot change protocol or PCM sample rate')
    vad = voice.get('vad') or {}
    if not isinstance(vad, dict) or set(vad) - {
            'engine', 'noise_floor_initial', 'positive_threshold', 'negative_threshold', 'redemption_ms',
            'pre_speech_pad_ms', 'min_speech_ms', 'playback_positive_threshold',
            'continuation_extra_ms'}:
        raise ValueError('Invalid property profile voice_policy.vad')
    vad_profile = VadProfile(
        engine=str(vad.get('engine', 'energy')),
        noise_floor_initial=_number(vad.get('noise_floor_initial', 0.002), 'vad.noise_floor_initial', 0.0001, 1.0),
        positive_threshold=_number(vad.get('positive_threshold', 0.006), 'vad.positive_threshold', 0.0001, 1.0),
        negative_threshold=_number(vad.get('negative_threshold', 0.004), 'vad.negative_threshold', 0.0001, 1.0),
        redemption_ms=_integer(vad.get('redemption_ms', 96), 'vad.redemption_ms', 20, 2000),
        pre_speech_pad_ms=_integer(vad.get('pre_speech_pad_ms', 420), 'vad.pre_speech_pad_ms', 0, 2000),
        min_speech_ms=_integer(vad.get('min_speech_ms', 320), 'vad.min_speech_ms', 40, 2000),
        playback_positive_threshold=_number(
            vad.get('playback_positive_threshold', 0.065),
            'vad.playback_positive_threshold', 0.0001, 1.0),
        continuation_extra_ms=_integer(
            vad.get('continuation_extra_ms', 620), 'vad.continuation_extra_ms', 0, 3000),
    )
    if vad_profile.engine not in {'energy', 'silero_v5'}:
        raise ValueError('Invalid property profile VAD engine')
    if vad_profile.negative_threshold > vad_profile.positive_threshold:
        raise ValueError('VAD negative threshold cannot exceed positive threshold')
    voice_profile = VoiceProfile(
        protocol=2,
        sample_rate=16000,
        max_frame_bytes=_integer(voice['max_frame_bytes'], 'max_frame_bytes', 4096, 131072),
        max_windowed_audio_bytes=_integer(voice.get('max_windowed_audio_bytes', 6_000_000), 'max_windowed_audio_bytes', 65536, 20_000_000),
        queue_bytes=_integer(voice['queue_bytes'], 'queue_bytes', 65536, 1_000_000),
        credit_bytes=_integer(voice['credit_bytes'], 'credit_bytes', 16384, 262144),
        vad_start_ms=_integer(voice['vad_start_ms'], 'vad_start_ms', 100, 600),
        vad_end_silence_ms=_integer(voice['vad_end_silence_ms'], 'vad_end_silence_ms', 400, 1800),
        barge_preview_ms=_integer(voice['barge_preview_ms'], 'barge_preview_ms', 80, 400),
        barge_confirm_ms=_integer(voice['barge_confirm_ms'], 'barge_confirm_ms', 250, 1200),
        false_interruption_recovery_ms=_integer(voice['false_interruption_recovery_ms'], 'false_interruption_recovery_ms', 100, 2000),
        backpressure_timeout_ms=_integer(voice['backpressure_timeout_ms'], 'backpressure_timeout_ms', 500, 10000),
        vad=vad_profile,
    )
    if voice_profile.barge_preview_ms >= voice_profile.barge_confirm_ms:
        raise ValueError('Barge preview must precede confirmation')
    if voice_profile.credit_bytes > voice_profile.queue_bytes:
        raise ValueError('Voice credit cannot exceed queue bound')
    emergency_payload = payload.get('emergency_policy') or {
        'escalation_after_seconds': 60,
        'default_kiosk_location': property_id,
    }
    if not isinstance(emergency_payload, dict) or set(emergency_payload) != {
            'escalation_after_seconds', 'default_kiosk_location'}:
        raise ValueError('Invalid property profile emergency_policy')
    emergency_location = emergency_payload['default_kiosk_location']
    if (not isinstance(emergency_location, str) or not emergency_location.strip()
            or len(emergency_location) > 64
            or not all(ch.isascii() and (ch.isalnum() or ch in '_-') for ch in emergency_location)):
        raise ValueError('Invalid property profile default_kiosk_location')
    return PropertyProfile(
        property_id=property_id.strip(), property_name=name.strip(), property_timezone=timezone.strip(),
        default_language=default,
        enabled_languages=tuple(enabled),
        session=SessionProfile(idle, warning), voice=voice_profile,
        service_catalog=_catalog(payload['service_catalog']),
        low_risk_requires_verified_room=bool(payload.get('low_risk_requires_verified_room', False)),
        hitl_mode=hitl_mode,
        emergency=EmergencyProfile(
            escalation_after_seconds=_integer(
                emergency_payload['escalation_after_seconds'], 'emergency escalation_after_seconds', 10, 86400),
            default_kiosk_location=emergency_location.strip(),
        ),
    )


def load_property_profile(path: str, expected_sha256: str, *, max_session_ttl: int,
                          signature_path: str = '', public_key_path: str = '') -> PropertyProfile:
    source = Path(path)
    if not path or not expected_sha256 or source.is_symlink() or not source.is_file():
        raise ValueError('Pinned property profile unavailable')
    if bool(signature_path) != bool(public_key_path):
        raise ValueError('Property profile signature and public key must be configured together')
    raw = source.read_bytes()
    if len(raw) > 128_000:
        raise ValueError('Property profile too large')
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError('Property profile SHA-256 mismatch')
    if signature_path:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        signature_file, key_file = Path(signature_path), Path(public_key_path)
        if (signature_file.is_symlink() or key_file.is_symlink() or
                not signature_file.is_file() or not key_file.is_file() or
                signature_file.stat().st_size > 4096 or key_file.stat().st_size > 8192):
            raise ValueError('Signed property profile trust material unavailable')
        try:
            key = serialization.load_pem_public_key(key_file.read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError('Only Ed25519 public keys are accepted')
            signature = base64.b64decode(signature_file.read_bytes(), validate=True)
            key.verify(signature, raw)
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise ValueError('Invalid property profile signature or public key') from exc
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid UTF-8 property profile JSON') from exc
    return parse_property_profile(payload, max_session_ttl=max_session_ttl)



def unconfigured_property_profile(*, property_id: str, property_name: str, property_timezone: str,
                                  max_session_ttl: int) -> PropertyProfile:
    """Non-production shell with no hotel facts and no service catalog.

    Real property behavior is supplied only by a pinned property profile. This
    shell exists so development can boot far enough to expose health/config
    state without inventing hotel services or operational claims.
    """
    idle = max(30, max_session_ttl)
    warning = min(60, max(10, idle // 6))
    voice = VoiceProfile()
    return parse_property_profile({
        'property_id': property_id.strip() or 'UNCONFIGURED',
        'property_name': property_name.strip() or 'Property not configured',
        'property_timezone': property_timezone.strip() or 'UTC',
        'default_language': 'vi',
        'enabled_languages': sorted(LANGUAGES),
        'session_policy': {'idle_timeout_seconds': idle, 'warning_seconds': warning},
        'voice_policy': {
            'protocol': voice.protocol,
            'sample_rate': voice.sample_rate,
            'max_frame_bytes': voice.max_frame_bytes,
            'max_windowed_audio_bytes': voice.max_windowed_audio_bytes,
            'queue_bytes': voice.queue_bytes,
            'credit_bytes': voice.credit_bytes,
            'vad_start_ms': voice.vad_start_ms,
            'vad_end_silence_ms': voice.vad_end_silence_ms,
            'barge_preview_ms': voice.barge_preview_ms,
            'barge_confirm_ms': voice.barge_confirm_ms,
            'false_interruption_recovery_ms': voice.false_interruption_recovery_ms,
            'backpressure_timeout_ms': voice.backpressure_timeout_ms,
        },
        'service_catalog': [],
        'low_risk_requires_verified_room': False,
        'emergency_policy': {
            'escalation_after_seconds': 60,
            'default_kiosk_location': 'unconfigured_kiosk',
        },
    }, max_session_ttl=idle)
