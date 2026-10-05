from __future__ import annotations

import hashlib
import json
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.voice.runtime import adapters
from concierge_kiosk.voice.session.turns import VoiceTurns


def _wav() -> bytes:
    from io import BytesIO

    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    return output.getvalue()


def test_stt_prompt_uses_profiled_sources_and_budget(monkeypatch, tmp_path: Path):
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps({"places": [{"labels": {"vi": "Hồ bơi"}}]}), encoding="utf-8")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "service-catalog.json").write_text(
        json.dumps([{"names_by_locale": {"vi": "Khăn tắm sạch"}}]), encoding="utf-8")
    (dataset / "aliases.json").write_text(
        json.dumps({"aliases_by_entity": {"x": {"vi": ["Furama"]}}}), encoding="utf-8")
    monkeypatch.setattr(adapters, "voice_policy", lambda: {
        "stt_hotwords": {"max_terms": 2, "sources": ["map_labels", "service_names", "aliases"],
                          "extra": {"vi": ["khách sạn"]}},
        "stt_prompt_prefix": {"vi": "guest: "},
    })
    adapters._stt_prompt.cache_clear()
    digest = hashlib.sha256(map_path.read_bytes()).hexdigest()
    prompt = adapters._stt_prompt(str(map_path), digest, "vi", str(dataset))
    assert prompt == "guest: Hồ bơi, Khăn tắm sạch"


def test_stt_manifest_controls_decode_and_reject_reason(monkeypatch, tmp_path: Path):
    model_dir = tmp_path / "whisper"
    model_dir.mkdir()
    manifest = tmp_path / "whisper.manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "model": "test", "revision": "test",
        "format": "concierge-whisper",
        "decode": {"beam_size": 2, "vad_filter": False, "condition_on_previous_text": True,
                   "no_speech_threshold": 0.7, "log_prob_threshold": -1.4,
                   "compression_ratio_threshold": 3.0, "temperature": [0.0]},
        "acceptance": {"no_speech_threshold": 0.7, "log_prob_threshold": -1.4,
                       "compression_ratio_threshold": 3.0, "min_confidence": 0.8},
        "calibration": {"status": "uncalibrated", "dataset": ""},
    }), encoding="utf-8")
    observed = {}

    class Model:
        def transcribe(self, path, **kwargs):
            observed.update(kwargs)
            return [SimpleNamespace(text=" hello ", no_speech_prob=0.01, avg_logprob=-1.0)], SimpleNamespace(
                language="en", language_probability=0.99)

    monkeypatch.setattr(adapters, "_whisper", lambda path: Model())
    cfg = Settings(whisper_model_path=str(model_dir), voice_stt_models={
        "default": {"model_path": str(model_dir), "manifest_path": str(manifest)},
    })
    result = adapters.transcribe_detected(cfg, _wav(), "en")
    assert observed["beam_size"] == 2 and observed["vad_filter"] is False
    assert result.text == "hello" and result.reject_reason == "low_confidence"


def test_lazy_stt_generator_is_subject_to_internal_decode_deadline(monkeypatch, tmp_path: Path):
    model_dir = tmp_path / "whisper"
    model_dir.mkdir()
    manifest = tmp_path / "whisper.manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "model": "test", "revision": "test",
        "format": "concierge-whisper",
        "decode": {"beam_size": 1, "vad_filter": True,
                   "condition_on_previous_text": False, "temperature": [0.0]},
        "acceptance": {"no_speech_threshold": 0.7, "log_prob_threshold": -1.4,
                       "compression_ratio_threshold": 3.0, "min_confidence": 0.0},
        "calibration": {"status": "uncalibrated", "dataset": ""},
    }), encoding="utf-8")

    class Model:
        def transcribe(self, _path, **_kwargs):
            def lazy_segments():
                time.sleep(0.11)
                yield SimpleNamespace(text="late", no_speech_prob=0.01, avg_logprob=-0.1)
            return lazy_segments(), SimpleNamespace(language="en", language_probability=0.99)

    monkeypatch.setattr(adapters, "_whisper", lambda path: Model())
    cfg = Settings(
        whisper_model_path=str(model_dir), stt_timeout_seconds=0.05,
        voice_stt_models={"default": {"model_path": str(model_dir), "manifest_path": str(manifest)}},
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="speech decoding failed"):
        adapters.transcribe_detected(cfg, _wav(), "en")
    assert time.monotonic() - started < 0.15


def test_quantity_rendering_and_speech_plan_are_config_driven():
    assert adapters.speech_rendering("Fresh towels x2", "vi").endswith("2 cái")
    turns = VoiceTurns(ttl_seconds=60, speech_plan={
        "max_chars": 80, "first_chunk_max_chars": 20, "clause_split_min_chars": 30,
    }, sentence_endings={"en": (".",)}, clause_delimiters={"en": (",",)})
    turn = turns.begin("guest")
    assert turns.finish("guest", turn)
    assert turns.authorize_speech("guest", turn, "First short. Second sentence is deliberately longer, with a clause.", "en")
    plan = turns.speech_plan("guest", turn)
    assert plan and plan["chunks"]
    assert len(plan["chunks"]) >= 2
