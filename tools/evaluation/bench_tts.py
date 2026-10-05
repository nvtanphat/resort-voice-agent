"""Measure local TTS latency and output validity without changing voice assets."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

# Make the checked-out application importable when the tool is invoked directly.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.voice.runtime.adapters import synthesize


def _percentile(values: list[float], percentile: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int((len(values) - 1) * percentile)))
    return round(values[index], 2)


def _samples(path: Path | None, language: str, texts: list[str]) -> list[tuple[str, str]]:
    if texts:
        return [(language, text) for text in texts]
    if path is None:
        raise SystemExit("Provide at least one --text or --dataset JSONL")
    result = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        row_language, text = row.get("language", language), row.get("text")
        if row_language not in {"vi", "en", "zh", "ko"} or not isinstance(text, str) or not text.strip():
            raise SystemExit(f"Invalid TTS row at {path}:{line_number}")
        result.append((row_language, text))
    return result


def bench(models_dir: Path, samples: list[tuple[str, str]]) -> dict:
    timings: dict[str, list[float]] = {}
    errors: list[dict[str, str]] = []
    sizes: dict[str, list[int]] = {}
    cfg = Settings(piper_models_dir=str(models_dir))
    for language, text in samples:
        started = time.perf_counter()
        try:
            audio = synthesize(cfg, text, language)
        except Exception as exc:  # benchmark records failures instead of hiding them
            errors.append({"language": language, "error": type(exc).__name__})
            continue
        elapsed = (time.perf_counter() - started) * 1000
        timings.setdefault(language, []).append(elapsed)
        sizes.setdefault(language, []).append(len(audio))
    return {
        "samples": len(samples),
        "successful": sum(len(values) for values in timings.values()),
        "errors": errors,
        "languages": {
            language: {
                "p50_ms": _percentile(values, 0.50),
                "p95_ms": _percentile(values, 0.95),
                "mean_ms": round(statistics.mean(values), 2),
                "mean_bytes": round(statistics.mean(sizes[language])),
            }
            for language, values in sorted(timings.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", required=True, type=Path)
    parser.add_argument("--language", default="en", choices=("vi", "en", "zh", "ko"))
    parser.add_argument("--text", action="append", default=[])
    parser.add_argument("--dataset", type=Path)
    args = parser.parse_args()
    report = bench(args.models_dir, _samples(args.dataset, args.language, args.text))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not report["errors"] else 2


if __name__ == "__main__":
    sys.exit(main())
