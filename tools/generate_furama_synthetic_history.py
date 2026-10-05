"""Compatibility entry point for the checked-in synthetic history corpus.

The old generator targeted a removed flat layout and could overwrite the
canonical operations history with an older schema.  The current corpus is
validated through the canonical dataset contract; one-time normalization is
available in ``tools/normalize_synthetic_history.py``.
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
