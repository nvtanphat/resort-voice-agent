"""Production provisioning preflight without opening the business database."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from concierge_kiosk.core.runtime_profile import RuntimeProfile


def _value(env: Mapping[str, str], name: str, fallback: str = "") -> str:
    return str(env.get(name, fallback)).strip()


def _file_or_dir(value: str) -> bool:
    if not value:
        return False
    path = Path(value)
    return path.exists() and not path.is_symlink()


def production_provisioning_gaps(profile: RuntimeProfile, env: Mapping[str, str] | None = None) -> list[str]:
    """Return missing operator-provisioned runtime assets, never touching SQLite.

    Runtime-profile defaults are combined with environment overrides using the
    same public environment names as ``load_settings``. This is intentionally a
    preflight: cryptographic manifest verification still happens in Settings.
    """
    env = os.environ if env is None else env
    models = profile.models
    gaps: list[str] = []

    slm = models["slm"]
    digest = _value(env, "CONCIERGE_LLM_MODEL_DIGEST", slm["digest"])
    if len(digest.removeprefix("sha256:")) != 64:
        gaps.append("pinned local SLM digest (CONCIERGE_LLM_MODEL_DIGEST)")

    embedding_model = _value(env, "CONCIERGE_EMBEDDING_MODEL_PATH")
    embedding_manifest = _value(env, "CONCIERGE_EMBEDDING_MANIFEST_PATH")
    if not (embedding_model and embedding_manifest):
        candidate_model, candidate_manifest = profile.embedding_assets()
        embedding_model = embedding_model or candidate_model
        embedding_manifest = embedding_manifest or candidate_manifest
    if not (_file_or_dir(embedding_model) and Path(embedding_manifest).is_file()):
        gaps.append("local embedding model + manifest")

    reranker = models["reranker"]
    reranker_model = _value(env, "CONCIERGE_RERANK_MODEL_PATH", reranker["model_path"])
    reranker_manifest = _value(env, "CONCIERGE_RERANK_MANIFEST_PATH", reranker["manifest_path"])
    if not (_file_or_dir(reranker_model) and Path(reranker_manifest).is_file()):
        gaps.append("local reranker model + manifest")

    nli = models["nli"]
    nli_model = _value(env, "CONCIERGE_NLI_MODEL_PATH", nli["model_path"])
    nli_manifest = _value(env, "CONCIERGE_NLI_MANIFEST_PATH", nli["manifest_path"])
    if not (_file_or_dir(nli_model) and Path(nli_manifest).is_file()):
        gaps.append("independent NLI model + manifest")

    voice = models["voice"]
    whisper = _value(env, "CONCIERGE_WHISPER_MODEL_PATH", voice["whisper_model_path"])
    piper_dir = _value(env, "CONCIERGE_PIPER_MODELS_DIR", voice["piper_models_dir"])
    incremental = _value(env, "CONCIERGE_VOICE_INCREMENTAL_MODEL_PATH", voice["incremental_model_path"])
    incremental_manifest = _value(env, "CONCIERGE_VOICE_INCREMENTAL_MANIFEST_PATH", voice["incremental_manifest_path"])
    final_manifest = _value(env, "CONCIERGE_VOICE_FINAL_MANIFEST_PATH", voice["final_manifest_path"])
    if not _file_or_dir(whisper):
        gaps.append("final Whisper model")
    if not (piper_dir and Path(piper_dir).is_dir() and not Path(piper_dir).is_symlink()):
        gaps.append("Piper voice directory")
    if not (_file_or_dir(incremental) and Path(incremental_manifest).is_file()):
        gaps.append("incremental STT model + manifest")
    if not Path(final_manifest).is_file():
        gaps.append("final voice asset manifest")

    # Non-model production authority/trust inputs live outside the runtime profile.
    required_env = {
        "CONCIERGE_PROPERTY_PROFILE_PATH": "signed property profile",
        "CONCIERGE_PROPERTY_PROFILE_SHA256": "property profile SHA-256",
        "CONCIERGE_PROPERTY_PROFILE_SIGNATURE_PATH": "property profile signature",
        "CONCIERGE_PROPERTY_PROFILE_PUBLIC_KEY_PATH": "property profile public key",
        "CONCIERGE_MAP_RELEASE_PATH": "approved map release",
        "CONCIERGE_MAP_RELEASE_SHA256": "map release SHA-256",
        "CONCIERGE_PLANNING_RELEASE_PATH": "approved activity release",
        "CONCIERGE_PLANNING_RELEASE_SHA256": "activity release SHA-256",
        "CONCIERGE_PRODUCTION_SIGNOFF_PATH": "production readiness receipt",
        "CONCIERGE_PRODUCTION_SIGNOFF_SIGNATURE_PATH": "production readiness signature",
        "CONCIERGE_PRODUCTION_SIGNOFF_PUBLIC_KEY_PATH": "production readiness public key",
    }
    for name, label in required_env.items():
        if not _value(env, name):
            gaps.append(f"{label} ({name})")
    return gaps
