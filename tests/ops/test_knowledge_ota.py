from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.operations.knowledge_ota import verify_update
from tools.packaging.knowledge import package


def _signed_bundle(tmp_path: Path, *, issued_at: int = 100, expires_at: int = 200):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    for language in ("en", "vi", "ko", "zh"):
        (source / f"welcome_{language}.md").write_text(
            f"---\ndocument_id: welcome_{language}\nproperty_id: P\n"
            f"title: Welcome\nlanguage: {language}\nclassification: public\n"
            "effective_from: 2026-01-01\n---\n# Welcome\nWelcome to the resort.\n",
            encoding="utf-8",
        )
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    private_path.write_bytes(private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    public_path.write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    archive = tmp_path / "knowledge.zip"
    manifest, signature = package(
        source, archive, private_path, "P", 1,
        issued_at=issued_at, expires_at=expires_at)
    return archive, manifest, signature, public_path


def test_ota_requires_fresh_signed_metadata_and_rejects_replay(tmp_path: Path):
    archive, manifest, signature, public_key = _signed_bundle(tmp_path)
    state = tmp_path / "ota-state.json"
    metadata, documents = verify_update(
        archive=archive, manifest=manifest, signature=signature,
        public_key=public_key, property_id="P", state_path=state, now=150)
    assert len(documents) == 4
    state.write_text(json.dumps({"accepted_release_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="rollback or replay"):
        verify_update(archive=archive, manifest=manifest, signature=signature,
                      public_key=public_key, property_id="P", state_path=state, now=150)
    assert metadata["expires_at"] == 200


def test_ota_rejects_expired_and_future_metadata(tmp_path: Path):
    archive, manifest, signature, public_key = _signed_bundle(
        tmp_path, issued_at=1_000, expires_at=2_000)
    with pytest.raises(ValueError, match="future"):
        verify_update(archive=archive, manifest=manifest, signature=signature,
                      public_key=public_key, property_id="P",
                      state_path=tmp_path / "state.json", now=100)
    archive, manifest, signature, public_key = _signed_bundle(
        tmp_path / "expired", issued_at=100, expires_at=200)
    with pytest.raises(ValueError, match="expired"):
        verify_update(archive=archive, manifest=manifest, signature=signature,
                      public_key=public_key, property_id="P",
                      state_path=tmp_path / "expired-state.json", now=200)
