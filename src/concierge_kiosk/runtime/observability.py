"""Optional metadata-only Langfuse v4 boundary. Never grants business authority.

No SDK import, sender, or socket exists in disabled mode. Explicit parent IDs
avoid attaching guest turns to unrelated global OpenTelemetry instrumentation.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import hashlib
import hmac
import inspect
import json
import math
import logging
import secrets
import base64
from threading import Lock, Thread
import time
from urllib.parse import urlsplit


SPAN_NAMES = frozenset({
    'guest_turn', 'business_workflow', 'understanding', 'fast_router', 'grounded_service',
    'candidate_selection', 'qwen_nlu', 'command_validation', 'semantic_authorization',
    'model_proposed', 'server_validated', 'semantically_authorized',
    'agent_execution', 'route_projection', 'memory_resolution', 'memory_lookup',
    'langgraph_execution', 'verify_goal', 'plan_next_action', 'execute_tool',
    'tool_execution', 'retrieval', 'citation_validation', 'final_response',
    'model_call', 'proposal_review', 'prepare', 'confirmation',
    'verified_write_receipt', 'staff_review', 'status_transition',
    'cancellation', 'modification', 'emergency_alert',
})
# Free text is never a telemetry value. Domain identifiers are checked against
# the loaded registry below; protocol outcomes use a closed vocabulary.
ENUMS = {
    'stage': {'model_proposed', 'server_validated', 'semantically_authorized'},
    'outcome': {'accepted', 'rejected', 'success', 'failure', 'none', 'expired', 'valid', 'missing'},
    'reason_code': {'unsupported_semantics', 'structural_validation', 'none', 'expired', 'source_revoked',
                    'not_plain', 'competing_service'},
    'evidence_category': {'domain_policy', 'not_checked'},
    'invocation_type': {'NLU', 'planner', 'reference', 'generation', 'goal', 'other'},
    'status': {'success', 'timeout', 'unavailable', 'invalid_output', 'cancelled', 'accepted',
               'failure', 'approved', 'paused',
               'rejected', 'completed', 'safe_fallback', 'needs_user_input', 'confirmation_required',
               'action_ready', 'denied', 'executed', 'draft', 'prepared', 'pending_staff',
               'awaiting_confirmation', 'confirmed', 'expired',
               'queued', 'acknowledged', 'in_progress', 'fulfilled', 'cancelled', 'rejected_by_validation',
               'partially_accepted', 'unsupported_semantics', 'no_response', 'malformed_output',
               'turn_budget_expired', 'not_run', 'ok', 'error', 'busy', 'deferred'},
    'route': {'knowledge', 'navigation', 'planning', 'service', 'handoff', 'multi_task',
              'confirmation', 'request_change', 'request_status', 'check_schedule', 'find_place',
              'nlu_failure', 'clarification', 'language', 'preference', 'greeting',
                'emergency', 'emergency_check', 'done', 'plan', 'execute', 'verify', 'out_of_scope'},
    'retrieval_mode': {'lexical', 'dense', 'hybrid', 'structured', 'not_used',
                       'conflict_abstention', 'rerank_unavailable', 'fallback_deadline',
                       'cross_language_lexical', 'cross_language_dense', 'cross_language_hybrid'},
    'rerank_status': {'not_run', 'not_configured', 'ok', 'error', 'busy', 'timeout'},
    'evidence_status': {'verified', 'unsupported', 'ambiguous', 'conflicting',
                        'VERIFIED', 'NO_VERIFIED_EVIDENCE', 'POLICY_BLOCKED'},
    'authority_outcome': {'allow', 'deny', 'confirm'},
    'action_type': {'prepare', 'confirm', 'cancel', 'modify', 'review', 'transition', 'emergency'},
    'failure_class': {'timeout', 'unavailable', 'cancelled', 'invalid_output', 'internal_error',
                      'MODEL_TIMEOUT', 'MODEL_UNAVAILABLE', 'MODEL_NOT_READY', 'MODEL_BUSY',
                      'AMBIGUOUS_INTENT', 'NO_VERIFIED_EVIDENCE', 'MODEL_INVALID_OUTPUT',
                      'NLU_TIMEOUT', 'INVALID_MODEL_OUTPUT', 'NLU_UNAVAILABLE', 'UNSUPPORTED_INTENT',
                      'authority_denied', 'invalid_transition',
                      'model_timeout', 'external_dependency_unavailable', 'tool_input_or_data_unavailable',
                      'internal_tool_error', 'capability_unavailable', 'alternative_read_may_exist'},
    'reference_resolution': {'none', 'accepted', 'rejected', 'ambiguous'},
    'answerability': {'answerable', 'abstained'},
    'abstention_reason': {'conflicting', 'ambiguous', 'unsupported', 'deadline'},
    'context_invalidation_reason': {'none', 'ttl_expired', 'source_revoked', 'missing'},
    'privacy_policy': {'metadata_only'},
    'semantic_policy': {'WP13'},
    'schema_version': {'WP11_compact'},
}
BOOL_KEYS = frozenset({'proposal_allowed', 'confirmation_required', 'confirmation_verified',
    'idempotent_replay', 'verified', 'anchor_existed', 'anchor_accepted',
    'session_ownership_verified', 'ttl_valid', 'pending_task_continuation', 'rrf_used'})
NUMBER_KEYS = frozenset({'latency_ms', 'prompt_tokens', 'completion_tokens', 'prompt_eval_ms',
    'generation_ms', 'load_ms', 'total_model_ms', 'command_count', 'command_index', 'policy_version',
    'schema_version', 'candidate_count', 'top_k', 'citation_count', 'business_writes',
    'service_proposal_count', 'source_count', 'rerank_ms'})
HEX_KEYS = {'correlation_id': 32, 'session_pseudonym': 64, 'proposal_link': 64, 'request_link': 64,
            'semantic_policy_digest': 64, 'model_digest': 64}
_CURRENT: ContextVar['Scope | None'] = ContextVar('observability_scope', default=None)


def sanitize(metadata: dict | None, *, model_names=()) -> dict:
    """Allowlist scalar telemetry. Nested payloads and unknown keys are dropped."""
    clean = {}
    if not isinstance(metadata, dict):
        return clean
    for key, value in metadata.items():
        if key in BOOL_KEYS and type(value) is bool:
            clean[key] = value
        elif key == 'model_name' and isinstance(value, str) and value in model_names:
            clean[key] = value
        elif key in NUMBER_KEYS and type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e12:
            clean[key] = value
        elif key in ENUMS and isinstance(value, str) and value in ENUMS[key]:
            clean[key] = value
        elif key in HEX_KEYS and isinstance(value, str) and len(value) == HEX_KEYS[key] and all(c in '0123456789abcdef' for c in value):
            clean[key] = value
        elif key == 'source_links' and isinstance(value, (list, tuple)):
            clean[key] = [v for v in value[:10] if isinstance(v, str) and len(v) == 64 and all(c in '0123456789abcdef' for c in v)]
        elif key in {'goal', 'command_type', 'slot_names', 'capability'}:
            from concierge_kiosk.agent.understanding.commands import COMMAND_TYPES
            from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS, accepted_slots
            from concierge_kiosk.core.domain_profile import preference_policy
            allowed = (set(SERVICE_DEFINITIONS) if key == 'goal' else set(COMMAND_TYPES)
                       if key == 'command_type' else
                       {'knowledge', 'navigation', 'planning', 'service_action', 'manage_request',
                        'request_status', 'check_schedule', 'find_place', 'handoff_staff', 'guest_context'}
                       if key == 'capability' else
                       {s for code in SERVICE_DEFINITIONS for s in accepted_slots(code)} | set(preference_policy().fields))
            if key == 'slot_names' and isinstance(value, (list, tuple)):
                clean[key] = [v for v in value[:16] if isinstance(v, str) and v in allowed]
            elif isinstance(value, str) and value in allowed:
                clean[key] = value
    return clean


def should_export_span(span) -> bool:
    """Auto-captured names, events, links and exception descriptions cannot leak."""
    attrs = span.attributes or {}
    return (span.name in SPAN_NAMES
            and attrs.get('langfuse.observation.metadata.privacy_policy') == 'metadata_only'
            and not span.events and not span.links
            and not getattr(span.status, 'description', None))


def mask_otel_spans(*, params, model_names=(), tracing_environment='development'):
    """SDK v4 export-stage defense, including arbitrary/nested OTEL attributes."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch
    patches = {}
    prefix = 'langfuse.observation.metadata.'
    for identifier, span in params.spans.items():
        attrs = span.attributes
        meta = {}
        for key, value in attrs.items():
            if key.startswith(prefix):
                name = key[len(prefix):]
                # SDK v4 preserves ints/bools but JSON-serializes floats.
                # Decode only known numeric/list fields before typed allowlist;
                # arbitrary strings/nested state are still rejected.
                if name in NUMBER_KEYS | {'slot_names', 'source_links'} and isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        pass
                meta[name] = value
        kept = {prefix + key: json.dumps(value) if isinstance(value, list) else value
                for key, value in sanitize(meta, model_names=model_names).items()}
        if attrs.get('langfuse.observation.type') in {'span', 'generation'}:
            kept['langfuse.observation.type'] = attrs['langfuse.observation.type']
        if span.parent_span_id is None:
            kept['langfuse.internal.is_app_root'] = True
            kept['langfuse.internal.as_root'] = True
        if tracing_environment in {'development', 'test', 'staging', 'production'}:
            kept['langfuse.environment'] = tracing_environment
        model_name = attrs.get('langfuse.observation.model.name')
        if model_name in model_names:
            kept['langfuse.observation.model.name'] = model_name
        session = attrs.get('session.id')
        if isinstance(session, str) and len(session) == 64 and all(c in '0123456789abcdef' for c in session):
            kept['session.id'] = session
        # Usage is provider-reported, and has already passed bounded numeric guards.
        usage = attrs.get('langfuse.observation.usage_details')
        if isinstance(usage, str):
            try:
                parsed = json.loads(usage)
                if isinstance(parsed, dict):
                    safe = {k: v for k, v in parsed.items() if k in {'input', 'output'} and type(v) is int and 0 <= v <= 1000000}
                    kept['langfuse.observation.usage_details'] = json.dumps(safe)
            except ValueError:
                pass
        patches[identifier] = OtelSpanPatch(delete_attributes=tuple(attrs), set_attributes=kept)
    return MaskOtelSpansResult(span_patches=patches)


