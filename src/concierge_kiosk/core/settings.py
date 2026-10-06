"""One property per edge appliance. No tenant or branch comes from a guest request."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Mapping

from pydantic import AliasChoices, Field, PrivateAttr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Keep every Ollama chat call on the same KV-cache shape. Changing num_ctx
# between calls makes Ollama reload/reallocate the model context and can add
# several seconds of latency per turn on edge hardware.
SLM_NUM_CTX = 4096


class _BootstrapSettings(BaseSettings):
    """Small environment-only source used before the pinned profile is loaded."""

    model_config = SettingsConfigDict(
        env_prefix="CONCIERGE_", env_file=None, extra="ignore",
        populate_by_name=True,
    )

    environment: str = Field(
        "production",
        validation_alias=AliasChoices("CONCIERGE_ENV", "CONCIERGE_ENVIRONMENT"),
    )
    session_ttl_seconds: int = 1200
    runtime_profile_path: str = ""
    runtime_profile_sha256: str = ""
    property_profile_path: str = ""
    property_profile_sha256: str = ""
    property_profile_signature_path: str = ""
    property_profile_public_key_path: str = ""
    property_id: str = "UNCONFIGURED"
    property_name: str = "Property not configured"
    property_timezone: str = "UTC"
    embedding_model_path: str | None = None
    embedding_manifest_path: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONCIERGE_", env_file=None,
        env_nested_delimiter="__", extra="ignore",
        populate_by_name=True, frozen=True,
    )

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings,
        dotenv_settings, file_secret_settings,
    ):
        # Runtime profile values are supplied through init_settings. Put the
        # process environment first so explicit CONCIERGE_* overrides win over
        # the profile, while callers can still construct Settings directly.
        return env_settings, init_settings, dotenv_settings, file_secret_settings

    _profile_override_keys: frozenset[str] = PrivateAttr(default_factory=frozenset)

    db_path: Path = Path("./data/concierge.sqlite3")
    property_id: str = "UNCONFIGURED"
    property_name: str = "Property not configured"
    property_timezone: str = "UTC"
    environment: str = Field(
        "development",
        validation_alias=AliasChoices("CONCIERGE_ENV", "CONCIERGE_ENVIRONMENT"),
    )
    public_origin: str = "http://localhost:8000"
    staff_origin: str = ""
    staff_token: str = ""
    agent_token: str = ""
    status_token_secret: str = "dev-status-token-secret-change-me-32-bytes"
    data_consent_required: bool = False
    session_ttl_seconds: int = 1200
    context_ttl_seconds: int = 900
    context_max_topics: int = 12
    proposal_ttl_seconds: int = 300
    retention_days: int = 30
    embedding_model_path: str = ""
    embedding_manifest_path: str = ""
    rerank_model_path: str = ""
    rerank_manifest_path: str = ""
    rag_min_dense_similarity: float = 0.70
    rag_vector_path: str = "data/vectors"
    rag_lexical_coverage: float = 0.60
    rag_rrf_k: int = 60
    rag_dense_max_rows: int = 5000
    rag_dense_budget_ms: int = 750
    rag_rerank_budget_ms: int = 500
    rag_rerank_top_k: int = 5
    rag_rerank_max_length: int = 128
    rag_rerank_input: str = "context_text"
    rag_rerank_fusion_alpha: float = 0.5
    rag_rerank_metadata_bonus: float = 0.2
    rag_rerank_on_failure: str = "keep_rrf"
    whisper_model_path: str = ""
    voice_stt_models: Mapping[str, Mapping[str, str]] = Field(default_factory=dict)
    stt_hallucination_phrases: tuple[str, ...] = ()
    piper_models_dir: str = ""
    piper_executable: str = "piper"
    voice_tts: Mapping[str, Mapping[str, str]] = Field(default_factory=dict)
    max_audio_bytes: int = 6_000_000
    stt_timeout_seconds: float = 35.0
    tts_timeout_seconds: float = 25.0
    max_audio_seconds: float = 25.0
    voice_tts_first_audio_ms: int = 1200
    voice_speech_plan: Mapping[str, int] = Field(default_factory=lambda: {
        "max_chars": 750, "first_chunk_max_chars": 120, "clause_split_min_chars": 220,
    })
    voice_language_switch_min_probability: float = 0.8
    voice_slm_caps: Mapping[str, float] = Field(default_factory=lambda: {
        "reference": 0.8, "planner": 1.5, "goal": 1.0, "intent": 1.5, "generation": 1.5,
    })
    stt_threads_max: int = 8
    stt_prompt_labels: int = 8
    stt_prompt_languages: tuple[str, ...] = ("vi", "ko", "zh")
    stt_short_audio_seconds: float = 1.5
    stt_short_audio_temperature: float = 0.0
    voice_ws_idle_timeout_seconds: float = 5.0
    voice_vad_stop_secs: float = 0.65
    voice_user_turn_stop_timeout_seconds: float = 2.0
    slm_circuit_cooldown_seconds: float = 15.0
    # ``legacy`` keeps the existing HTTP chunk transport. ``pipecat`` selects
    # the guarded full-duplex websocket adapter when the optional extra is
    # installed.
    voice_transport: str = "legacy"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_fallback_model: str = ""
    llm_model_digest: str = ""
    local_ai_strict_mode: bool = False
    semantic_generation_enabled: bool = False
    semantic_require_independent_nli: bool = False
    voice_windowed_preview_enabled: bool = False
    voice_preview_stability_enabled: bool = False
    voice_max_previews: int = 2
    voice_incremental_enabled: bool = False
    voice_incremental_model_path: str = ""
    voice_incremental_language: str = "en"
    voice_incremental_manifest_path: str = ""
    voice_incremental_require_manifest: bool = False
    voice_final_manifest_path: str = ""
    voice_final_require_manifest: bool = False
    nli_manifest_path: str = ""
    nli_require_manifest: bool = False
    agent_planner_enabled: bool = False
    # Embedding-based service understanding (candidates, few-shots and the
    # model-free fallback). Off only where tests need hermetic routing.
    semantic_understanding_enabled: bool = True
    agent_max_steps: int = 8
    agent_max_wall_time_ms: int = 15000
    agent_max_planner_calls: int = 5
    agent_max_read_calls: int = 5
    agent_planner_timeout_seconds: float = 5.0
    goal_interpreter_timeout_seconds: float = 2.5
    reference_resolver_timeout_seconds: float = 1.8
    intent_parser_timeout_seconds: float = 3.0
    text_generation_timeout_seconds: float = 3.0
    slm_generation_timeout_seconds: float = 8.0
    slm_probe_timeout_seconds: float = 1.5
    semantic_verifier_model: str = ""
    nli_model_path: str = ""
    nli_min_confidence: float = 0.85
    allowed_client_cidrs: str = ""
    staff_credentials_json: str = ""
    staff_gateway_token: str = ""
    map_release_path: str = ""
    map_release_sha256: str = ""
    planning_release_path: str = ""
    planning_release_sha256: str = ""
    property_profile_path: str = ""
    property_profile_sha256: str = ""
    property_profile_signature_path: str = ""
    property_profile_public_key_path: str = ""
    structured_dataset_dir: str = "datasets"
    domain_profile_path: str = ""
    domain_profile_sha256: str = ""
    runtime_profile_path: str = ""
    runtime_profile_sha256: str = ""
    runtime_profile_id: str = ""
    production_signoff_path: str = ""
    production_signoff_signature_path: str = ""
    production_signoff_public_key_path: str = ""
    # The real deployment profile is selected by load_settings() for production.
    # Direct Settings(...) in isolated tests remains explicitly unprovisioned.
    real_runtime_required: bool = False

    @field_validator(
        "local_ai_strict_mode",
        "semantic_generation_enabled", "semantic_require_independent_nli",
        "voice_windowed_preview_enabled", "voice_preview_stability_enabled",
        "voice_incremental_enabled", "voice_incremental_require_manifest",
        "voice_final_require_manifest", "nli_require_manifest",
        "agent_planner_enabled", "semantic_understanding_enabled", "real_runtime_required",
        "data_consent_required",
        mode="before",
    )
    @classmethod
    def _strict_bool(cls, value, info):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError(f"CONCIERGE_{info.field_name.upper()} must be true or false")

    def llm_candidates(self) -> tuple[str, ...]:
        """Return bounded local SLM candidates. Strict profiles never fall back."""
        models: list[str] = []
        if self.llm_model:
            models.append(self.llm_model)
        if (not self.local_ai_strict_mode and self.llm_fallback_model
                and self.llm_fallback_model not in models):
            models.append(self.llm_fallback_model)
        return tuple(models)

    def validate(self) -> None:
        if self.environment == "production" and self._profile_override_keys:
            keys = ", ".join(sorted(self._profile_override_keys))
            raise ValueError(
                "Production forbids environment overrides of pinned runtime profile keys: "
                + keys)
        if self.embedding_manifest_path and not self.embedding_model_path:
            raise ValueError('Embedding manifest requires a configured embedding model')
        if self.rerank_manifest_path and not self.rerank_model_path:
            raise ValueError('Reranker manifest requires a configured reranker model')
        if self.embedding_manifest_path:
            if self.embedding_model_path.startswith('ollama://'):
                from ..rag.embedding.ollama import validate_ollama_manifest
                validate_ollama_manifest(self.embedding_manifest_path,
                                         self.embedding_model_path.removeprefix('ollama://').strip('/'))
            else:
                from .model_manifest import verify_rag_model_manifest
                if not verify_rag_model_manifest(self.embedding_model_path, self.embedding_manifest_path):
                    raise ValueError('Local embedding model manifest integrity check failed')
        if self.rerank_manifest_path:
            from .model_manifest import verify_rag_model_manifest
            if not verify_rag_model_manifest(self.rerank_model_path, self.rerank_manifest_path):
                raise ValueError('Local reranker manifest integrity check failed')

        if bool(self.domain_profile_path) != bool(self.domain_profile_sha256):
            raise ValueError('Agent domain profile path and pinned SHA-256 must be configured together')
        if self.domain_profile_sha256 and (len(self.domain_profile_sha256) != 64 or
                                            any(c not in '0123456789abcdef' for c in self.domain_profile_sha256)):
            raise ValueError('Invalid pinned agent domain profile SHA-256')
        if bool(self.runtime_profile_path) != bool(self.runtime_profile_sha256):
            raise ValueError('Runtime profile path and pinned SHA-256 must be configured together')
        if self.runtime_profile_sha256 and (len(self.runtime_profile_sha256) != 64 or
                                             any(c not in '0123456789abcdef' for c in self.runtime_profile_sha256)):
            raise ValueError('Invalid pinned runtime profile SHA-256')
        if self.runtime_profile_path and not self.runtime_profile_id:
            raise ValueError('Loaded runtime profile identity is required')
        if bool(self.property_profile_path) != bool(self.property_profile_sha256):
            raise ValueError('Property profile path and pinned SHA-256 must be configured together')
        if bool(self.property_profile_signature_path) != bool(self.property_profile_public_key_path):
            raise ValueError('Property profile signature and public key must be configured together')
        if (self.property_profile_signature_path or self.property_profile_public_key_path) and not self.property_profile_path:
            raise ValueError('Property profile trust material requires a configured profile')
        signoff_paths = (self.production_signoff_path, self.production_signoff_signature_path,
                         self.production_signoff_public_key_path)
        if any(signoff_paths) and not all(signoff_paths):
            raise ValueError('Production sign-off receipt, signature and public key must be configured together')
        if self.property_profile_sha256 and (len(self.property_profile_sha256) != 64 or
                                              any(c not in '0123456789abcdef' for c in self.property_profile_sha256)):
            raise ValueError('Invalid pinned property profile SHA-256')
        if bool(self.planning_release_path) != bool(self.planning_release_sha256):
            raise ValueError('Scheduling release path and pinned SHA-256 must be configured together')
        if self.planning_release_sha256 and (len(self.planning_release_sha256) != 64 or
                                              any(c not in '0123456789abcdef' for c in self.planning_release_sha256)):
            raise ValueError('Invalid pinned scheduling SHA-256')
        if bool(self.map_release_path) != bool(self.map_release_sha256):
            raise ValueError('Map release path and pinned SHA-256 must be configured together')
        if self.map_release_sha256 and (len(self.map_release_sha256) != 64 or
                                        any(c not in '0123456789abcdef' for c in self.map_release_sha256)):
            raise ValueError('Invalid pinned map SHA-256')
        if not self.property_id or len(self.property_id) > 64:
            raise ValueError("Property ID required")
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(self.property_timezone)
        except Exception as exc:
            raise ValueError("Invalid property timezone") from exc
        if self.environment not in {"test", "development", "production"}:
            raise ValueError("Unknown environment")
        if self.environment == "production":
            if not self.data_consent_required:
                raise ValueError("Production requires explicit guest data consent")
            from urllib.parse import urlsplit
            public, staff = urlsplit(self.public_origin), urlsplit(self.staff_origin)
            if (public.scheme != "https" or not public.hostname or public.username or
                public.path not in ("", "/") or public.query or public.fragment):
                raise ValueError("Production kiosk must use a canonical HTTPS origin")
            if (staff.scheme != "https" or not staff.hostname or staff.username or
                staff.path not in ("", "/") or staff.query or staff.fragment or
                self.staff_origin == self.public_origin):
                raise ValueError("Production requires a separate HTTPS staff origin")
            # Different ports alone do not provide the intended DNS/host-based
            # staff ingress separation on a lobby appliance.
            if staff.hostname == public.hostname:
                raise ValueError("Production staff and public ingress require distinct hostnames")
            if (len(self.agent_token) < 32 or self.agent_token.startswith("dev-")):
                raise ValueError("Use a random, independent agent credential")
            if (len(self.status_token_secret) < 32 or self.status_token_secret.startswith("dev-")):
                raise ValueError("Production requires a random status token secret")
            if self.status_token_secret in {self.agent_token, self.staff_gateway_token}:
                raise ValueError("Status token secret must be distinct")
            if len(self.staff_gateway_token) < 32 or self.staff_gateway_token.startswith("dev-") or self.staff_gateway_token == self.agent_token:
                raise ValueError("Production requires a distinct random staff gateway secret")
            if not self.staff_credentials_json:
                raise ValueError("Production requires staff accounts with scoped credentials")
            if not self.allowed_client_cidrs:
                raise ValueError("Production must restrict kiosk ingress CIDRs")
            try:
                for address in self.allowed_client_cidrs.split(","):
                    network = ipaddress.ip_network(address.strip(), strict=False)
                    if network.prefixlen == 0 or not (network.is_private or network.is_loopback):
                        raise ValueError("Production ingress requires private or loopback reverse-proxy CIDRs")
                accounts = json.loads(self.staff_credentials_json)
                if not isinstance(accounts,list) or not accounts:
                    raise ValueError("Staff accounts required")
                names=set()
                for item in accounts:
                    if not isinstance(item,dict) or not item.get("name") or item["name"] in names:
                        raise ValueError("Staff account names must be unique")
                    names.add(item["name"])
                    token_hash=item.get("token_hash", "")
                    if len(token_hash)!=64 or any(c not in "0123456789abcdef" for c in token_hash):
                        raise ValueError("Staff token hashes must be lowercase SHA-256")
                    scopes=set(item.get("scopes",[]))
                    if not scopes or not scopes.issubset({"requests:read","requests:write"}):
                        raise ValueError("Unknown staff account scope")
            except (TypeError,ValueError,KeyError) as exc:
                raise ValueError(f"Invalid scoped staff credentials: {exc}") from exc
            if self.property_id in {"DEMO-HOTEL", "UNCONFIGURED"}:
                raise ValueError("Configure a real property ID in production")
        if self.real_runtime_required:
            if self.environment != 'production':
                raise ValueError('Real-runtime profile requires production security settings')
            if (not self.property_profile_path and
                    ('DEMO' in self.property_name.upper() or self.property_name == 'Property not configured' or not self.property_name.strip())):
                raise ValueError('Real-runtime requires an operator-approved property name or signed property profile')
            required = {
                'signed property profile, hash and trust key': bool(self.property_profile_path and self.property_profile_sha256 and self.property_profile_signature_path and self.property_profile_public_key_path),
                'approved map release and hash': bool(self.map_release_path and self.map_release_sha256),
                'approved activity release and hash': bool(self.planning_release_path and self.planning_release_sha256),
                'pinned learned multilingual dense embedding model': bool(
                    self.embedding_model_path and self.embedding_manifest_path and
                    __import__('concierge_kiosk.core.model_manifest', fromlist=['learned_embedding_profile'])
                    .learned_embedding_profile(self.embedding_model_path)),
                'pinned local reranker': bool(self.rerank_model_path and self.rerank_manifest_path),
                'local multilingual final STT': bool(self.whisper_model_path),
                'local Piper voice directory': bool(self.piper_models_dir),
                'pinned local SLM': bool(self.llm_base_url and self.llm_model and self.llm_model_digest),
                'strict local SLM and independent NLI': (self.local_ai_strict_mode and
                    self.semantic_generation_enabled and self.semantic_require_independent_nli and
                    self.nli_require_manifest and bool(self.nli_model_path and self.nli_manifest_path)),
                'next-action agent planner': self.agent_planner_enabled,
                'pinned incremental STT': (self.voice_incremental_enabled and
                    self.voice_incremental_require_manifest and
                    bool(self.voice_incremental_model_path and self.voice_incremental_manifest_path)),
                'pinned final Whisper/Piper assets': (self.voice_final_require_manifest and
                    bool(self.voice_final_manifest_path)),
                'durable graph': True,
                'operator-signed production readiness receipt': bool(
                    self.production_signoff_path and self.production_signoff_signature_path
                    and self.production_signoff_public_key_path),
            }
            missing = [name for name, present in required.items() if not present]
            if missing:
                raise ValueError('Real-runtime configuration incomplete: ' + ', '.join(missing))
        if (self.environment == "production"
                and os.getenv("LANGGRAPH_STRICT_MSGPACK", "").lower() != "true"):
            raise ValueError("Production requires LANGGRAPH_STRICT_MSGPACK=true for checkpoint safety")
        if self.retention_days < 1 or self.session_ttl_seconds < 60:
            raise ValueError("Invalid retention or session duration")
        if not (1 <= self.context_ttl_seconds <= self.session_ttl_seconds
                and 1 <= self.context_max_topics <= 32):
            raise ValueError("Invalid bounded conversation context")
        if not (0.01 <= self.stt_timeout_seconds <= 60 and
                0.01 <= self.tts_timeout_seconds <= 60 and
                1 <= self.max_audio_seconds <= 60 and
                1024 <= self.max_audio_bytes <= 20_000_000):
            raise ValueError("Invalid speech timeout")
        if not 0 <= self.voice_language_switch_min_probability <= 1:
            raise ValueError("Invalid voice language-switch probability")
        caps = self.voice_slm_caps or {}
        if set(caps) != {"reference", "planner", "goal", "intent", "generation"}:
            raise ValueError("Invalid voice SLM caps")
        if any(not 0.1 <= float(value) <= 10 for value in caps.values()):
            raise ValueError("Invalid voice SLM cap")
        if not 1 <= self.stt_threads_max <= 64 or not 0 <= self.stt_prompt_labels <= 32:
            raise ValueError("Invalid STT resource budget")
        from concierge_kiosk.core.domain_profile import supported_languages
        if not self.stt_prompt_languages or any(language not in supported_languages()
                                                for language in self.stt_prompt_languages):
            raise ValueError("Invalid STT prompt languages")
        if not (0.1 <= self.stt_short_audio_seconds <= 10 and
                0 <= self.stt_short_audio_temperature <= 1):
            raise ValueError("Invalid short-audio STT decode policy")
        if not 1 <= self.voice_ws_idle_timeout_seconds <= 120:
            raise ValueError("Invalid voice WebSocket idle timeout")
        if not 0.1 <= self.voice_vad_stop_secs <= 5:
            raise ValueError("Invalid voice VAD stop duration")
        if not 0.5 <= self.voice_user_turn_stop_timeout_seconds <= 30:
            raise ValueError("Invalid voice user-turn stop timeout")
        if not 1 <= self.slm_circuit_cooldown_seconds <= 300:
            raise ValueError("Invalid SLM circuit cooldown")
        if self.voice_transport not in {"legacy", "pipecat"}:
            raise ValueError("Unknown voice transport")
        if not (-1 <= self.rag_min_dense_similarity <= 1 and
                0 < self.rag_lexical_coverage <= 1 and 1 <= self.rag_rrf_k <= 1000
                and 10 <= self.rag_dense_max_rows <= 100000
                and 1 <= self.rag_dense_budget_ms <= 30000
                and 1 <= self.rag_rerank_budget_ms <= 30000
                and 2 <= self.rag_rerank_top_k <= 10
                and 32 <= self.rag_rerank_max_length <= 512
                and 0 <= self.rag_rerank_fusion_alpha <= 1
                and 0 <= self.rag_rerank_metadata_bonus <= 1):
            raise ValueError('Invalid RAG policy thresholds')
        vector_path = Path(self.rag_vector_path)
        if vector_path.is_symlink() or any(part == '..' for part in vector_path.parts):
            raise ValueError('Invalid vector index path')
        if self.rag_rerank_input not in {'context_text', 'body'}:
            raise ValueError('Invalid reranker input field')
        if self.rag_rerank_on_failure not in {'keep_rrf', 'abstain'}:
            raise ValueError('Invalid reranker failure policy')
        if not 1 <= self.voice_max_previews <= 4:
            raise ValueError("Invalid bounded voice preview limit")
        if self.voice_preview_stability_enabled and not self.voice_windowed_preview_enabled:
            raise ValueError("Voice stability metadata requires windowed previews")
        if self.voice_incremental_language not in supported_languages():
            raise ValueError("Invalid incremental STT model language")
        if self.voice_incremental_enabled:
            voice_model = Path(self.voice_incremental_model_path)
            if not voice_model.is_dir() or voice_model.is_symlink():
                raise ValueError("Incremental PCM STT requires a local model directory")
            if self.voice_incremental_require_manifest and not self.voice_incremental_manifest_path:
                raise ValueError("Pinned PCM STT requires an operator-approved manifest")
            if self.voice_incremental_manifest_path:
                from .model_manifest import verify_voice_manifest
                if not verify_voice_manifest(self.voice_incremental_model_path,
                                             self.voice_incremental_manifest_path):
                    raise ValueError("Local Vosk model manifest integrity check failed")
        if self.voice_final_require_manifest and not self.voice_final_manifest_path:
            raise ValueError("Production final speech assets require an operator-approved manifest")
        if self.voice_final_manifest_path and self.real_runtime_required:
            from ..voice.runtime.final_assets import verify_final_voice_manifest
            if not verify_final_voice_manifest(self, self.voice_final_manifest_path):
                raise ValueError("Final Whisper/Piper manifest integrity check failed")
        if self.nli_require_manifest and not (self.nli_model_path and self.nli_manifest_path):
            raise ValueError("Strict NLI artifact policy requires a local model and manifest")
        if self.nli_manifest_path:
            from .model_manifest import verify_model_manifest
            if not verify_model_manifest(self.nli_model_path, self.nli_manifest_path):
                raise ValueError("Local NLI manifest integrity check failed")
        if self.llm_fallback_model and (len(self.llm_fallback_model) > 128 or self.llm_fallback_model == self.llm_model):
            raise ValueError('Invalid local SLM fallback model')
        if self.llm_model_digest and not (len(self.llm_model_digest.removeprefix('sha256:')) == 64 and
                all(c in '0123456789abcdef' for c in self.llm_model_digest.removeprefix('sha256:'))):
            raise ValueError('Invalid exact local SLM SHA-256 digest')
        if self.local_ai_strict_mode and not (
                self.llm_base_url and self.llm_model and self.llm_model_digest and
                self.semantic_generation_enabled and self.semantic_require_independent_nli and
                self.nli_model_path and self.nli_manifest_path and self.nli_require_manifest):
            raise ValueError('Strict local AI requires pinned generator, independent NLI and semantic opt-in')
        if self.agent_planner_enabled and not (self.llm_base_url and self.llm_model):
            raise ValueError('Next-action planner requires a local SLM')
        if not 2 <= self.agent_max_steps <= 12:
            raise ValueError('Agent max steps must be between 2 and 12')
        if not 1000 <= self.agent_max_wall_time_ms <= 60000:
            raise ValueError('Agent wall-time budget must be between 1000 and 60000 ms')
        if not 0 <= self.agent_max_planner_calls <= self.agent_max_steps:
            raise ValueError('Agent planner-call budget must fit within max steps')
        if not 1 <= self.agent_max_read_calls <= self.agent_max_steps:
            raise ValueError('Agent read-call budget must fit within max steps')
        if not 0.5 <= self.agent_planner_timeout_seconds <= 10.0:
            raise ValueError('Agent planner timeout must be between 0.5 and 10 seconds')
        if not 0.5 <= self.goal_interpreter_timeout_seconds <= 10.0:
            raise ValueError('Goal interpreter timeout must be between 0.5 and 10 seconds')
        if not 0.5 <= self.intent_parser_timeout_seconds <= 10.0:
            raise ValueError('Invalid intent parser timeout')
        if not 0.5 <= self.text_generation_timeout_seconds <= 30.0:
            raise ValueError('Invalid text generation timeout')
        if not 0.5 <= self.reference_resolver_timeout_seconds <= 10.0:
            raise ValueError('Reference resolver timeout must be between 0.5 and 10 seconds')
        if not 0.5 <= self.slm_generation_timeout_seconds <= 30.0:
            raise ValueError('SLM generation timeout must be between 0.5 and 30 seconds')
        if not 0.1 <= self.slm_probe_timeout_seconds <= 5.0:
            raise ValueError('SLM probe timeout must be between 0.1 and 5 seconds')
        if self.semantic_generation_enabled and not (self.llm_base_url and self.llm_model):
            raise ValueError("Model-assisted semantic generation requires a local SLM")
        if self.semantic_require_independent_nli and (not self.semantic_generation_enabled or not self.nli_model_path):
            raise ValueError("Strict semantic mode requires opt-in generation and an independent local NLI model")
        if self.semantic_verifier_model and not self.semantic_generation_enabled:
            raise ValueError("Semantic verifier model requires semantic generation opt-in")
        if self.nli_model_path:
            model_dir = Path(self.nli_model_path)
            if (not self.semantic_generation_enabled or not model_dir.is_dir() or
                    model_dir.is_symlink() or not (model_dir / 'config.json').is_file()):
                raise ValueError('Independent NLI requires semantic opt-in and a local model directory')
        if not 0.5 <= self.nli_min_confidence <= 1.0:
            raise ValueError('Invalid independent NLI confidence threshold')
        if bool(self.llm_base_url) != bool(self.llm_model):
            raise ValueError("Local SLM endpoint and model must be configured together")
        if self.llm_base_url:
            from urllib.parse import urlsplit
            url = urlsplit(self.llm_base_url)
            from concierge_kiosk.runtime.local_http import LOOPBACK_HOSTS
            if url.scheme != 'http' or url.hostname not in LOOPBACK_HOSTS or url.username:
                raise ValueError("Offline SLM endpoint must be HTTP on loopback")


def _secret(name: str, default: str = "") -> str:
    """Read a secret via environment OR a root-provisioned file, never both.

    Docker secrets are read at process startup; restart for rotation. Avoid logs.
    """
    direct, filename = os.environ.get(name), os.environ.get(name + "_FILE")
    if direct is not None and filename:
        raise ValueError(f"Configure either {name} or {name}_FILE, not both")
    if filename:
        path = Path(filename)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Invalid secret file for {name}")
        if path.stat().st_size > 32_768:
            raise ValueError(f"Secret file too large for {name}")
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    return direct if direct is not None else default


def _profile_env_overrides(profile_fields: set[str]) -> frozenset[str]:
    """Return explicit environment keys that shadow pinned profile values."""
    overrides: set[str] = set()
    prefix = "CONCIERGE_"
    for key in os.environ:
        if not key.startswith(prefix):
            continue
        field = key.removeprefix(prefix).lower().split("__", 1)[0]
        if field == "env":
            continue
        if field in profile_fields:
            overrides.add(key)
    return frozenset(overrides)


def load_settings() -> Settings:
    # No environment flag means the secure production profile, NOT demo mode.
    # Unit tests explicitly select test; intentional local development must
    # explicitly select development.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    bootstrap = _BootstrapSettings()
    environment = bootstrap.environment.strip().lower()
    # Runtime model/budget/feature choices are checksum-pinned operator data.
    # Environment variables remain explicit process-level overrides.
    from .runtime_profile import default_runtime_profile_binding, load_runtime_profile
    runtime_path, runtime_sha256 = default_runtime_profile_binding(environment)
    runtime_profile = load_runtime_profile(runtime_path, runtime_sha256)
    models = runtime_profile.models
    features = runtime_profile.features
    budgets = runtime_profile.budgets
    timeouts = runtime_profile.timeouts
    slm_defaults = models["slm"]
    embedding_defaults = runtime_profile.embedding_assets()
    reranker_defaults = models["reranker"]
    nli_defaults = models["nli"]
    voice_defaults = models["voice"]
    agent_defaults = budgets["agent"]
    rag_defaults = budgets["rag"]
    voice_budget_defaults = budgets["voice"]
    slm_budget_defaults = budgets["slm"]
    real_runtime = environment == 'production'
    from .domain_profile import default_domain_profile_binding, load_domain_profile
    default_domain_path, default_domain_sha256 = default_domain_profile_binding()
    domain_profile = load_domain_profile(default_domain_path, default_domain_sha256)
    memory_defaults = domain_profile.memory_policy
    session_ttl_seconds = bootstrap.session_ttl_seconds
    property_profile_path = bootstrap.property_profile_path.strip()
    property_profile_sha256 = bootstrap.property_profile_sha256.strip().lower()
    property_profile_signature_path = bootstrap.property_profile_signature_path.strip()
    property_profile_public_key_path = bootstrap.property_profile_public_key_path.strip()

    # A pinned property profile owns deployment identity. Optional legacy env
    # identity values may remain during rollout, but they must agree exactly; a
    # stale property-specific bootstrap cannot silently point one profile at another
    # property's database namespace or timezone.
    pinned_property = None
    if property_profile_path or property_profile_sha256:
        if not property_profile_path or not property_profile_sha256:
            raise ValueError('Property profile path and pinned SHA-256 must be configured together')
        from .property_profile import load_property_profile
        pinned_property = load_property_profile(
            property_profile_path, property_profile_sha256,
            max_session_ttl=session_ttl_seconds,
            signature_path=property_profile_signature_path,
            public_key_path=property_profile_public_key_path)

    property_id = pinned_property.property_id if pinned_property else bootstrap.property_id
    property_name = pinned_property.property_name if pinned_property else bootstrap.property_name
    property_timezone = pinned_property.property_timezone if pinned_property else bootstrap.property_timezone
    if pinned_property:
        identity_env = {
            'property_id': property_id,
            'property_name': property_name,
            'property_timezone': property_timezone,
        }
        for field_name, expected in identity_env.items():
            if field_name in bootstrap.model_fields_set:
                supplied = str(getattr(bootstrap, field_name)).strip()
                if supplied != expected:
                    raise ValueError(
                        f'CONCIERGE_{field_name.upper()} conflicts with pinned property profile')
    embedding_model_path = (bootstrap.embedding_model_path
                            if bootstrap.embedding_model_path is not None
                            else embedding_defaults[0])
    embedding_manifest_path = (bootstrap.embedding_manifest_path
                               if bootstrap.embedding_manifest_path is not None
                               else embedding_defaults[1])
    if embedding_model_path.startswith('ollama://') and bootstrap.embedding_model_path is not None \
            and bootstrap.embedding_manifest_path is None:
        embedding_manifest_path = ''
    profile_values = {
        "embedding_model_path": embedding_model_path,
        "embedding_manifest_path": embedding_manifest_path,
        "rerank_model_path": str(reranker_defaults["model_path"]),
        "rerank_manifest_path": str(reranker_defaults["manifest_path"]),
        "rag_min_dense_similarity": float(rag_defaults["min_dense_similarity"]),
        "rag_vector_path": str(rag_defaults.get("vector_path", "data/vectors")),
        "rag_lexical_coverage": float(rag_defaults["lexical_coverage"]),
        "rag_rrf_k": int(rag_defaults["rrf_k"]),
        "rag_dense_max_rows": int(rag_defaults["dense_max_rows"]),
        "rag_dense_budget_ms": int(rag_defaults["dense_budget_ms"]),
        "rag_rerank_budget_ms": int(rag_defaults["rerank_budget_ms"]),
        "rag_rerank_top_k": int(rag_defaults["rerank_top_k"]),
        "rag_rerank_max_length": int(rag_defaults["rerank_max_length"]),
        "rag_rerank_input": str(rag_defaults["rerank_input"]),
        "rag_rerank_fusion_alpha": float(rag_defaults["rerank_fusion_alpha"]),
        "rag_rerank_metadata_bonus": float(rag_defaults["rerank_metadata_bonus"]),
        "rag_rerank_on_failure": str(rag_defaults["rerank_on_failure"]),
        "whisper_model_path": str(voice_defaults["whisper_model_path"]),
        "voice_stt_models": voice_defaults.get("stt_models", {}),
        "stt_hallucination_phrases": tuple(voice_defaults["stt_hallucination_phrases"]),
        "stt_timeout_seconds": float(timeouts["stt_seconds"]),
        "tts_timeout_seconds": float(timeouts["tts_seconds"]),
        "max_audio_seconds": float(voice_budget_defaults["max_audio_seconds"]),
        "max_audio_bytes": int(voice_budget_defaults["max_audio_bytes"]),
        "voice_language_switch_min_probability": float(voice_budget_defaults["language_switch_min_probability"]),
        "voice_slm_caps": {key: float(value) for key, value in voice_budget_defaults["slm_caps"].items()},
        "stt_threads_max": int(voice_budget_defaults["stt_threads_max"]),
        "stt_prompt_labels": int(voice_budget_defaults["stt_prompt_labels"]),
        "stt_prompt_languages": tuple(voice_budget_defaults["stt_prompt_languages"]),
        "stt_short_audio_seconds": float(voice_budget_defaults["stt_short_audio_seconds"]),
        "stt_short_audio_temperature": float(voice_budget_defaults["stt_short_audio_temperature"]),
        "voice_ws_idle_timeout_seconds": float(voice_budget_defaults["ws_idle_timeout_seconds"]),
        "voice_vad_stop_secs": float(voice_budget_defaults["voice_vad_stop_secs"]),
        "voice_user_turn_stop_timeout_seconds": float(
            voice_budget_defaults["voice_user_turn_stop_timeout_seconds"]),
        "slm_circuit_cooldown_seconds": float(slm_budget_defaults["circuit_cooldown_seconds"]),
        "voice_transport": str(features.get("voice_transport", "legacy")),
        "piper_models_dir": str(voice_defaults["piper_models_dir"]),
        "piper_executable": str(voice_defaults["piper_executable"]),
        "voice_tts": voice_defaults.get("tts", {}),
        "voice_tts_first_audio_ms": int(voice_budget_defaults.get("tts_first_audio_ms", 1200)),
        "voice_speech_plan": dict(voice_budget_defaults.get("speech_plan", {
            "max_chars": 750, "first_chunk_max_chars": 120, "clause_split_min_chars": 220,
        })),
        "llm_base_url": str(slm_defaults["base_url"]),
        "llm_model": str(slm_defaults["primary_model"]),
        "llm_fallback_model": str(slm_defaults["fallback_model"]),
        "llm_model_digest": str(slm_defaults["digest"]),
        "local_ai_strict_mode": bool(slm_defaults["strict_mode"]),
        "semantic_generation_enabled": bool(features["semantic_generation"]),
        "semantic_require_independent_nli": bool(features["semantic_require_independent_nli"]),
        "voice_windowed_preview_enabled": bool(features["voice_windowed_preview"]),
        "voice_preview_stability_enabled": bool(features["voice_preview_stability"]),
        "voice_max_previews": int(voice_budget_defaults["max_previews"]),
        "voice_incremental_enabled": bool(features["voice_incremental"]),
        "voice_incremental_model_path": str(voice_defaults["incremental_model_path"]),
        "voice_incremental_language": str(voice_defaults["incremental_language"]),
        "voice_incremental_manifest_path": str(voice_defaults["incremental_manifest_path"]),
        "voice_incremental_require_manifest": bool(voice_defaults["incremental_require_manifest"]),
        "voice_final_manifest_path": str(voice_defaults["final_manifest_path"]),
        "voice_final_require_manifest": bool(voice_defaults["final_require_manifest"]),
        "nli_manifest_path": str(nli_defaults["manifest_path"]),
        "nli_require_manifest": bool(nli_defaults["require_manifest"]),
        "agent_planner_enabled": bool(features["agent_planner"]),
        "semantic_understanding_enabled": bool(features["semantic_understanding"]),
        "agent_max_steps": int(agent_defaults["max_steps"]),
        "agent_max_wall_time_ms": int(agent_defaults["max_wall_time_ms"]),
        "agent_max_planner_calls": int(agent_defaults["max_planner_calls"]),
        "agent_max_read_calls": int(agent_defaults["max_read_calls"]),
        "agent_planner_timeout_seconds": float(timeouts["agent_planner_seconds"]),
        "goal_interpreter_timeout_seconds": float(timeouts["goal_interpreter_seconds"]),
        "reference_resolver_timeout_seconds": float(timeouts["reference_resolver_seconds"]),
        "intent_parser_timeout_seconds": float(timeouts["intent_parser_seconds"]),
        "text_generation_timeout_seconds": float(timeouts["text_generation_seconds"]),
        "slm_generation_timeout_seconds": float(timeouts["slm_generation_seconds"]),
        "slm_probe_timeout_seconds": float(timeouts["slm_probe_seconds"]),
        "semantic_verifier_model": str(nli_defaults["semantic_verifier_model"]),
        "nli_model_path": str(nli_defaults["model_path"]),
        "nli_min_confidence": float(nli_defaults["min_confidence"]),
    }
    status_token_secret = _secret("CONCIERGE_STATUS_TOKEN_SECRET")
    if not status_token_secret and environment != "production":
        # Development/test profiles may run without a provisioned secret. The
        # value is intentionally a clearly non-production fallback; production
        # validation rejects it and requires operator provisioning.
        status_token_secret = "dev-status-token-secret-change-me-32-bytes"
    base_values = {
        "property_id": property_id,
        "property_name": property_name,
        "property_timezone": property_timezone,
        "environment": environment,
        "session_ttl_seconds": session_ttl_seconds,
        "context_ttl_seconds": int(memory_defaults.conversation_ttl_seconds),
        "context_max_topics": int(memory_defaults.max_topics),
        "staff_token": _secret("CONCIERGE_STAFF_TOKEN"),
        "agent_token": _secret("CONCIERGE_AGENT_TOKEN"),
        "status_token_secret": status_token_secret,
        "data_consent_required": environment == "production",
        "staff_credentials_json": _secret("CONCIERGE_STAFF_CREDENTIALS_JSON"),
        "staff_gateway_token": _secret("CONCIERGE_STAFF_GATEWAY_TOKEN"),
        "property_profile_path": property_profile_path,
        "property_profile_sha256": property_profile_sha256,
        "property_profile_signature_path": property_profile_signature_path,
        "property_profile_public_key_path": property_profile_public_key_path,
        "domain_profile_path": default_domain_path,
        "domain_profile_sha256": default_domain_sha256,
        "runtime_profile_path": runtime_path,
        "runtime_profile_sha256": runtime_sha256,
        "runtime_profile_id": runtime_profile.profile_id,
        "real_runtime_required": real_runtime,
    }
    cfg = Settings(**(base_values | profile_values))
    object.__setattr__(cfg, "_profile_override_keys",
                       _profile_env_overrides(set(profile_values)))
    from concierge_kiosk.runtime.local_http import configure_slm_circuit_cooldown
    configure_slm_circuit_cooldown(cfg.slm_circuit_cooldown_seconds)
    from concierge_kiosk.voice.runtime.adapters import configure_stt_runtime
    configure_stt_runtime(
        threads_max=cfg.stt_threads_max,
        prompt_labels=cfg.stt_prompt_labels,
        prompt_languages=cfg.stt_prompt_languages,
    )
    cfg.validate()
    return cfg
