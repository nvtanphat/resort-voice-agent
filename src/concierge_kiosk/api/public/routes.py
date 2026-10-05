"""Public/static, health, readiness, config, service-catalog and map endpoints."""
from __future__ import annotations
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from concierge_kiosk.api.shared.contracts import PublicConfigResponse, ServiceCatalogResponse, UiContractResponse
from concierge_kiosk.agent.tools.navigation import read_approved_map, public_map_places, MapUnavailable
from concierge_kiosk.agent.tools.scheduling import approved_schedule, ScheduleUnavailable
from concierge_kiosk.core.clock import property_today
from concierge_kiosk.core.domain_profile import get_domain_profile, voice_policy
from concierge_kiosk.rag import LANGUAGES, validate_knowledge_index
from concierge_kiosk.voice.runtime.adapters import voice_assets

def register_public_routes(app, *, cfg, store, web_dir, pcm_permitted, slm_permitted,
                           get_graph, property_profile, embedder, guest_session, rate,
                           strict_ai_ready_at_boot, logger) -> None:
    @app.get("/")
    def home():
        return FileResponse(web_dir / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/ops")
    def ops():
        return FileResponse(web_dir / "ops.html", headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    def health():
        with store.connection() as con:
            con.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @app.get("/readyz")
    def readiness():
        if cfg.real_runtime_required:
            if not pcm_permitted() or not slm_permitted():
                raise HTTPException(status_code=503, detail='Pinned offline AI/Voice runtime unavailable')
        if cfg.map_release_path:
            map_today = property_today(cfg.property_timezone)
            try:
                approved_map = read_approved_map(
                    cfg.map_release_path, cfg.map_release_sha256, cfg.property_id, as_of=map_today)
            except (MapUnavailable, OSError) as exc:
                raise HTTPException(status_code=503, detail='Approved map release unavailable') from exc
            with store.connection() as con:
                map_source_languages = {
                    row['language'] for row in con.execute(
                        'SELECT language,revision FROM knowledge WHERE property_id=? AND source=? '
                        "AND classification='public' AND active=1 AND effective_from<=? "
                        'AND (effective_to IS NULL OR effective_to>=?)',
                        (cfg.property_id, approved_map['source_id'], map_today, map_today))
                    if row['revision'] == approved_map['source_revisions'].get(row['language'])}
            if not LANGUAGES.issubset(map_source_languages):
                raise HTTPException(status_code=503, detail='Approved multilingual map source unavailable')
        if cfg.real_runtime_required:
            # A configured path/hash is not proof that a current activity
            # release is usable for each of the supported languages.
            try:
                for language in LANGUAGES:
                    approved_schedule(store, path=cfg.planning_release_path,
                                      expected_sha256=cfg.planning_release_sha256,
                                      property_id=cfg.property_id, language=language,
                                      as_of=property_today(cfg.property_timezone))
            except (ScheduleUnavailable, OSError) as exc:
                raise HTTPException(status_code=503,
                                    detail='Approved multilingual planning release unavailable') from exc
        if cfg.orchestrator == "langgraph":
            # Probe the durable store on every readiness check; graph object
            # creation alone does not prove checkpoints are still writable/readable.
            try:
                get_graph().check_readiness()
            except Exception as exc:
                # A damaged serialized checkpoint can raise msgpack errors
                # which are not necessarily sqlite3.Error or ValueError.
                logger.error("langgraph_readiness_failed type=%s", type(exc).__name__)
                raise HTTPException(status_code=503, detail="Durable agent orchestrator unavailable") from exc
        today = property_today(cfg.property_timezone)
        with store.connection() as con:
            rows = con.execute("SELECT DISTINCT language FROM knowledge WHERE property_id=? "
                               "AND classification='public' AND active=1 AND effective_from<=? "
                               "AND (effective_to IS NULL OR effective_to>=?)",
                               (cfg.property_id, today, today)).fetchall()
        languages = {row[0] for row in rows}
        try:
            validate_knowledge_index(store)
        except RuntimeError as exc:
            logger.error('rag_index_readiness_failed')
            raise HTTPException(status_code=503, detail='Knowledge index unavailable') from exc
        if not LANGUAGES.issubset(languages):
            raise HTTPException(status_code=503, detail="Approved multilingual knowledge missing")
        if cfg.environment == "production":
            with store.connection() as con:
                release = con.execute('SELECT release_version FROM knowledge_releases WHERE property_id=?',
                                      (cfg.property_id,)).fetchone()
            if release is None:
                raise HTTPException(status_code=503, detail="Signed property knowledge release missing")
            assets = voice_assets(cfg)
            if not assets["stt_available"] or set(assets["tts_languages"]) != LANGUAGES:
                raise HTTPException(status_code=503, detail="Offline speech assets not provisioned")
            if cfg.real_runtime_required and cfg.voice_final_require_manifest:
                from concierge_kiosk.voice.runtime.final_assets import check_final_voice_manifest
                if not check_final_voice_manifest(cfg, cfg.voice_final_manifest_path):
                    raise HTTPException(status_code=503, detail="Pinned final speech assets changed after startup")
        from concierge_kiosk.rag.index.health import dense_index_status
        dense = dense_index_status(store, cfg.property_id, embedder)
        if cfg.real_runtime_required and dense['state'] != 'ok':
            raise HTTPException(status_code=503, detail='Dense retrieval index does not match the embedder')
        return {"status": "ready", "knowledge_languages": sorted(languages),
                "dense_retrieval": dense['state']}

    @app.get("/api/config", response_model=PublicConfigResponse)
    def public_config():
        from concierge_kiosk.voice.agent.pipeline import pipecat_available
        assets = voice_assets(cfg)
        pcm_ready = pcm_permitted()
        current_ai_ready = bool(strict_ai_ready_at_boot and slm_permitted())
        profile_config = property_profile.public_config()
        domain_voice = voice_policy()
        profile_config['voice_policy'].update({
            'continuation_cues': domain_voice.get('continuation_cues', {}),
            'clause_delimiters': domain_voice.get('clause_delimiters', {}),
            'sentence_endings': domain_voice.get('sentence_endings', {}),
        })
        return {"product": "Concierge Kiosk", "public_origin": cfg.public_origin, **profile_config,
                "api_version": 2,
                "languages": sorted(LANGUAGES),
                "voice_available": bool((assets["stt_available"] or pcm_ready) and assets["tts_languages"]),
                "voice_transport": cfg.voice_transport,
                "voice_agent_available": bool(cfg.voice_transport == "pipecat" and pipecat_available()),
                "incremental_voice_available": pcm_ready,
                "incremental_voice_language": cfg.voice_incremental_language,
                "stt_modes": {language: ("incremental_pcm16" if pcm_ready and language == cfg.voice_incremental_language
                                         else "windowed_audio") for language in sorted(LANGUAGES)},
                "tts_languages": assets["tts_languages"],
                "max_audio_bytes": cfg.max_audio_bytes,
                "max_audio_seconds": cfg.max_audio_seconds,
                "retrieval_mode": "hybrid" if embedder else "lexical",
                "orchestrator": cfg.orchestrator,
                "generation_mode": ("local_semantic_ready" if current_ai_ready else "extractive")
                if cfg.local_ai_strict_mode else
                ("local_slm_configured" if cfg.llm_base_url and cfg.llm_model else "extractive")}

    @app.get("/api/ui-contract", response_model=UiContractResponse)
    def ui_contract():
        ui = get_domain_profile().ui
        enabled_languages = tuple(property_profile.enabled_languages)
        catalog_kinds = {item.get('request_kind') for item in property_profile.service_catalog
                         if item.get('request_kind') is not None}
        create_enabled = bool(ui.capabilities.get('create_request', False))
        request_types = []
        for kind in sorted(ui.request_types):
            spec = ui.request_types[kind]
            actions = [action for action in spec['actions']
                       if action != 'create_request' or (create_enabled and kind in catalog_kinds)]
            request_types.append({
                'kind': kind,
                'labels': {language: spec['labels'][language] for language in enabled_languages},
                'icon_category': spec['icon_category'],
                'fields': list(spec['fields']),
                'actions': actions,
            })
        return {
            'contract_version': ui.contract_version,
            'languages': [
                {'code': code, 'label': ui.language_labels[code][code]}
                for code in enabled_languages
            ],
            'request_types': request_types,
            'capabilities': {
                'service_menu': bool(ui.capabilities.get('service_menu', False) and property_profile.service_catalog),
                'create_request': bool(create_enabled and any('create_request' in item['actions'] for item in request_types)),
                'language_switch': bool(ui.capabilities.get('language_switch', False) and len(enabled_languages) > 1),
            },
        }

    @app.get("/api/services", response_model=ServiceCatalogResponse)
    def service_catalog():
        ui = get_domain_profile().ui
        items = []
        if not ui.capabilities.get('service_menu', False):
            return {'items': items}
        for raw in property_profile.service_catalog:
            item = dict(raw)
            kind = item.get('request_kind')
            spec = ui.request_types.get(kind, {}) if kind else {}
            item['icon_category'] = spec.get('icon_category', 'info')
            item['actions'] = list(spec.get('actions', ['ask']))
            items.append(item)
        return {'items': items}

    @app.get('/api/map/places')
    def map_places(request: Request, language: str = 'en',
                   session: str = Depends(guest_session)):
        rate(request, f'map-places:{session}', 60)
        if language not in property_profile.enabled_languages:
            raise HTTPException(status_code=422, detail='Language is not enabled for this property')
        return public_map_places(store, path=cfg.map_release_path,
                                 expected_sha256=cfg.map_release_sha256,
                                 property_id=cfg.property_id, language=language,
                                 as_of=property_today(cfg.property_timezone))