class SDKBackend:
    """One SDK-owned exporter on an isolated provider; no LangGraph callback."""
    def __init__(self, cfg, *, span_exporter=None):
        from langfuse import Langfuse
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace import ReadableSpan
        from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
        from opentelemetry.sdk.util.instrumentation import InstrumentationScope
        # LANGFUSE_DEBUG can override debug=False inside the SDK. Its startup
        # record includes a public key; OTLP errors may include response bodies.
        # Keep only severity, never third-party free-text diagnostics.
        class MetadataOnlyLogFilter(logging.Filter):
            def filter(self, record):
                record.msg = 'Observability exporter diagnostic (details withheld by privacy policy)'
                record.args = ()
                record.exc_info = record.exc_text = record.stack_info = None
                return record.levelno >= logging.WARNING
        for logger_name in ('langfuse', 'opentelemetry.exporter.otlp.proto.http.trace_exporter'):
            logger = logging.getLogger(logger_name)
            if not any(getattr(f, '_concierge_privacy_filter', False) for f in logger.filters):
                privacy_filter = MetadataOnlyLogFilter()
                privacy_filter._concierge_privacy_filter = True
                logger.addFilter(privacy_filter)
        if span_exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            auth = base64.b64encode((cfg.langfuse_public_key.get_secret_value() + ':' +
                                     cfg.langfuse_secret_key.get_secret_value()).encode()).decode()
            span_exporter = OTLPSpanExporter(
                endpoint=cfg.langfuse_base_url.rstrip('/') + '/api/public/otel/v1/traces',
                headers={'Authorization': 'Basic ' + auth, 'x-langfuse-ingestion-version': '4'}, timeout=1)

        class PrivateExporter(SpanExporter):
            # The SDK export hook cannot edit scope/resource attributes. Strip
            # SDK project public_key baggage here; this wraps the SINGLE sink.
            def export(self, spans):
                try:
                    safe = [ReadableSpan(
                        name=s.name, context=s.context, parent=s.parent, resource=Resource({}),
                        attributes=s.attributes, events=(), links=(), status=s.status,
                        start_time=s.start_time, end_time=s.end_time,
                        instrumentation_scope=InstrumentationScope('concierge.observability')) for s in spans]
                    return span_exporter.export(safe)
                except Exception:
                    return SpanExportResult.FAILURE

            def shutdown(self):
                span_exporter.shutdown()
        # Sampling is decided once at the turn boundary, including mock backends.
        self.provider = TracerProvider(resource=Resource({}), shutdown_on_exit=False)
        self.model_names = tuple(filter(None, (cfg.llm_model, cfg.llm_fallback_model, cfg.semantic_verifier_model)))
        self.client = Langfuse(
            public_key=cfg.langfuse_public_key.get_secret_value(),
            secret_key=cfg.langfuse_secret_key.get_secret_value(),
            base_url=cfg.langfuse_base_url, environment=cfg.langfuse_tracing_environment,
            timeout=1, debug=False, sample_rate=1, flush_at=64, flush_interval=1,
            tracer_provider=self.provider,
            mask_otel_spans=lambda *, params: mask_otel_spans(params=params, model_names=self.model_names,
                tracing_environment=cfg.langfuse_tracing_environment),
            should_export_span=should_export_span, span_exporter=PrivateExporter(),
        )
        # v4 caches resources by public key. Never inherit another client's
        # provider/export policy, even if that client has the same credentials.
        # This guarded internal check is tied to the exact optional SDK pin.
        if getattr(getattr(self.client, '_resources', None), 'tracer_provider', None) is not self.provider:
            self.provider.shutdown()
            span_exporter.shutdown()
            raise RuntimeError('Langfuse provider isolation unavailable')
        self.model_name = cfg.llm_model

    def start(self, name, trace_id, parent_id, metadata):
        from langfuse import propagate_attributes
        from opentelemetry import context as context_api
        from opentelemetry.context import Context
        # Session baggage contains only a keyed pseudonym, never a token.
        token = context_api.attach(Context())
        try:
            with propagate_attributes(session_id=metadata.get('session_pseudonym')):
                return self.client.start_observation(
                    name=name, as_type='generation' if name == 'model_call' else 'span',
                    # No remote parent for a root: SDK/OTEL generates a real root ID.
                    trace_context={'trace_id': trace_id, 'parent_span_id': parent_id} if parent_id else None,
                    metadata=metadata)
        finally:
            context_api.detach(token)

    def finish(self, handle, metadata):
        handle.update(metadata=metadata)
        if metadata.get('model_name') in self.model_names:
            handle.update(model=metadata['model_name'])
        if metadata.get('prompt_tokens') is not None or metadata.get('completion_tokens') is not None:
            handle.update(usage_details={key: metadata[src] for key, src in
                (('input', 'prompt_tokens'), ('output', 'completion_tokens')) if src in metadata})
        handle.end()

    def score(self, **score):
        self.client.create_score(**score)

    def shutdown(self):
        self.client.shutdown()
        self.provider.shutdown()


