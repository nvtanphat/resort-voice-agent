"""Create a reproducible, explicitly synthetic Piper voice-evaluation set.

The generated WAV files are not evidence of ASR quality or human consent.  They
are a deterministic transport/route smoke set.  Real recorded audio must use
the separate consented manifest consumed by ``tools.evaluation.voice``.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import sys
import wave
from io import BytesIO

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.core.settings import load_settings  # noqa: E402
from concierge_kiosk.voice.runtime.tts import synthesize  # noqa: E402

LANGUAGES = ("vi", "en", "zh", "ko")
ALLOWED_DISFLUENCY = frozenset({
    "none", "filler", "self_correction", "repetition", "hesitation", "noise",
    "out_of_scope",
})
SOURCE_FILES = {
    "vi": ("datasets/evaluation/voice_text/vi_asr_robustness.jsonl",),
    "other": (
        "datasets/evaluation/challenges/natural.jsonl",
        "datasets/evaluation/end_to_end/service_actions.jsonl",
        "datasets/evaluation/end_to_end/scenarios/production.jsonl",
    ),
}


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected an object at {path}:{line_number}")
        value["_source_file"] = path.as_posix()
        rows.append(value)
    return rows


def _language(row: dict) -> str:
    value = row.get("language") or row.get("lang")
    return str(value or "")


def _text(row: dict) -> str:
    for key in ("reference_transcript", "utterance", "transcript", "query", "text"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"No utterance text in synthetic row {row.get('case_id')}")


def _route(row: dict) -> str:
    return str(row.get("expected_route") or row.get("route") or "unknown")


def _expected_tools(row: dict) -> list[str]:
    route = _route(row)
    if route == "service":
        return ["service_action"]
    if route == "knowledge":
        return ["knowledge"]
    if route == "navigation":
        return ["navigation"]
    return []


def _disfluency(row: dict) -> list[str]:
    source = str(row.get("perturbation_type") or row.get("disfluency") or "none")
    mapping = {
        "disfluency": "filler", "self_correction": "self_correction",
        "repetition": "repetition", "hesitation": "hesitation",
        "noise": "noise", "out_of_scope": "out_of_scope",
    }
    value = mapping.get(source, "none")
    return [value] if value in ALLOWED_DISFLUENCY and value != "none" else []


def _resample_wav(raw: bytes, target_rate: int = 16_000) -> bytes:
    """Downmix and linearly resample a bounded PCM WAV using stdlib only."""
    with wave.open(BytesIO(raw), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if sample_width != 2 or rate <= 0 or channels <= 0:
        raise ValueError("Piper output must be 16-bit PCM WAV")
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    if channels > 1:
        mono = array("h")
        for index in range(0, len(samples), channels):
            group = samples[index:index + channels]
            mono.append(int(sum(group) / max(1, len(group))))
        samples = mono
    if rate != target_rate and samples:
        size = max(1, round(len(samples) * target_rate / rate))
        resampled = array("h")
        scale = (len(samples) - 1) / max(1, size - 1)
        for index in range(size):
            position = index * scale
            left = int(position)
            right = min(left + 1, len(samples) - 1)
            fraction = position - left
            value = round(samples[left] + (samples[right] - samples[left]) * fraction)
            resampled.append(max(-32768, min(32767, value)))
        samples = resampled
    output = BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(target_rate)
        if sys.byteorder != "little":
            samples.byteswap()
        target.writeframes(samples.tobytes())
        if sys.byteorder != "little":
            samples.byteswap()
    return output.getvalue()


def _candidate_rows() -> dict[str, list[dict]]:
    root = ROOT
    rows: dict[str, list[dict]] = {language: [] for language in LANGUAGES}
    for row in _read_jsonl(root / SOURCE_FILES["vi"][0]):
        language = _language(row)
        if language == "vi":
            rows[language].append(row)
    for relative in SOURCE_FILES["other"]:
        for row in _read_jsonl(root / relative):
            language = _language(row)
            if language in rows:
                rows[language].append(row)
    for language in rows:
        rows[language].sort(key=lambda item: (
            str(item.get("case_id", "")), str(item.get("_source_file", ""))))
    return rows


def build(*, output: Path, limit_per_language: int, piper_dir: Path) -> dict:
    if limit_per_language < 1:
        raise ValueError("limit_per_language must be positive")
    os.environ.setdefault("CONCIERGE_ENV", "test")
    os.environ.setdefault("CONCIERGE_PUBLIC_ORIGIN", "http://localhost:8000")
    os.environ["CONCIERGE_PIPER_MODELS_DIR"] = str(piper_dir)
    cfg = load_settings()
    rows = _candidate_rows()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.jsonl"
    generated = []
    with manifest_path.open("w", encoding="utf-8", newline="\n") as manifest:
        for language in LANGUAGES:
            for index, row in enumerate(rows[language][:limit_per_language], 1):
                text = _text(row)
                case_id = f"SYNTH-{language.upper()}-{index:04d}"
                wav = _resample_wav(synthesize(cfg, text, language))
                relative_audio = Path(language) / f"{case_id}.wav"
                audio_path = output / relative_audio
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(wav)
                service_code = row.get("service_code")
                expected_slots = {"service_code": str(service_code)} if service_code else {}
                record = {
                    "id": case_id,
                    "audio": relative_audio.as_posix(),
                    "lang": language,
                    "transcript": text,
                    "disfluency": _disfluency(row),
                    "expected_tools": _expected_tools(row),
                    "expected_slots": expected_slots,
                    "expected_db_change": "unknown",
                    "synthetic": True,
                    "generator": "piper-local",
                    "source_case_id": str(row.get("case_id") or case_id),
                    "source_file": str(row.get("_source_file", "")),
                    "route_oracle": _route(row),
                }
                manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
                generated.append(record)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "required": ["id", "audio", "lang", "transcript", "disfluency",
                      "expected_tools", "expected_slots", "expected_db_change", "synthetic"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "audio": {"type": "string", "minLength": 1},
            "lang": {"enum": list(LANGUAGES)},
            "transcript": {"type": "string", "minLength": 1},
            "disfluency": {"type": "array", "items": {"type": "string"}},
            "expected_tools": {"type": "array", "items": {"type": "string"}},
            "expected_slots": {"type": "object", "additionalProperties": {"type": "string"}},
            "expected_db_change": {"enum": ["request_created", "no_request", "unknown"]},
            "synthetic": {"const": True},
            "generator": {"type": "string"},
            "source_case_id": {"type": "string"},
            "source_file": {"type": "string"},
            "route_oracle": {"type": "string"},
        },
    }
    (output / "manifest.schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1, "synthetic": True, "generator": "piper-local",
        "sample_rate_hz": 16_000, "limit_per_language": limit_per_language,
        "languages": {language: sum(item["lang"] == language for item in generated)
                       for language in LANGUAGES},
        "cases": len(generated),
        "manifest": str(manifest_path),
        "audio_sha256": hashlib.sha256(b"".join(
            (output / item["audio"]).read_bytes() for item in generated)).hexdigest(),
        "note": "Synthetic Piper transport set; not an ASR or human-listening release gate.",
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/voice-set"))
    parser.add_argument("--limit-per-language", type=int, default=350)
    parser.add_argument("--piper-dir", type=Path, default=Path("models/voice/piper"))
    args = parser.parse_args()
    report = build(output=args.output, limit_per_language=args.limit_per_language,
                   piper_dir=args.piper_dir)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
