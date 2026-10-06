"""Single-property edge API composition root.

Runtime behavior lives in application/domain/capability packages; this module
only wires concrete adapters, HTTP routes and lifecycle resources.
"""
from __future__ import annotations

import logging
import json
import os
import sqlite3
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent.memory.conversation import ConversationMemory
from .agent.memory.task_memory import AgentTaskMemory
from .agent.memory.preferences import SessionPreferenceMemoryStore
from .agent.proactive import ProactiveEngine
from .agent.runtime.persistence import AgentCheckpointStore, SessionSemanticMemoryStore
from .api.guest.routes import register_guest_routes
from .api.internal.routes import register_internal_agent_routes
from .api.shared.auth import build_auth_dependencies
from .api.shared.contracts import ClientTelemetry
from .api.shared.security import install_http_security
from .api.shared.status_tokens import StatusTokenService
from .api.staff.routes import register_staff_routes
from .application import TurnFinalizer, WorkflowApplicationService
from .bootstrap import prepare_runtime
from .core.property_profile import load_property_profile, unconfigured_property_profile
from .core.domain_profile import get_domain_profile, load_domain_profile, voice_policy
from .agent.understanding.service_selector import ServiceSelector, load_command_examples
from .core.dataset_layout import (
    SERVICE_CATALOG, TRAIN_AGENT_MULTILINGUAL, TRAIN_AGENT_VI_GOLD, dataset_path,
)
from .core.settings import Settings
from .domain.service_requests import InvalidTransition
from .runtime.metrics import latency_bucket
from .runtime.turn_events import TurnEvents
from .application import SpeechService
from .voice.runtime.adapters import synthesize, synthesize_cancellable, transcribe, transcribe_detected, validate_audio
from .voice.session.turns import VoiceTurns


def _web_directory() -> Path:
    """Find frontend assets in source checkout, Docker or an installed wheel.

    Wheel data-files install under the environment's share directory. An explicit
    CONCIERGE_WEB_DIR is always authoritative (including a misspelled path).
    """
    configured = os.environ.get("CONCIERGE_WEB_DIR")
    if configured is not None:
        return Path(configured)
    source = Path(__file__).resolve().parents[2] / "web"
    # The wheel's data-files go to <install-root>/share; a --target install
    # uses the target directory, which is *not* sys.prefix. Check both.
    candidates = (
        source,
        Path(__file__).resolve().parents[1] / "share" / "concierge-kiosk" / "web",
        Path(sys.prefix) / "share" / "concierge-kiosk" / "web",
    )
    for directory in candidates:
        if ((directory / "index.html").is_file() and (directory / "staff.html").is_file()
                and (directory / "status.html").is_file() and (directory / "status.js").is_file()):
            return directory
    # Retain an explicit missing-asset failure at FastAPI startup rather than
    # silently serving an empty or unrelated directory.
    return candidates[-1]


WEB_DIR = _web_directory()
LOGGER = logging.getLogger('concierge_kiosk')




def _load_bound_profiles(cfg, workflows):
    domain_profile = (load_domain_profile(cfg.domain_profile_path, cfg.domain_profile_sha256)
                      if cfg.domain_profile_path else get_domain_profile())
    active_domain = get_domain_profile()
    if domain_profile.sha256 != active_domain.sha256:
        raise RuntimeError('Configured agent domain profile differs from process domain registry')
    if cfg.property_profile_path:
        property_profile = load_property_profile(
            cfg.property_profile_path, cfg.property_profile_sha256,
            max_session_ttl=cfg.session_ttl_seconds,
            signature_path=cfg.property_profile_signature_path,
            public_key_path=cfg.property_profile_public_key_path)
    else:
        property_profile = unconfigured_property_profile(
            property_id=cfg.property_id, property_name=cfg.property_name,
            property_timezone=cfg.property_timezone, max_session_ttl=cfg.session_ttl_seconds)
    if (property_profile.property_id != cfg.property_id
            or property_profile.property_name != cfg.property_name
            or property_profile.property_timezone != cfg.property_timezone):
        raise RuntimeError('Configured property identity differs from pinned property profile')
    enabled_request_kinds = frozenset(
        item['request_kind'] for item in property_profile.service_catalog
        if item.get('request_kind') is not None)
    workflows.configure_property_policy(
        languages=frozenset(property_profile.enabled_languages),
        request_kinds=enabled_request_kinds)
    return domain_profile, property_profile, enabled_request_kinds