@dataclass(frozen=True)
class Scope:
    owner: 'Observability'
    trace_id: str
    correlation_id: str
    session_pseudonym: str | None
    parent_id: str | None = None


class Observation:
    def __init__(self):
        self.metadata = {}

    def update(self, **metadata):
        try:
            scope = _CURRENT.get()
            self.metadata.update(sanitize(metadata, model_names=scope.owner.model_names if scope else ()))
        except Exception:
            pass


class Observability:
    def __init__(self, backend=None, *, sample_rate=1.0, pseudonym_key: bytes | None = None,
                 model_names=(), model_digest=''):
        self.backend = backend
        self.sample_rate = sample_rate
        self._key = pseudonym_key or secrets.token_bytes(32)
        self.closed = False
        self._shutdown_thread = None
        self._shutdown_lock = Lock()
        self.model_names = tuple(model_names)
        self.model_digest = model_digest.removeprefix('sha256:')

    @classmethod
    def configured(cls, cfg, *, backend_factory=SDKBackend):
        # Fail closed on optional configuration errors; never reject app startup.
        disabled = cls()
        try:
            if not cfg.langfuse_enabled or cfg.langfuse_sample_rate <= 0:
                return disabled
            url = urlsplit(cfg.langfuse_base_url)
            if (not cfg.langfuse_public_key.get_secret_value() or not cfg.langfuse_secret_key.get_secret_value()
                    or url.scheme not in {'http', 'https'} or not url.hostname
                    or url.username or url.password or url.query or url.fragment):
                return disabled
            key = cfg.langfuse_pseudonym_key.get_secret_value()
            if key and len(key) < 32:
                return disabled
            return cls(backend_factory(cfg), sample_rate=cfg.langfuse_sample_rate,
                       pseudonym_key=key.encode() if key else None,
                       model_names=tuple(filter(None, (cfg.llm_model, cfg.llm_fallback_model, cfg.semantic_verifier_model))),
                       model_digest=cfg.llm_model_digest)
        except Exception:
            return disabled

    def pseudonym(self, category: str, value: str) -> str:
        return hmac.new(self._key, (category + ':' + value).encode(), hashlib.sha256).hexdigest()

    @contextmanager
    def turn(self, session: str | None, *, name='guest_turn'):
        prior = _CURRENT.get()
        if session is not None and prior is not None and prior.owner is self and prior.session_pseudonym == self.pseudonym('session', session):
            yield prior
            return
        scope = None
        try:
            if self.backend is not None and not self.closed and secrets.randbelow(1000000) < self.sample_rate * 1000000:
                scope = Scope(self, '', secrets.token_hex(16), self.pseudonym('session', session) if session is not None else None)
        except Exception:
            pass
        token = _CURRENT.set(scope)
        try:
            with observation(name):
                yield _CURRENT.get()
        finally:
            _CURRENT.reset(token)

    def shutdown(self, timeout=1.5) -> bool:
        self.closed = True
        if self.backend is None:
            return True
        # SDK flush/joins run away from the event loop, bounded at the app boundary.
        # Only one daemon helper is created, once, at shutdown.
        def stop():
            try:
                self.backend.shutdown()
            except Exception:
                pass
        with self._shutdown_lock:
            if self._shutdown_thread is None:
                self._shutdown_thread = Thread(target=stop, name='observability-shutdown', daemon=True)
                self._shutdown_thread.start()
            thread = self._shutdown_thread
        thread.join(max(0, min(timeout, 5)))
        return not thread.is_alive()


