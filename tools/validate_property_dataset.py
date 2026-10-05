"""Generic integrity checks for a structured concierge property dataset.

Property-specific curation rules belong beside the dataset.  This validator owns
only deployment invariants shared by every property: one manifest identity,
profile/manifest agreement, safe relative artifact paths and pinned bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from concierge_kiosk.core.dataset_layout import KNOWLEDGE_MANIFEST, PROPERTY, dataset_path


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON: {path.name}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if (not value or path.is_absolute() or ".." in path.parts or path == Path("manifest.json")):
        raise ValueError(f"Unsafe dataset artifact path: {value!r}")
    return path


def verify_dataset_manifest(dataset_root: str | Path) -> dict[str, int | str]:
    root = Path(dataset_root)
    manifest_path = dataset_path(KNOWLEDGE_MANIFEST, root)
    # Keep isolated property-swap fixtures compatible with the original flat
    # layout; production datasets use datasets/knowledge/manifest.json.
    if not manifest_path.is_file():
        manifest_path = root / "manifest.json"
    if root.is_symlink() or not root.is_dir() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("Structured dataset manifest is missing or unsafe")
    manifest = _json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("Structured dataset manifest must be an object")
    property_id = manifest.get("property_id")
    if not isinstance(property_id, str) or not property_id.strip() or len(property_id) > 64:
        raise ValueError("Structured dataset manifest requires property_id")

    profile_path = dataset_path(PROPERTY, root)
    if not profile_path.is_file():
        profile_path = root / "property-profile.json"
    if profile_path.is_file():
        if profile_path.is_symlink():
            raise ValueError("Structured dataset property profile is unsafe")
        profile = _json(profile_path)
        if isinstance(profile, dict) and profile.get("property_id") not in {None, property_id}:
            raise ValueError("Structured dataset property profile does not match manifest property_id")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Structured dataset manifest requires pinned artifacts")
    errors: list[str] = []
    for relative, metadata in artifacts.items():
        if not isinstance(relative, str) or not isinstance(metadata, dict):
            errors.append("invalid artifact metadata")
            continue
        try:
            path = dataset_path(_safe_relative(relative), root)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file() or path.is_symlink():
            errors.append(f"missing/unsafe artifact: {relative}")
            continue
        supplied_sha = metadata.get("sha256")
        supplied_size = metadata.get("size_bytes", metadata.get("bytes"))
        if (not isinstance(supplied_sha, str) or len(supplied_sha) != 64
                or any(ch not in "0123456789abcdef" for ch in supplied_sha)):
            errors.append(f"invalid sha256 metadata: {relative}")
            continue
        if not isinstance(supplied_size, int) or supplied_size < 0:
            errors.append(f"invalid size metadata: {relative}")
            continue
        if path.stat().st_size != supplied_size:
            errors.append(f"size mismatch: {relative}")
        if _sha256(path) != supplied_sha:
            errors.append(f"sha256 mismatch: {relative}")
    if errors:
        raise ValueError("Dataset manifest integrity validation failed:\n- " + "\n- ".join(errors))
    return {"property_id": property_id, "manifest_artifacts": len(artifacts), "manifest_errors": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_dataset_manifest(args.dataset), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
