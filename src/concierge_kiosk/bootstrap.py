"""Application composition: validate settings and construct owned dependencies.

No HTTP routes or process-global state live here.
"""
from __future__ import annotations

from .core.settings import Settings, load_settings
from .persistence.sqlite_store import Store
from .domain.service_requests import Workflows
from .agent.understanding.intent import emergency_response
from .rag import RAGPolicy, LocalEmbedder, LocalReranker


def prepare_runtime(settings: Settings | None, embedder, reranker):
    cfg = settings or load_settings()
    cfg.validate()
    if cfg.real_runtime_required:
        # A production process must use the provisioned model assets, never a
        # test double supplied through the application factory. Construct the
        # heavy retrieval models before opening/creating business storage so a
        # bad deployment cannot leave a seemingly initialized empty database.
        if embedder is not None or reranker is not None:
            raise RuntimeError('Production does not accept injected retrieval models')
        # Full production profile: configuration alone is not operational
        # readiness. A missing model must stop startup, never be presented as
        # an installed or benchmarked component.
        from .runtime.local_ai import inspect_local_ai
        from .voice.runtime.readiness import incremental_voice_ready
        from .voice.runtime.adapters import voice_assets
        from .rag import LANGUAGES
        if not inspect_local_ai(cfg).strict_ready:
            raise RuntimeError('Strict local SLM/NLI runtime unavailable or model pin mismatch')
        if not incremental_voice_ready(
                enabled=cfg.voice_incremental_enabled,
                model_path=cfg.voice_incremental_model_path,
                manifest_path=cfg.voice_incremental_manifest_path,
                require_manifest=cfg.voice_incremental_require_manifest):
            raise RuntimeError('Pinned incremental speech decoder unavailable')
        assets = voice_assets(cfg)
        if not assets['stt_available'] or set(assets['tts_languages']) != LANGUAGES:
            raise RuntimeError('Real multilingual STT and licensed local voices are required')
        from .voice.runtime.final_assets import check_final_voice_manifest, probe_final_tts
        if cfg.voice_final_require_manifest and not check_final_voice_manifest(cfg, cfg.voice_final_manifest_path):
            raise RuntimeError('Pinned final Whisper/Piper assets are unavailable or changed')
        from .voice.runtime.adapters import _whisper
        try:
            _whisper(cfg.whisper_model_path)
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            raise RuntimeError('Local final-window STT model cannot be loaded') from exc
        try:
            probe_final_tts(cfg)
        except (OSError, ValueError, RuntimeError) as exc:
            raise RuntimeError('Configured final Piper voices failed synthesis readiness') from exc
        try:
            embedder = LocalEmbedder(cfg.embedding_model_path, cfg.embedding_manifest_path,
                                     num_gpu=cfg.slm_num_gpu)
            reranker = LocalReranker(cfg.rerank_model_path, cfg.rerank_manifest_path)
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            raise RuntimeError('Local embedding/reranking models cannot be loaded') from exc
        try:
            from .operations.production_signoff import verify_runtime_signoff
            verify_runtime_signoff(cfg)
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError('Operator production sign-off is missing, invalid, or stale') from exc
    store = Store(cfg.db_path)
    rag_policy = RAGPolicy(rrf_k=cfg.rag_rrf_k,
                           min_dense_similarity=cfg.rag_min_dense_similarity,
                           lexical_coverage=cfg.rag_lexical_coverage,
                           dense_max_rows=cfg.rag_dense_max_rows,
                           dense_budget_ms=cfg.rag_dense_budget_ms,
                           rerank_budget_ms=cfg.rag_rerank_budget_ms,
                           rerank_top_k=cfg.rag_rerank_top_k,
                           rerank_max_length=cfg.rag_rerank_max_length,
                           rerank_input=cfg.rag_rerank_input,
                           rerank_fusion_alpha=cfg.rag_rerank_fusion_alpha,
                           rerank_metadata_bonus=cfg.rag_rerank_metadata_bonus,
                           rerank_on_failure=cfg.rag_rerank_on_failure)
    rag_policy.validate()
    from .integrations.hotel_ops import integrations_from_env, room_inventory_from_env
    guest_verifier, service_dispatcher = integrations_from_env()
    room_validator = room_inventory_from_env()
    workflows = Workflows(store, cfg.property_id, cfg.proposal_ttl_seconds,
                          emergency_detector=lambda text, language: emergency_response(text, language) is not None,
                          guest_verifier=guest_verifier, service_dispatcher=service_dispatcher,
                          room_validator=room_validator, cfg=cfg)
    # Models are optional; a model loading failure must not silently claim hybrid retrieval.
    if embedder is None and cfg.embedding_model_path:
        embedder = LocalEmbedder(cfg.embedding_model_path, cfg.embedding_manifest_path,
                                 num_gpu=cfg.slm_num_gpu)
    if reranker is None and cfg.rerank_model_path:
        reranker = LocalReranker(cfg.rerank_model_path, cfg.rerank_manifest_path)

    # Ollama loads BGE lazily. Warm the loopback model once at composition time
    # so the first guest retrieval fits the bounded dense budget instead of
    # timing out while the model is loaded on demand.
    if (embedder is not None and getattr(embedder, 'backend', '') == 'ollama'):
        try:
            embedder.encode_query('concierge embedding warmup')
        except (ConnectionError, OSError, RuntimeError, ValueError, TimeoutError):
            # Retrieval still degrades to the authorized lexical path when the
            # local model is unavailable; startup must remain usable offline.
            pass
    if reranker is not None:
        try:
            reranker.score('concierge reranker warmup', ['verified concierge passage'])
        except (ConnectionError, OSError, RuntimeError, ValueError, TimeoutError):
            # A cold or unavailable optional reranker must never prevent the
            # lexical/dense retrieval path from serving a grounded answer.
            pass

    import logging
    from .rag.index.health import dense_index_status
    health = dense_index_status(store, cfg.property_id, embedder)
    if health['state'] != 'ok':
        logging.getLogger(__name__).warning(
            'dense_retrieval_disabled state=%s embedder=%s indexed=%s - answers use lexical retrieval only',
            health['state'], health.get('model', ''), health.get('indexed_models'))
    return cfg, store, rag_policy, workflows, embedder, reranker
