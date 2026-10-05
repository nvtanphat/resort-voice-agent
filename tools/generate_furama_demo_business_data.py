"""Compatibility entry point for the canonical synthetic business dataset.

The checked-in demo data lives under ``datasets/synthetic/operations``.  This
command validates that release instead of generating a second, stale flat tree.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_synthetic_operations import validate


def main() -> None:
    print(json.dumps({"status": "validated", **validate()}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
