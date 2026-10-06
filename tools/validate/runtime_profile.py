#!/usr/bin/env python3
"""Validate a checksum-pinned runtime profile and print its effective contract."""
from __future__ import annotations

import argparse
from pathlib import Path

from concierge_kiosk.core.runtime_profile import load_runtime_profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", help="Path to runtime profile JSON")
    parser.add_argument("--sha256", default="", help="Expected SHA-256; defaults to sibling .sha256")
    args = parser.parse_args()
    path = Path(args.profile)
    checksum = args.sha256.strip().lower()
    if not checksum:
        sidecar = path.with_suffix(".sha256")
        checksum = sidecar.read_text(encoding="ascii").strip().lower()
    profile = load_runtime_profile(str(path), checksum)
    embedding_path, _ = profile.embedding_assets()
    print(f"profile={profile.profile_id}")
    print(f"schema_version={profile.schema_version}")
    print(f"slm_primary={profile.models['slm']['primary_model'] or '<disabled>'}")
    print(f"slm_fallback={profile.models['slm']['fallback_model'] or '<none>'}")
    print(f"embedding={embedding_path or '<unavailable>'}")
    print(f"agent_max_steps={profile.budgets['agent']['max_steps']}")
    print(f"planner_enabled={str(profile.features['agent_planner']).lower()}")
    print(f"sha256={profile.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
