import json
from pathlib import Path

from jsonschema import Draft202012Validator

from tools.evaluation.run_voice_eval import _percentile, _tool_names, word_error_rate


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "datasets" / "schemas" / "voice" / "manifest.schema.json"


def _valid_case() -> dict:
    return {
        "audio_path": "vi/vi-service-001.wav",
        "language": "vi",
        "condition": "quiet",
        "reference": "mang hai khan len phong ba khong nam",
        "consent": True,
        "speaker_id": "SYNTH-01",
        "split": "test",
    }


def test_voice_eval_manifest_schema_accepts_case_and_rejects_bad_consent():
    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    assert list(validator.iter_errors(_valid_case())) == []

    invalid = _valid_case()
    invalid["consent"] = False
    assert list(validator.iter_errors(invalid))


def test_voice_eval_metrics_are_deterministic():
    assert word_error_rate("mot hai ba", "mot hai bon") == 1 / 3
    assert word_error_rate("", "bat ky") == 2.0
    assert _percentile([10.0, 20.0, 30.0, 40.0], 0.50) == 20.0
    assert _percentile([], 0.95) is None


def test_voice_eval_tool_extraction_normalizes_service_alias():
    payload = {
        "tool_route": "service",
        "agent_action": {"tool": "knowledge"},
        "requirements": [{"capability": "navigation"}],
    }
    assert _tool_names(payload) == {"service_action", "knowledge", "navigation"}
