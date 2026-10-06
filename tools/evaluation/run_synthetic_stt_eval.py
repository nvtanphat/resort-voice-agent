"""Measure local STT on the generated Piper set without faking human consent.

This is a transport/model smoke baseline only.  It intentionally cannot consume
the consented recorded-audio evaluator's manifest, and its result never counts
as the production WER/CER gate.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.voice.runtime.adapters import transcribe
from tools.evaluation.voice import distance, normalize, percentile


def run(manifest: Path, *, limit_per_language: int) -> dict:
    if limit_per_language < 1:
        raise ValueError("limit_per_language must be positive")
    root = manifest.parent
    groups: dict[str, list[dict]] = defaultdict(list)
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        lang = row.get("lang")
        if row.get("synthetic") is not True or lang not in {"vi", "en", "zh", "ko"}:
            raise ValueError("Synthetic manifest contains an invalid row")
        if len(groups[lang]) < limit_per_language:
            groups[lang].append(row)
    cfg = load_settings()
    output = {"type": "synthetic_local_stt_smoke", "synthetic": True,
              "release_gate": False, "note": "Does not replace consented human recordings.",
              "languages": {}}
    for lang, rows in sorted(groups.items()):
        cer_errors = cer_units = wer_errors = wer_units = 0
        timings: list[float] = []
        for row in rows:
            audio = (root / row["audio"]).read_bytes()
            start = time.perf_counter()
            recognized = transcribe(cfg, audio, lang)
            timings.append((time.perf_counter() - start) * 1000)
            ref = normalize(str(row["transcript"]))
            hyp = normalize(recognized)
            cer_errors += distance(list(ref.replace(" ", "")), list(hyp.replace(" ", "")))
            cer_units += len(ref.replace(" ", ""))
            if lang != "zh":
                wer_errors += distance(ref.split(), hyp.split())
                wer_units += len(ref.split())
        output["languages"][lang] = {
            "sample_count": len(rows),
            "cer": round(cer_errors / max(1, cer_units), 6),
            "wer": round(wer_errors / wer_units, 6) if wer_units else None,
            "latency_p50_ms": round(statistics.median(timings), 2),
            "latency_p95_ms": round(percentile(timings, 0.95), 2),
        }
    if not output["languages"]:
        raise ValueError("Synthetic manifest is empty")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--limit-per-language", type=int, default=5)
    args = parser.parse_args()
    result = run(args.manifest, limit_per_language=args.limit_per_language)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