def _build_service_selector(cfg, embedder):
    """Build the lazy catalog/example index behind embedding-based understanding."""
    if embedder is None or not cfg.semantic_understanding_enabled:
        return None
    from .core.domain_profile import nlu_policy
    policy = nlu_policy().service_selector
    root = getattr(cfg, 'structured_dataset_dir', None)
    path = dataset_path(SERVICE_CATALOG, root)
    try:
        examples = load_command_examples(
            [dataset_path(name, root) for name in (TRAIN_AGENT_VI_GOLD, TRAIN_AGENT_MULTILINGUAL)])
        return ServiceSelector(path, embedder, top_k=int(policy['top_k']),
                               examples=examples, example_k=int(policy['example_k']))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning('service_selector_unavailable path=%s error=%s', path, type(exc).__name__)
        return None


def _build_lifespan(graph_holder, store, workflows, cfg=None):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        import asyncio
        stop = asyncio.Event()

        async def warm_voice():
            # Background: startup and /healthz never wait on model loading.
            from .voice.runtime.adapters import warm_voice_models
            try:
                warmed = await asyncio.to_thread(warm_voice_models, cfg)
                if warmed:
                    LOGGER.info('voice_models_warmed models=%s', ','.join(warmed))
            except Exception as exc:  # warm-up is an optimization only
                LOGGER.warning('voice_model_warmup_failed type=%s', type(exc).__name__)
            if cfg.llm_base_url and cfg.llm_model:
                # Ollama loads a model lazily; the first guest turn would pay
                # several seconds. A 1-token request keeps it resident.
                from .runtime.local_ai import warm_local_slm
                try:
                    await asyncio.to_thread(warm_local_slm, cfg.llm_base_url, cfg.llm_model)
                except Exception as exc:
                    LOGGER.warning('slm_warmup_failed type=%s', type(exc).__name__)
            selector = getattr(_app.state, 'service_selector', None)
            if selector is not None:
                # Embed the catalog and reviewed examples before the first turn.
                try:
                    await asyncio.to_thread(selector.warm)
                except Exception as exc:
                    LOGGER.warning('service_selector_warmup_failed type=%s', type(exc).__name__)
        warmup = asyncio.create_task(warm_voice()) if cfg is not None else None
        async def overdue_worker():
            while not stop.is_set():
                try:
                    workflows.refresh_overdue_requests(int(time.time()))
                except sqlite3.Error:
                    LOGGER.warning('overdue_worker_storage_unavailable')
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
        worker = asyncio.create_task(overdue_worker())
        try:
            yield
        finally:
            stop.set()
            if warmup is not None and not warmup.done():
                warmup.cancel()
            try:
                await worker
            except Exception:
                LOGGER.warning('overdue_worker_shutdown_failed')
            try:
                store.flush_metrics()
            except sqlite3.Error:
                LOGGER.warning("aggregate_metric_flush_unavailable")
            graph = graph_holder["instance"]
            if graph is not None:
                graph.close()
    return lifespan


def _build_fastapi(cfg, graph_holder, store, workflows) -> FastAPI:
    return FastAPI(
        title="Concierge Kiosk Edge API", version="6.6.0",
        docs_url="/docs" if cfg.environment != "production" else None,
        redoc_url=None if cfg.environment == "production" else "/redoc",
        openapi_url=None if cfg.environment == "production" else "/openapi.json",
        lifespan=_build_lifespan(graph_holder, store, workflows, cfg),
    )


