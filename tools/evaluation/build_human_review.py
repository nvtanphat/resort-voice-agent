"""Compatibility entry point for the canonical reviewed/gold corpus.

Review data is checked in under ``datasets/evaluation/gold``.  The old
generated human-review layer is intentionally not rebuilt because it used a
removed property-specific layout and could create stale artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluation.validate_human_review import validate


def main() -> None:
    print(json.dumps({"status": "validated", **validate()}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
