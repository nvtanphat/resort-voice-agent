"""Build checksum-pinned runtime profiles from one base and small overlays."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "config" / "runtime-profiles" / "src"
OUTPUT_ROOT = ROOT / "config" / "runtime-profiles"
SCHEMA_PATH = ROOT / "config" / "runtime-profile.schema.json"
PROFILES = ("test", "development", "edge", "production")


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def build_profile(name: str) -> bytes:
    payload = _deep_merge(_read_json(SOURCE_ROOT / "base.json"), _read_json(SOURCE_ROOT / f"{name}.json"))
    payload["profile_id"] = name
    errors = sorted(Draft202012Validator(_read_json(SCHEMA_PATH)).iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ValueError(f"{name}: schema validation failed at {location}: {error.message}")
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when generated files differ")
    args = parser.parse_args()

    changed: list[str] = []
    for name in PROFILES:
        raw = build_profile(name)
        target = OUTPUT_ROOT / f"{name}.json"
        sidecar = OUTPUT_ROOT / f"{name}.sha256"
        if args.check:
            if target.read_bytes() != raw:
                changed.append(str(target))
            digest = hashlib.sha256(raw).hexdigest() + "\n"
            if sidecar.read_text(encoding="ascii") != digest:
                changed.append(str(sidecar))
        else:
            target.write_bytes(raw)
            sidecar.write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii")
    if changed:
        raise SystemExit("Runtime profile outputs are stale: " + ", ".join(changed))
    if not args.check:
        subprocess.run([sys.executable, str(ROOT / "tools" / "repin_configs.py")], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
