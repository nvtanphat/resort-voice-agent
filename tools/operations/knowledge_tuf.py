"""Fetch a signed knowledge target through a real python-tuf client.

The existing ``knowledge_ota`` module remains the final package boundary: it
authenticates the Ed25519 package, checks freshness and applies the SQLite
release atomically.  This module adds the remote delivery boundary.  TUF
authenticates the target bytes and validates root/timestamp/snapshot/targets
metadata; the package signature remains a second, application-level trust
domain.  A pinned trusted ``root.json`` (bootstrap) is required on every
invocation.

Install the optional ``ota-tuf`` extra before using this command.  If it is not
installed, the command fails closed instead of silently falling back to the
legacy envelope downloader.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import inspect
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse

from tools.operations.knowledge_ota import apply_update


class TUFUnavailable(RuntimeError):
    """Raised when the optional TUF client is not installed."""


def _safe_directory(path: Path, *, create: bool = False) -> Path:
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"TUF directory must be a real directory: {path}")
    return path


def _safe_target_name(name: str) -> str:
    candidate = str(name or "").replace("\\", "/")
    parts = candidate.split("/")
    if (not candidate or candidate.startswith("/") or any(part in {"", ".", ".."} for part in parts)
            or ":" in candidate):
        raise ValueError("TUF target name must be a relative POSIX path")
    return candidate


def _safe_repository_url(value: str, *, label: str) -> str:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError(f"{label} must be an https URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"{label} must use https outside local test hosts")
    return str(value).rstrip("/") + "/"


def _updater(*, metadata_dir: Path, metadata_base_url: str, target_dir: Path,
             target_base_url: str, bootstrap_root: Path):
    try:
        from tuf.ngclient import Updater
    except ImportError as exc:  # pragma: no cover - exercised in deployment validation
        raise TUFUnavailable(
            "python-tuf is not installed; install the project's ota-tuf extra") from exc
    if bootstrap_root.is_symlink() or not bootstrap_root.is_file():
        raise ValueError("A pinned, non-symlink TUF root.json is required")
    common = {
        "metadata_dir": str(metadata_dir),
        "metadata_base_url": _safe_repository_url(metadata_base_url, label="metadata_base_url"),
        "target_dir": str(target_dir),
        "target_base_url": _safe_repository_url(target_base_url, label="target_base_url"),
    }
    if "bootstrap" in inspect.signature(Updater).parameters:
        # Newer ngclient releases accept the root bytes directly and persist
        # them as the initial trust anchor.
        return Updater(**common, bootstrap=bootstrap_root.read_bytes())

    # python-tuf 5.x uses the older repository_dir API shape (the project pins
    # the 5.x line for the edge image).  Seed root.json exactly once; after the
    # first refresh, TUF itself controls trusted root rotation.  Never replace
    # a cached root with the operator bootstrap on a later invocation.
    cached_root = metadata_dir / "root.json"
    if cached_root.is_symlink():
        raise ValueError("Cached TUF root.json may not be a symlink")
    if cached_root.exists() and not cached_root.is_file():
        raise ValueError("Cached TUF root.json must be a regular file")
    if not cached_root.exists():
        temporary = metadata_dir / ".root-bootstrap.tmp"
        try:
            temporary.write_bytes(bootstrap_root.read_bytes())
            os.replace(temporary, cached_root)
        finally:
            temporary.unlink(missing_ok=True)
    return Updater(**common)


@contextmanager
def verified_target(*, metadata_dir: Path, metadata_base_url: str, target_dir: Path,
                    target_base_url: str, bootstrap_root: Path,
                    target_name: str):
    """Yield a target after TUF refresh and hash/length verification.

    The temporary target is never exposed as an accepted knowledge release by
    this function.  The caller must still invoke ``knowledge_ota.apply_update``
    to check the application signature, freshness and rollback state.
    """
    metadata_dir = _safe_directory(metadata_dir, create=True)
    target_dir = _safe_directory(target_dir, create=True)
    name = _safe_target_name(target_name)
    updater = _updater(
        metadata_dir=metadata_dir, metadata_base_url=metadata_base_url,
        target_dir=target_dir, target_base_url=target_base_url,
        bootstrap_root=bootstrap_root,
    )
    updater.refresh()
    target_info = updater.get_targetinfo(name)
    if target_info is None:
        raise ValueError(f"TUF target is not present: {name}")
    with tempfile.TemporaryDirectory(prefix="tuf-target-", dir=target_dir) as temporary:
        destination = Path(temporary) / Path(name).name
        downloaded = Path(updater.download_target(target_info, filepath=str(destination)))
        if downloaded != destination or downloaded.is_symlink() or not downloaded.is_file():
            raise ValueError("python-tuf returned an unsafe target path")
        yield downloaded


def apply_tuf_update(*, metadata_dir: Path, metadata_base_url: str, target_dir: Path,
                     target_base_url: str, bootstrap_root: Path, target_name: str,
                     manifest: Path, signature: Path, public_key: Path,
                     property_id: str, state_path: Path, effective_date: str,
                     db: Path | None = None, now: int | None = None,
                     embedder=None) -> dict:
    """TUF-fetch an archive, then apply the existing signed package contract."""
    with verified_target(
        metadata_dir=metadata_dir, metadata_base_url=metadata_base_url,
        target_dir=target_dir, target_base_url=target_base_url,
        bootstrap_root=bootstrap_root, target_name=target_name,
    ) as archive:
        if db is None:
            from tools.packaging.knowledge import read_signed_package
            metadata, documents = read_signed_package(
                archive, manifest, signature, public_key, property_id=property_id)
            return {"status": "verified", "property_id": property_id,
                    "release_version": metadata["release_version"],
                    "documents": len(documents), "delivery": "tuf"}
        result = apply_update(
            archive=archive, manifest=manifest, signature=signature,
            public_key=public_key, property_id=property_id, db=db,
            state_path=state_path, embedder=embedder,
            effective_date=effective_date, now=now,
        )
        return {**result, "delivery": "tuf"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    parser.add_argument("--metadata-base-url", required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--target-base-url", required=True)
    parser.add_argument("--bootstrap-root", type=Path, required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--property-id", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--effective-date", default="2026-10-05")
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    result = apply_tuf_update(
        metadata_dir=args.metadata_dir, metadata_base_url=args.metadata_base_url,
        target_dir=args.target_dir, target_base_url=args.target_base_url,
        bootstrap_root=args.bootstrap_root, target_name=args.target_name,
        manifest=args.manifest, signature=args.signature, public_key=args.public_key,
        property_id=args.property_id, state_path=args.state,
        effective_date=args.effective_date, db=args.db,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
