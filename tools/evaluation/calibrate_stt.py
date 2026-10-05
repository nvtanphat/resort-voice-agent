"""Calibrate Whisper acceptance thresholds from operator-supplied recordings.

The tool deliberately refuses to invent a benchmark set. Expected input is a
directory containing ``<language>/*.wav`` and ``transcripts.jsonl`` rows with
``audio`` and ``text`` fields. It writes only a model manifest when ``--write``
is explicitly supplied; audio and transcripts are never copied to the output.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from statistics import quantiles


def _norm(value: str) -> list[str]:
    return re.findall(r"\w+", value.casefold(), flags=re.UNICODE)


def _wer(reference: str, hypothesis: str) -> float:
    ref, hyp = _norm(reference), _norm(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, token in enumerate(ref, 1):
        next_row = [i]
        for j, candidate in enumerate(hyp, 1):
            next_row.append(min(
                next_row[-1] + 1,
                row[j] + 1,
                row[j - 1] + (token != candidate),
            ))
        row = next_row
    return row[-1] / max(1, len(ref))


def _rows(dataset: Path, language: str) -> list[tuple[Path, str]]:
    candidates = (
        dataset / language / "transcripts.jsonl",
        dataset / f"transcripts-{language}.jsonl",
        dataset / "transcripts.jsonl",
    )
    transcript_file = next((path for path in candidates if path.is_file()), None)
    if transcript_file is None:
        raise SystemExit(
            f"No transcript JSONL for {language}. Expected one of: "
            + ", ".join(str(path) for path in candidates))
    result: list[tuple[Path, str]] = []
    for line_number, line in enumerate(transcript_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON at {transcript_file}:{line_number}") from exc
        audio = row.get("audio") or row.get("file") or row.get("wav")
        text = row.get("text") or row.get("transcript") or row.get("reference")
        if not isinstance(audio, str) or not isinstance(text, str) or not text.strip():
            raise SystemExit(f"Each transcript row needs audio and text: {transcript_file}:{line_number}")
        audio_path = Path(audio)
        if not audio_path.is_absolute():
            audio_path = transcript_file.parent / audio_path
        if not audio_path.is_file() or audio_path.suffix.lower() != ".wav":
            raise SystemExit(f"Missing WAV referenced by {transcript_file}:{line_number}: {audio_path}")
        result.append((audio_path, text))
    if not result:
        raise SystemExit(f"Transcript file is empty: {transcript_file}")
    return result


def _manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read STT manifest: {path}") from exc
    if payload.get("format") != "concierge-whisper" or not isinstance(payload.get("decode"), dict):
        raise SystemExit(f"Unsupported STT manifest: {path}")
    return payload


def _decode(model, path: Path, language: str, decode: dict) -> tuple[str, float | None]:
    options = {key: value for key, value in decode.items()
               if key in {"beam_size", "vad_filter", "condition_on_previous_text",
                          "no_speech_threshold", "log_prob_threshold",
                          "compression_ratio_threshold", "temperature"}}
    segments, _info = model.transcribe(str(path), language=language, **options)
    text: list[str] = []
    scores: list[float] = []
    for segment in segments:
        part = str(getattr(segment, "text", "") or "").strip()
        if part:
            text.append(part)
        value = getattr(segment, "avg_logprob", None)
        if isinstance(value, (int, float)):
            scores.append(max(0.0, min(1.0, math.exp(float(value)))))
    return " ".join(text).strip(), sum(scores) / len(scores) if scores else None


def calibrate(*, model_path: Path, manifest_path: Path, dataset: Path,
              language: str, write: bool) -> dict:
    rows = _rows(dataset, language)
    manifest = _manifest(manifest_path)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit("faster-whisper is required to calibrate STT") from exc
    if not model_path.is_dir() or model_path.is_symlink():
        raise SystemExit(f"Local Whisper model directory is unavailable: {model_path}")

    model = WhisperModel(str(model_path), device="cpu", compute_type="int8", local_files_only=True)
    observations = []
    for audio, reference in rows:
        started = time.perf_counter()
        hypothesis, confidence = _decode(model, audio, language, manifest["decode"])
        observations.append({
            "reference": reference,
            "hypothesis": hypothesis,
            "confidence": confidence if confidence is not None else 0.0,
            "wer": _wer(reference, hypothesis),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        })

    values = sorted({0.0, 0.2, 0.3, 0.35, 0.4, 0.5,
                     *(round(item["confidence"], 3) for item in observations)})
    candidates = []
    for threshold in values:
        accepted = [item for item in observations if item["confidence"] >= threshold]
        rejected = len(observations) - len(accepted)
        gated_wer = sum(item["wer"] if item in accepted else 1.0 for item in observations) / len(observations)
        reject_rate = rejected / len(observations)
        candidates.append((gated_wer + 0.25 * reject_rate, threshold, gated_wer, reject_rate))
    _objective, threshold, gated_wer, reject_rate = min(candidates)
    latency = sorted(item["latency_ms"] for item in observations)
    report = {
        "language": language,
        "samples": len(observations),
        "acceptance": {"min_confidence": threshold},
        "metrics": {
            "gated_wer": round(gated_wer, 4),
            "reject_rate": round(reject_rate, 4),
            "p50_latency_ms": round(latency[len(latency) // 2], 2),
            "p95_latency_ms": round(latency[min(len(latency) - 1, math.ceil(len(latency) * 0.95) - 1)], 2),
        },
    }
    if write:
        manifest["acceptance"] = {
            **manifest.get("acceptance", {}),
            "min_confidence": threshold,
        }
        manifest["calibration"] = {
            "status": "calibrated",
            "dataset": str(dataset),
            "metrics": report["metrics"],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["manifest_written"] = str(manifest_path)
    else:
        report["manifest_written"] = None
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--language", required=True, choices=("vi", "en", "zh", "ko"))
    parser.add_argument("--write", action="store_true", help="write calibrated acceptance values to the manifest")
    args = parser.parse_args()
    print(json.dumps(calibrate(model_path=args.model, manifest_path=args.manifest,
                               dataset=args.dataset, language=args.language, write=args.write),
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
