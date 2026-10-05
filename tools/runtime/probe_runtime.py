"""Inspect the configured local runtime without downloading models or changing state.

This reports runtime/model availability. It does not claim model accuracy,
approved hotel data, or physical-site acceptance.
"""
from __future__ import annotations

import json

from concierge_kiosk.core.settings import load_settings
from tools.runtime.preflight import inspect_configured_runtime


def inspect_runtime() -> dict:
    status = inspect_configured_runtime()
    status['retrieval_models_loaded'] = False
    status['actual_final_stt_loaded'] = False
    status['property_data_ready'] = False
    status['model_quality_measured'] = False
    if not status.get('runtime_ready'):
        return status
    try:
        cfg = load_settings()
        from concierge_kiosk.rag import LocalEmbedder, LocalReranker
        from concierge_kiosk.voice.runtime.adapters import _whisper

        LocalEmbedder(cfg.embedding_model_path, cfg.embedding_manifest_path)
        LocalReranker(cfg.rerank_model_path, cfg.rerank_manifest_path)
        status['retrieval_models_loaded'] = True
        _whisper(cfg.whisper_model_path)
        status['actual_final_stt_loaded'] = True
    except (ImportError, OSError, RuntimeError, ValueError, TypeError) as exc:
        status['model_load_error_type'] = type(exc).__name__

    status['runtime_ready'] = (
        status['runtime_ready']
        and status['retrieval_models_loaded']
        and status['actual_final_stt_loaded']
    )
    status['next_step'] = (
        'Check /readyz with approved property data, then run on-device acceptance tests'
    )
    return status


def main() -> int:
    result = inspect_runtime()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result['runtime_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
