"""Compatibility entry point for the canonical production evaluation corpus.

The production corpus is checked in under ``datasets/evaluation/end_to_end``.
The former builder wrote a removed property-specific tree and is deliberately
not allowed to recreate it; use the validator as the release-time build gate.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluation.validate_production_evaluation import validate


def main() -> None:
    print(json.dumps({"status": "validated", **validate()}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
