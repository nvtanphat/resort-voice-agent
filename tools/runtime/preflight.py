"""Read-only runtime readiness inspection for a configured kiosk environment.

This checks local model/configuration presence only. It does not download models,
change configuration, or claim model quality or hotel acceptance.
"""
from __future__ import annotations

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.runtime.local_ai import inspect_local_ai
from concierge_kiosk.voice.runtime.readiness import incremental_voice_ready
from concierge_kiosk.voice.runtime.adapters import voice_assets
from concierge_kiosk.rag import LANGUAGES


def inspect_configured_runtime() -> dict:
    try:
        cfg = load_settings()
    except (ValueError, OSError, TypeError) as exc:
        return {
            "runtime_ready": False,
            "configuration_error": str(exc),
            "model_quality_measured": False,
            "property_data_ready": False,
        }

    status = inspect_local_ai(cfg) if cfg.local_ai_strict_mode else None
    pcm = incremental_voice_ready(
        enabled=cfg.voice_incremental_enabled,
        model_path=cfg.voice_incremental_model_path,
        manifest_path=cfg.voice_incremental_manifest_path,
        require_manifest=cfg.voice_incremental_require_manifest,
    )
    assets = voice_assets(cfg)
    signoff_verified = False
    if cfg.real_runtime_required:
        try:
            from concierge_kiosk.operations.production_signoff import verify_runtime_signoff
            verify_runtime_signoff(cfg)
            signoff_verified = True
        except (OSError, ValueError, TypeError):
            signoff_verified = False
    checks = {
        "real_runtime_profile": cfg.real_runtime_required,
        "local_slm_and_nli_ready": bool(status and status.strict_ready),
        "incremental_stt_ready": pcm,
        "final_stt_assets_present": assets["stt_available"],
        "all_local_tts_languages_present": set(assets["tts_languages"]) == LANGUAGES,
        "retrieval_models_configured": bool(cfg.embedding_model_path and cfg.rerank_model_path),
        "operator_production_signoff_verified": signoff_verified if cfg.real_runtime_required else True,
        "map_and_planning_data_configured": bool(
            cfg.map_release_path
            and cfg.map_release_sha256
            and cfg.planning_release_path
            and cfg.planning_release_sha256
        ),
    }
    return {
        "runtime_ready": all(checks.values()),
        "runtime_profile": cfg.runtime_profile_id,
        "runtime_profile_sha256": cfg.runtime_profile_sha256,
        "checks": checks,
        "model_quality_measured": False,
        "property_data_ready": False,
    }