def _configure_voice_runtime(app: FastAPI, cfg):
    from .runtime.admission import AudioAdmission
    from .runtime.local_ai import inspect_local_ai
    from .voice.runtime.readiness import incremental_voice_ready

    pcm_ready_at_boot = incremental_voice_ready(
        enabled=cfg.voice_incremental_enabled,
        model_path=cfg.voice_incremental_model_path,
        manifest_path=cfg.voice_incremental_manifest_path,
        require_manifest=cfg.voice_incremental_require_manifest)
    app.state.incremental_voice_ready = pcm_ready_at_boot
    local_ai_status = inspect_local_ai(cfg) if cfg.local_ai_strict_mode else None
    strict_ai_ready_at_boot = bool(local_ai_status and local_ai_status.strict_ready)
    app.state.local_ai_status = local_ai_status

    def pcm_permitted() -> bool:
        if not pcm_ready_at_boot:
            return False
        if cfg.voice_incremental_require_manifest:
            from .core.model_manifest import check_voice_manifest
            return check_voice_manifest(cfg.voice_incremental_model_path,
                                        cfg.voice_incremental_manifest_path)
        return True

    def slm_permitted() -> bool:
        """Gate all model routes, including intent ordering, under strict pinning."""
        if not (cfg.llm_base_url and cfg.llm_model):
            return False
        if not cfg.local_ai_strict_mode:
            return True
        if not strict_ai_ready_at_boot:
            return False
        from .runtime.local_ai import exact_ollama_digest
        if (exact_ollama_digest(cfg.llm_base_url, cfg.llm_model, timeout=cfg.slm_probe_timeout_seconds)
                != cfg.llm_model_digest.removeprefix('sha256:')):
            return False
            from .core.model_manifest import check_model_manifest
        return check_model_manifest(cfg.nli_model_path, cfg.nli_manifest_path)

    audio_admission = AudioAdmission()
    app.state.audio_admission = audio_admission
    voice_recording_ttl = cfg.max_audio_seconds + cfg.stt_timeout_seconds + 10
    voice_finalized_ttl = max(60.0, cfg.stt_timeout_seconds + cfg.tts_timeout_seconds + 20)
    voice_playback_ttl = max(90.0, cfg.tts_timeout_seconds * 4 + 20)
    voice_turns = VoiceTurns(
        recording_ttl_seconds=voice_recording_ttl,
        finalized_ttl_seconds=voice_finalized_ttl,
        playback_ttl_seconds=voice_playback_ttl,
        hard_ttl_seconds=voice_recording_ttl + voice_finalized_ttl + voice_playback_ttl + 30,
        speech_plan=cfg.voice_speech_plan,
        sentence_endings={
            language: tuple(values)
            for language, values in (voice_policy().get('sentence_endings') or {}).items()
        },
        clause_delimiters={
            language: tuple(values)
            for language, values in (voice_policy().get('clause_delimiters') or {}).items()
        })
    app.state.voice_turns = voice_turns
    turn_events = TurnEvents()
    app.state.turn_events = turn_events
    return (audio_admission, voice_turns, turn_events,
            threading.BoundedSemaphore(1), threading.BoundedSemaphore(1),
            pcm_permitted, slm_permitted, strict_ai_ready_at_boot)


def _configure_memory_runtime(app: FastAPI, cfg, store):
    from .core.domain_profile import memory_policy

    memory_cfg = memory_policy()
    conversations = ConversationMemory(
        ttl=cfg.context_ttl_seconds, max_topics=cfg.context_max_topics,
        max_sessions=memory_cfg.max_sessions)
    agent_checkpoints = AgentCheckpointStore(store, cfg.property_id, cfg.context_ttl_seconds)
    agent_memory = SessionSemanticMemoryStore(store, cfg.property_id, cfg.context_ttl_seconds)
    preference_memory = SessionPreferenceMemoryStore(
        store, cfg.property_id, min(cfg.context_ttl_seconds, memory_cfg.preference_ttl_seconds))
    agent_tasks = AgentTaskMemory(
        ttl=min(memory_cfg.task_ttl_seconds, cfg.context_ttl_seconds),
        max_sessions=memory_cfg.max_sessions)
    app.state.conversations = conversations
    app.state.agent_checkpoints = agent_checkpoints
    app.state.agent_memory = agent_memory
    app.state.preference_memory = preference_memory
    app.state.agent_tasks = agent_tasks
    return conversations, agent_checkpoints, agent_memory, preference_memory, agent_tasks


