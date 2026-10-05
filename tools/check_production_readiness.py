#!/usr/bin/env python3
"""Report production provisioning gaps without creating/migrating SQLite."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.core.runtime_profile import load_runtime_profile
from concierge_kiosk.runtime.production_readiness import production_provisioning_gaps


def main() -> int:
    profile_path = ROOT / "config" / "runtime-profiles" / "production.json"
    digest = (profile_path.with_suffix(".sha256").read_text(encoding="ascii").strip())
    profile = load_runtime_profile(str(profile_path), digest)
    gaps = production_provisioning_gaps(profile)
    print(json.dumps({"profile": profile.profile_id, "ready": not gaps, "gaps": gaps}, ensure_ascii=False, indent=2))
    return 0 if not gaps else 2


if __name__ == "__main__":
    raise SystemExit(main())
