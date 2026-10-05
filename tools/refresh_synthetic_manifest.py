"""Refresh or verify hashes in the canonical synthetic-data manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "synthetic"
MANIFEST = DATASET / "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    updated = {}
    for key, previous in manifest["artifacts"].items():
        relative = key.removeprefix("synthetic/")
        path = DATASET / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe synthetic artifact: {relative}")
        metadata = dict(previous)
        metadata["sha256"] = _sha256(path)
        metadata["bytes"] = path.stat().st_size
        if path.suffix == ".jsonl" or "records" in metadata:
            metadata["records"] = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        updated[relative] = metadata
    result = dict(manifest)
    result["artifacts"] = updated
    result["metrics"] = {
        **manifest.get("metrics", {}),
        "jsonl_records": sum(meta.get("records", 0) for meta in updated.values()),
        "jsonl_files": sum(1 for key in updated if key.endswith(".jsonl")),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = build()
    supplied = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.check:
        if supplied != expected:
            raise SystemExit("synthetic manifest hashes or paths are stale")
    else:
        MANIFEST.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifacts": len(expected["artifacts"]), "status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