def _build_graph_getter(*, app, cfg, workflows, graph_holder):
    graph_lock = threading.Lock()

    def get_graph():
        with graph_lock:
            if graph_holder["instance"] is None:
                try:
                    from .agent.orchestration.graph import ConciergeGraph
                    graph_holder["instance"] = ConciergeGraph(
                        workflows, cfg.db_path.with_name(cfg.db_path.stem + "-graph.sqlite3"))
                    app.state.graph = graph_holder["instance"]
                except (RuntimeError, OSError, sqlite3.Error, ValueError) as exc:
                    LOGGER.error("langgraph_unavailable type=%s: %s", type(exc).__name__, exc)
                    raise HTTPException(
                        status_code=503,
                        detail=f"Durable agent orchestrator unavailable: {exc}") from exc
            return graph_holder["instance"]
    return get_graph


def _build_request_observers(store):
    def rate(request: Request, category: str, limit: int, interval: int = 60) -> None:
        ip = request.client.host if request.client else "unknown"
        if not store.throttle(f"{category}:{ip}", limit, interval, int(time.time())):
            raise HTTPException(status_code=429, detail="Please retry later")

    def record_metric(metric: str, language: str) -> None:
        try:
            store.metric(metric, language, int(time.time()))
        except sqlite3.Error:
            LOGGER.warning('aggregate_metric_unavailable metric=%s', metric)

    def speech_metric(stage: str, language: str, start: float, outcome: str) -> None:
        record_metric(f"voice.{stage}.{outcome}", language)
        ms = int((time.monotonic() - start) * 1000)
        bucket = next((n for n in (250, 500, 1000, 2000, 5000, 10000, 30000)
                       if ms <= n), "over_30000")
        record_metric(f"voice.{stage}.latency_le_{bucket}ms", language)

    def agent_latency_metric(node: str, elapsed_ms: float, outcome: str) -> None:
        record_metric(f"agent.node.{node}.{outcome}", "system")
        bucket = next((n for n in (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000)
                       if elapsed_ms <= n), "over_5000")
        record_metric(f"agent.node.{node}.latency_le_{bucket}ms", "system")

    def observe_slm(name: str, value: float, language: str) -> None:
        if name == 'slm_ttft_ms':
            try:
                record_metric(latency_bucket('voice.slm_ttft', value), language)
            except ValueError:
                pass
        elif name == 'slm_evidence_composed' and 1 <= value <= 4:
            record_metric('rag.evidence_composition', language)
        elif name == 'slm_claims_repaired' and 1 <= value <= 4:
            record_metric('rag.claim_repair', language)
        elif name == 'slm_tokens_per_sec' and 0 < value <= 1000:
            bound = next((n for n in (1, 2, 5, 10, 20, 40, 80, 160, 320, 640, 1000)
                          if value <= n), 1000)
            record_metric(f'voice.slm_tokens_per_sec.le_{bound}', language)

    return rate, record_metric, speech_metric, agent_latency_metric, observe_slm

