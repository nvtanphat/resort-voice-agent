"""Refresh or verify the repository-level dataset manifest hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "datasets"
MANIFEST = DATA / "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = []
    for previous in manifest["files"]:
        path = DATA / previous["path"]
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe dataset artifact: {previous['path']}")
        item = {"path": previous["path"], "bytes": path.stat().st_size, "sha256": _sha256(path)}
        if path.suffix == ".jsonl" or "records" in previous:
            item["records"] = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        files.append(item)
    result = dict(manifest)
    result["file_count"] = len(files)
    result["files"] = files
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = build()
    supplied = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.check:
        if supplied != expected:
            raise SystemExit("dataset manifest hashes are stale")
    else:
        MANIFEST.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(expected["files"]), "status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
