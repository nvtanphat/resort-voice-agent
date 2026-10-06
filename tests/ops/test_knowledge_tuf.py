from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from tools.operations.knowledge_tuf import _safe_repository_url, _safe_target_name, verified_target


@pytest.mark.parametrize("name", ["../knowledge.zip", "/knowledge.zip", "knowledge\\..\\x.zip", ""])
def test_tuf_target_paths_are_confined(name: str):
    with pytest.raises(ValueError):
        _safe_target_name(name)


def test_tuf_requires_https_outside_local_test_hosts():
    assert _safe_repository_url("http://localhost:8000/metadata", label="metadata_base_url").endswith("/")
    with pytest.raises(ValueError, match="https"):
        _safe_repository_url("http://updates.example.invalid/metadata", label="metadata_base_url")


def test_tuf_client_is_optional_but_explicit(tmp_path: Path):
    # Deployment validation must make the missing optional dependency visible;
    # it must never silently fall back to the legacy envelope OTA path.
    from tools.operations.knowledge_tuf import TUFUnavailable, _updater

    root = tmp_path / "root.json"
    root.write_text("{}", encoding="utf-8")
    if __import__("importlib.util").util.find_spec("tuf") is None:
        with pytest.raises(TUFUnavailable, match="python-tuf"):
            _updater(metadata_dir=tmp_path / "metadata", metadata_base_url="http://localhost:8000/metadata",
                     target_dir=tmp_path / "targets", target_base_url="http://localhost:8000/targets",
                     bootstrap_root=root)


def _write_tuf_repository(root: Path) -> None:
    metadata = pytest.importorskip("tuf.api.metadata")
    from securesystemslib.signer import CryptoSigner

    signer = CryptoSigner.generate_ed25519()
    key = signer.public_key
    keyid = key.keyid
    role = {name: metadata.Role([keyid], 1) for name in ("root", "timestamp", "snapshot", "targets")}
    expiry = datetime.now(timezone.utc) + timedelta(days=2)
    tuf_root = metadata.Root(version=1, spec_version="1.0.31", expires=expiry,
                             keys={keyid: key}, roles=role, consistent_snapshot=False)
    target_path = root / "targets" / "knowledge.zip"
    target_path.parent.mkdir(parents=True)
    target_path.write_bytes(b"verified-through-tuf")
    targets = metadata.Targets(
        version=1, spec_version="1.0.31", expires=expiry,
        targets={"knowledge.zip": metadata.TargetFile.from_file(
            "knowledge.zip", str(target_path), ["sha256"])},
    )
    targets_meta = metadata.Metadata(targets)
    targets_meta.sign(signer)
    targets_bytes = targets_meta.to_bytes()
    snapshot = metadata.Snapshot(
        version=1, spec_version="1.0.31", expires=expiry,
        meta={"targets.json": metadata.MetaFile(
            version=1, length=len(targets_bytes),
            hashes={"sha256": hashlib.sha256(targets_bytes).hexdigest()})},
    )
    snapshot_meta = metadata.Metadata(snapshot)
    snapshot_meta.sign(signer)
    snapshot_bytes = snapshot_meta.to_bytes()
    timestamp = metadata.Timestamp(
        version=1, spec_version="1.0.31", expires=expiry,
        snapshot_meta=metadata.MetaFile(
            version=1, length=len(snapshot_bytes),
            hashes={"sha256": hashlib.sha256(snapshot_bytes).hexdigest()}),
    )
    timestamp_meta = metadata.Metadata(timestamp)
    timestamp_meta.sign(signer)
    documents = {
        "root.json": metadata.Metadata(tuf_root),
        "targets.json": targets_meta,
        "snapshot.json": snapshot_meta,
        "timestamp.json": timestamp_meta,
    }
    documents["root.json"].sign(signer)
    metadata_dir = root / "metadata"
    metadata_dir.mkdir()
    for name, document in documents.items():
        (metadata_dir / name).write_bytes(document.to_bytes())


def test_real_python_tuf_target_verification_when_extra_is_provisioned(tmp_path: Path):
    pytest.importorskip("tuf")
    _write_tuf_repository(tmp_path)
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with verified_target(
            metadata_dir=tmp_path / "client-metadata", metadata_base_url=base + "/metadata",
            target_dir=tmp_path / "client-targets", target_base_url=base + "/targets",
            bootstrap_root=tmp_path / "metadata" / "root.json", target_name="knowledge.zip",
        ) as target:
            assert target.read_bytes() == b"verified-through-tuf"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