def create_app(settings: Settings | None = None, *, embedder=None, reranker=None) -> FastAPI:
    cfg, store, rag_policy, workflows, embedder, reranker = prepare_runtime(settings, embedder, reranker)
    vector_store = None
    vector_store_error = None
    try:
        from .rag.vectorstore import open_vector_store
        vector_store = open_vector_store(
            path=cfg.rag_vector_path, collection=f'{cfg.property_id}-knowledge')
    except (OSError, RuntimeError, ValueError) as exc:
        vector_store_error = type(exc).__name__
        LOGGER.error('dense_vector_index_unavailable type=%s', vector_store_error)
    domain_profile, property_profile, enabled_request_kinds = _load_bound_profiles(cfg, workflows)
    service_selector = _build_service_selector(cfg, embedder)
    workflows.emergency_escalation_seconds = property_profile.emergency.escalation_after_seconds
    workflows.default_kiosk_location = property_profile.emergency.default_kiosk_location
    graph_holder = {"instance": None}
    app = _build_fastapi(cfg, graph_holder, store, workflows)
    app.state.store = store
    app.state.workflows = workflows
    app.state.config = cfg
    app.state.property_profile = property_profile
    app.state.domain_profile = domain_profile
    app.state.vector_store = vector_store
    app.state.vector_store_error = vector_store_error
    app.state.service_selector = service_selector
    app.state.status_tokens = StatusTokenService(cfg.status_token_secret)
    # Suggestions are consent-gated at the route and remain read-only. The
    # engine is enabled so an explicitly opted-in kiosk can use it; without
    # consent it returns no suggestions.
    app.state.proactive_engine = ProactiveEngine(enabled=True)
    (audio_admission, voice_turns, turn_events, stt_semaphore, tts_semaphore,
     pcm_permitted, slm_permitted, strict_ai_ready_at_boot) = _configure_voice_runtime(app, cfg)
    (conversations, agent_checkpoints, agent_memory,
     preference_memory, agent_tasks) = _configure_memory_runtime(app, cfg, store)
    get_graph = _build_graph_getter(
        app=app, cfg=cfg, workflows=workflows, graph_holder=graph_holder)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    install_http_security(app, cfg)

    @app.exception_handler(sqlite3.OperationalError)
    async def storage_error(_request: Request, exc: sqlite3.OperationalError):
        # No implicit retry: a prior business transaction may have committed.
        LOGGER.error('business_storage_unavailable type=%s', type(exc).__name__)
        return JSONResponse({'detail': 'Business storage temporarily unavailable'},
                            status_code=503, headers={'Retry-After': '1'})

    @app.exception_handler(InvalidTransition)
    async def transition_error(_request: Request, exc: InvalidTransition):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(PermissionError)
    async def permission_error(_request: Request, _exc: PermissionError):
        return JSONResponse({"detail": "Not authorized or session expired"}, status_code=403)

    @app.exception_handler(ValueError)
    async def input_error(_request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    rate, record_metric, speech_metric, agent_latency_metric, observe_slm = _build_request_observers(store)

    auth = build_auth_dependencies(cfg, workflows)
    guest_session = auth.guest_session
    staff_read = auth.staff_read
    staff_write = auth.staff_write
    require_agent = auth.require_agent

    from .api.public import register_public_routes
    register_public_routes(
        app, cfg=cfg, store=store, web_dir=WEB_DIR, pcm_permitted=pcm_permitted,
        slm_permitted=slm_permitted, get_graph=get_graph, property_profile=property_profile,
        embedder=embedder, guest_session=guest_session, rate=rate,
        strict_ai_ready_at_boot=strict_ai_ready_at_boot, logger=LOGGER,
        vector_store=vector_store,
    )

    from .application.conversation import build_answer_services, build_conversation_engine
    answer_services = build_answer_services(
        store=store, workflows=workflows, cfg=cfg, conversations=conversations, rag_policy=rag_policy,
        embedder=embedder, reranker=reranker, vector_store=vector_store,
        record_metric=record_metric,
        speech_metric=speech_metric, slm_permitted=slm_permitted,
        audio_admission=audio_admission, observe_slm=observe_slm,
    )
    conversation_engine = build_conversation_engine(
        app=app, cfg=cfg, store=store, workflows=workflows, agent_tasks=agent_tasks,
        conversations=conversations, agent_checkpoints=agent_checkpoints, agent_memory=agent_memory,
        preference_memory=preference_memory, property_profile=property_profile,
        enabled_request_kinds=enabled_request_kinds, get_graph=get_graph,
        record_metric=record_metric, turn_events=turn_events,
        audio_admission=audio_admission, slm_permitted=slm_permitted,
        answers=answer_services, logger=LOGGER, service_selector=service_selector,
    )
    answer = conversation_engine.answer
    ensure_active_context_session = answer_services.ensure_active_context_session
    service_actions = conversation_engine.service_actions

    turn_finalizer = TurnFinalizer(
        conversations=conversations, agent_checkpoints=agent_checkpoints,
        agent_memory=agent_memory, preference_memory=preference_memory,
        ensure_session=ensure_active_context_session, logger=LOGGER,
    )
    finalize_answer = turn_finalizer.finalize
    finalize_service_turn = service_actions.finalize_service_turn

    workflow_service = WorkflowApplicationService(
        workflows=workflows, store=store, property_id=cfg.property_id,
        get_graph=get_graph, logger=LOGGER,
    )
    prepare_authorized_proposal = workflow_service.prepare
    confirm_authorized_proposal = workflow_service.confirm

    register_guest_routes(
        app, cfg=cfg, workflows=workflows, store=store,
        voice_turns=voice_turns, turn_events=turn_events, audio_admission=audio_admission,
        conversations=conversations, agent_tasks=agent_tasks, rate=rate,
        guest_session=guest_session, answer=answer, finalize_answer=finalize_answer,
        finalize_service_turn=finalize_service_turn,
        get_graph=get_graph,
        prepare_authorized_proposal=prepare_authorized_proposal,
        confirm_authorized_proposal=confirm_authorized_proposal,
        record_metric=record_metric, logger=LOGGER,
        status_tokens=app.state.status_tokens, web_dir=WEB_DIR,
    )

    register_staff_routes(
        app, workflows=workflows, store=store, cfg=cfg, get_graph=get_graph,
        staff_read=staff_read, staff_write=staff_write,
        record_metric=record_metric, logger=LOGGER,
    )

    @app.post("/api/telemetry")
    def client_telemetry(body: ClientTelemetry, request: Request,
                         session: str = Depends(guest_session)):
        # Authentication/CSRF and rate limiting are enforced just like other
        # kiosk writes; no session ID, IP, text or audio is retained with data.
        rate(request, f"telemetry:{session}", 120)
        accepted = store.record_client_latency(body.event_id, body.language,
                                               body.stage, body.duration_ms,
                                               int(time.time()))
        return {"accepted": accepted}

    speech_service = SpeechService()
    speech_service.register(
        app, cfg, store=store, voice_turns=voice_turns, audio_admission=audio_admission,
        turn_events=turn_events,
        stt_semaphore=stt_semaphore, tts_semaphore=tts_semaphore,
        guest_session=guest_session, rate=rate, speech_metric=speech_metric,
        session_for=workflows.session_for,
        transcribe_fn=lambda config, data, language: transcribe(config, data, language),
        transcribe_detected_fn=lambda config, data, language: transcribe_detected(config, data, language),
        synthesize_fn=lambda config, text, language: synthesize(config, text, language),
        synthesize_cancellable_fn=lambda config, text, language, cancelled:
            synthesize_cancellable(config, text, language, cancelled),
        validate_audio_fn=lambda data, mime, **limits: validate_audio(data, mime, **limits),
        voice_policy=property_profile.voice,
        compatibility_contract_metric=lambda: record_metric('voice.compatibility_contract_used', 'none'),
        pipecat_dependencies={
            'voice_turns': voice_turns,
            'turn_events': turn_events,
            'store': store,
            'session_for': workflows.session_for,
            'transcribe_fn': lambda config, data, language: transcribe_detected(config, data, language),
            'synthesize_fn': lambda config, text, language: synthesize(config, text, language),
            'answer': answer,
            'finalize_answer': finalize_answer,
            'finalize_service_turn': finalize_service_turn,
        },
    )

    register_internal_agent_routes(
        app, cfg=cfg, workflows=workflows, require_agent=require_agent,
        answer=answer, finalize_answer=finalize_answer, conversations=conversations,
        prepare_authorized_proposal=prepare_authorized_proposal,
    )

    return app


class LazyApplication:
    """ASGI proxy that constructs the FastAPI application on first ASGI use.

    Importing ``concierge_kiosk.main`` is side-effect free: it does not create or
    migrate SQLite, load model manifests, or bind property data. Uvicorn may keep
    using ``concierge_kiosk.main:app`` without requiring a deployment rewrite.
    """

    def __init__(self, factory):
        self._factory = factory
        self._instance = None
        self._lock = threading.Lock()

    def instance(self) -> FastAPI:
        app = self._instance
        if app is not None:
            return app
        with self._lock:
            if self._instance is None:
                self._instance = self._factory()
            return self._instance

    async def __call__(self, scope, receive, send):
        await self.instance()(scope, receive, send)


app = LazyApplication(create_app)