@contextmanager
def observation(name: str, **metadata):
    current = _CURRENT.get()
    item = Observation()
    if current is None or current.owner.closed or name not in SPAN_NAMES:
        yield item
        return
    handle = None
    token = None
    started = time.perf_counter()
    item_token = _OBSERVATION.set(item)
    try:
        item.update(privacy_policy='metadata_only', correlation_id=current.correlation_id,
                    session_pseudonym=current.session_pseudonym, **metadata)
        handle = current.owner.backend.start(name, current.trace_id, current.parent_id, item.metadata)
        token = _CURRENT.set(Scope(current.owner, handle.trace_id, current.correlation_id,
                                   current.session_pseudonym, handle.id))
    except Exception:
        # A broken parent suppresses its subtree instead of exporting orphan roots.
        token = _CURRENT.set(None)
    try:
        yield item
    except BaseException as exc:
        if 'failure_class' not in item.metadata:
            item.update(failure_class='timeout' if isinstance(exc, TimeoutError) else
                        'cancelled' if isinstance(exc, (InterruptedError, GeneratorExit)) or type(exc).__name__ == 'CancelledError'
                        else 'internal_error')
        item.update(status='failure')
        raise
    finally:
        _OBSERVATION.reset(item_token)
        if token is not None:
            _CURRENT.reset(token)
        if handle is not None:
            try:
                item.update(latency_ms=(time.perf_counter() - started) * 1000)
                current.owner.backend.finish(handle, item.metadata)
            except Exception:
                pass


