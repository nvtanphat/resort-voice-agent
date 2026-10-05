"""Compatibility entry point for the canonical hospitality evaluation corpus.

The generated artifacts are already checked in under
``datasets/evaluation/end_to_end``.  Rebuilding the former directory would
silently reintroduce an obsolete layout, so this command validates the current
release instead.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluation.validate_hospitality import validate


def main() -> None:
    print(json.dumps({"status": "validated", **validate()}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
