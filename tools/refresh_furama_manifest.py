"""Refresh or verify the current canonical knowledge manifest.

The manifest describes ``datasets/knowledge``.  Evaluation, quarantine and
synthetic operational files are intentionally outside this RAG-truth manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import (
    KNOWLEDGE_MANIFEST, canonical_text_bytes, dataset_path, dataset_root,
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _metadata(path: Path, previous: dict[str, Any]) -> dict[str, Any]:
    result = dict(previous)
    canonical = canonical_text_bytes(path)
    result["sha256"] = hashlib.sha256(canonical).hexdigest()
    result["bytes"] = len(canonical)
    if path.suffix == ".jsonl" or "records" in previous:
        result["records"] = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return result


def build_manifest(root: str | Path | None = None) -> dict[str, Any]:
    dataset = dataset_root(root)
    manifest_path = dataset_path(KNOWLEDGE_MANIFEST, dataset)
    manifest = _load(manifest_path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Knowledge manifest requires pinned artifacts")
    updated: dict[str, Any] = {}
    for relative, previous in artifacts.items():
        if not isinstance(relative, str) or not isinstance(previous, dict):
            raise ValueError(f"Invalid manifest artifact: {relative!r}")
        path = dataset_path(relative, dataset)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe manifest artifact: {relative}")
        updated[relative] = _metadata(path, previous)
    # Localization metadata is canonical RAG truth, not a code-side translation
    # table. Keep it covered by the same integrity pin as facts/entities.
    for relative in (
        "knowledge/canonical/context_labels.json",
        "knowledge/canonical/entity_display_labels.json",
    ):
        if relative in updated:
            continue
        path = dataset_path(relative, dataset)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe manifest artifact: {relative}")
        updated[relative] = _metadata(path, {})
    result = dict(manifest)
    result["artifacts"] = updated
    return result


def refresh_manifest(root: str | Path | None = None) -> dict[str, Any]:
    dataset = dataset_root(root)
    path = dataset_path(KNOWLEDGE_MANIFEST, dataset)
    manifest = build_manifest(dataset)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_manifest(root: str | Path | None = None) -> dict[str, int | str]:
    dataset = dataset_root(root)
    supplied = _load(dataset_path(KNOWLEDGE_MANIFEST, dataset))
    expected = build_manifest(dataset)
    if supplied.get("property_id") != expected.get("property_id"):
        raise ValueError("Knowledge manifest property_id mismatch")
    if supplied.get("artifacts") != expected.get("artifacts"):
        raise ValueError("Knowledge manifest hashes/counts do not match canonical artifacts")
    return {"manifest_artifacts": len(expected["artifacts"]), "manifest_errors": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=dataset_root())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = verify_manifest(args.dataset) if args.check else refresh_manifest(args.dataset)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