def current_scope():
    return _CURRENT.get()


_OBSERVATION: ContextVar[Observation | None] = ContextVar('current_observation', default=None)


def update_current(**metadata):
    item = _OBSERVATION.get()
    if item is not None:
        item.update(**metadata)


def memory_lookup(session, current, now):
    scope = _CURRENT.get()
    if scope is None:
        return
    owned = scope.session_pseudonym == scope.owner.pseudonym('session', session)
    event('memory_lookup', anchor_existed=bool(owned and current and current.latest_anchors),
          session_ownership_verified=owned and current is not None,
          ttl_valid=bool(owned and current is not None and current.deadline > now),
          context_invalidation_reason='ttl_expired' if owned and current is not None and current.deadline <= now else 'none')


def event(name, **metadata):
    if _CURRENT.get() is not None:
        with observation(name, **metadata):
            pass


def observed(name, *, project=None):
    """Instrument a real executed boundary; never serialize args or return objects."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if _CURRENT.get() is None:
                return function(*args, **kwargs)
            with observation(name) as span:
                result = function(*args, **kwargs)
                if project is not None:
                    try:
                        span.update(**project(result))
                    except Exception:
                        pass
                return result
        return wrapped
    return decorate


def turn_observed(owner, *, session_field='session', name='guest_turn'):
    def decorate(function):
        signature = inspect.signature(function, eval_str=True)
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                bound = signature.bind(*args, **kwargs).arguments
                telemetry = owner(bound)
                session = bound.get(session_field, '')
            except Exception:
                return function(*args, **kwargs)
            if telemetry is None or telemetry.backend is None:
                return function(*args, **kwargs)
            prior = _CURRENT.get()
            nested = prior is not None and prior.owner is telemetry and prior.session_pseudonym == telemetry.pseudonym('session', session)
            with telemetry.turn(session, name=name) as scope:
                result = function(*args, **kwargs)
                if name == 'guest_turn' and not nested:
                    try:
                        final_response(result)
                        if scope is not None and isinstance(result, dict):
                            result = {**result, 'observability': {
                                'trace_id': scope.trace_id, 'correlation_id': scope.correlation_id}}
                    except Exception:
                        pass
                return result
        # FastAPI resolves annotations in the wrapper module.
        wrapped.__signature__ = signature
        return wrapped
    return decorate


def final_response(result):
    if not isinstance(result, dict):
        return
    action = result.get('agent_action') or {}
    update_current(route=result.get('tool_route'), failure_class=result.get('failure_class'),
                   service_proposal_count=int(bool(result.get('suggested_action'))),
                   business_writes=action.get('business_writes', 0))
    event('final_response', route=result.get('tool_route'), failure_class=result.get('failure_class'),
          service_proposal_count=int(bool(result.get('suggested_action'))),
          business_writes=action.get('business_writes', 0),
          citation_count=len(result.get('citations') or []))


def command_event(stage, command, *, outcome='accepted', reason='none', index=0):
    if _CURRENT.get() is None:
        return
    try:
        from concierge_kiosk.core.domain_profile import get_domain_profile
        profile = get_domain_profile()
        event(stage, stage=stage, command_type=command.type, goal=command.goal,
              slot_names=[getattr(slot, 'name', None) for slot in command.slots] +
                         ([command.field] if command.field else []), command_index=index,
              outcome=outcome, reason_code=reason, semantic_policy='WP13',
              semantic_policy_digest=profile.sha256,
              evidence_category='domain_policy' if stage == 'semantically_authorized' else 'not_checked',
              proposal_allowed=(outcome == 'accepted') if stage == 'semantically_authorized' else False)
    except Exception:
        pass


def semantic_gate_metadata(command):
    if _CURRENT.get() is None:
        return
    try:
        from concierge_kiosk.core.domain_profile import get_domain_profile
        update_current(command_type=getattr(command, 'type', None), goal=getattr(command, 'goal', None),
                       semantic_policy='WP13', semantic_policy_digest=get_domain_profile().sha256)
    except Exception:
        pass


def provider_metadata(event_data):
    """Read only provider counters, never content/errors/headers. No new reads."""
    if _CURRENT.get() is None or not isinstance(event_data, dict):
        return
    metadata = {}
    for source, target in [('prompt_eval_count', 'prompt_tokens'), ('eval_count', 'completion_tokens')]:
        value = event_data.get(source)
        if type(value) is int and 0 <= value <= 1000000:
            metadata[target] = value
    for source, target in [('load_duration', 'load_ms'), ('prompt_eval_duration', 'prompt_eval_ms'),
                           ('eval_duration', 'generation_ms'), ('total_duration', 'total_model_ms')]:
        value = event_data.get(source)
        if type(value) is int and 0 <= value <= 1e15:
            metadata[target] = value / 1e6
    # Update the current model observation through a scoped collector.
    collector = _MODEL.get()
    if collector is not None:
        collector.update(**metadata)


_MODEL: ContextVar[Observation | None] = ContextVar('model_observation', default=None)
_INVOCATION: ContextVar[str] = ContextVar('model_invocation', default='other')


@contextmanager
def invocation(kind):
    token = _INVOCATION.set(kind)
    try:
        yield
    finally:
        _INVOCATION.reset(token)


def invoked(kind):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if _CURRENT.get() is None:
                return function(*args, **kwargs)
            with invocation(kind):
                return function(*args, **kwargs)
        return wrapped
    return decorate


@contextmanager
def model_observation():
    with observation('model_call', invocation_type=_INVOCATION.get()) as span:
        token = _MODEL.set(span)
        try:
            yield span
        finally:
            _MODEL.reset(token)


def model_failure(reason):
    collector = _MODEL.get()
    if collector is not None:
        collector.update(status=reason if reason in {'timeout', 'cancelled', 'unavailable'} else 'invalid_output',
                         failure_class=reason if reason in {'timeout', 'cancelled', 'unavailable'} else 'invalid_output')


def observed_model(function):
    @wraps(function)
    def wrapped(base_url, payload, timeout, cancel):
        if _CURRENT.get() is None:
            return function(base_url, payload, timeout, cancel)
        with model_observation() as span:
            scope = _CURRENT.get()
            if _INVOCATION.get() == 'NLU':
                span.update(schema_version='WP11_compact')
            if isinstance(payload, dict):
                span.update(model_name=payload.get('model'),
                            model_digest=scope.owner.model_digest if scope and scope.owner.model_names
                            and payload.get('model') == scope.owner.model_names[0] else None)
            result = function(base_url, payload, timeout, cancel)
            if 'status' not in span.metadata:
                span.update(status='success' if result is not None else 'invalid_output')
            return result
    return wrapped


def observed_generation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _CURRENT.get() is None:
            return function(*args, **kwargs)
        with invocation('generation'), model_observation() as span:
            span.update(model_name=kwargs.get('model'))
            result = function(*args, **kwargs)
            span.update(status='success' if result is not None else 'invalid_output')
            return result
    return wrapped


def retrieval_projection(result):
    scope = _CURRENT.get()
    return {'retrieval_mode': result.mode, 'source_count': len(result.sources),
            'answerability': 'answerable' if result.sources else 'abstained',
            'abstention_reason': ('deadline' if result.mode == 'fallback_deadline' else
                                  result.evidence_quality) if not result.sources else None,
            'rerank_status': result.rerank_status, 'rerank_ms': result.rerank_ms,
            'evidence_status': result.evidence_quality,
            'source_links': [scope.owner.pseudonym('source', source['source_id'])
                             for source in result.sources[:10] if scope and isinstance(source.get('source_id'), str)]}


def citation_projection(result):
    return {'citation_count': len(result.citations), 'source_count': len(result.sources),
            'verified': bool(result.citations)}


def tool_projection(result):
    meta, raw = result
    action = raw.get('agent_action') or {}
    return {'capability': meta.get('capability'), 'status': meta.get('status'),
            'verified': meta.get('verified'), 'failure_class': meta.get('failure_class'),
            'authority_outcome': meta.get('authority_outcome'),
            'business_writes': action.get('business_writes', 0),
            'confirmation_required': action.get('status') == 'confirmation_required'}


def business_observed(name, *, session_field='session_id', action_type=None):
    """Observe the authoritative workflow, after its existing transaction returns.

    The receipt describes service writes, not proposal/audit/checkpoint SQL rows.
    No lookups or ownership checks are added on behalf of telemetry.
    """
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def wrapped(*args, **kwargs):
            owner = getattr(args[0], 'observability', None)
            if owner is None or owner.backend is None:
                return function(*args, **kwargs)
            bound = signature.bind(*args, **kwargs).arguments
            session = bound.get(session_field)
            # Staff traces use no guest-session association unless already present.
            with owner.turn(session, name='business_workflow'):
                with observation(name, action_type=action_type) as span:
                    proposal_id = bound.get('proposal_id')
                    request_id = bound.get('request_id')
                    try:
                        if proposal_id:
                            span.update(proposal_link=owner.pseudonym('proposal', proposal_id))
                        if request_id:
                            span.update(request_link=owner.pseudonym('request', request_id))
                    except Exception:
                        pass
                    try:
                        result = function(*args, **kwargs)
                    except Exception as exc:
                        if isinstance(exc, PermissionError) or type(exc).__name__ == 'InvalidTransition':
                            span.update(authority_outcome='deny', failure_class='authority_denied'
                                        if isinstance(exc, PermissionError) else 'invalid_transition')
                        raise
                    try:
                        if isinstance(result, dict):
                            span.update(status=result.get('status'), idempotent_replay=result.get('idempotent_replay', False))
                            if name in {'prepare', 'modification'} and result.get('id'):
                                span.update(proposal_link=owner.pseudonym('proposal', result['id']),
                                            confirmation_required=True, business_writes=0)
                            if name == 'confirmation':
                                replay = result.get('idempotent_replay', False)
                                writes = int(bool(result.get('id')) and not replay and bound.get('confirmed') is True)
                                span.update(confirmation_required=True, confirmation_verified=bound.get('confirmed') is True,
                                            business_writes=writes, authority_outcome='allow')
                                event('verified_write_receipt', business_writes=writes,
                                      idempotent_replay=bool(replay), verified=True,
                                      proposal_link=owner.pseudonym('proposal', proposal_id),
                                      request_link=owner.pseudonym('request', result['id']) if result.get('id') else None)
                    except Exception:
                        pass
                    return result
        return wrapped
    return decorate
