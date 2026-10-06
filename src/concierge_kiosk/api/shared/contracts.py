"""Validated HTTP request contracts shared by guest, staff and internal APIs."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, StrictBool, field_validator
from concierge_kiosk.domain.service_registry import LANGUAGES, REQUEST_KINDS

class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Ask(StrictRequest):
    query: str = Field(min_length=2, max_length=500)
    language: str = "vi"
    source: Literal['dialogue', 'sos_button'] = 'dialogue'

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str):
        if value not in LANGUAGES:
            raise ValueError("Unsupported language")
        return value
    previous_query: str = Field(default="", max_length=200)
    # Stable per-user-turn idempotency capability required before any autonomous
    # low-risk business write; old clients without it fall back to review.
    turn_nonce: str | None = Field(default=None, min_length=8, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    # Optional approved map place identifier; never an arbitrary route or GPS.
    start_location: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_-]{0,39}$')
    room_qr_token: str = Field(default='', max_length=1024)
    verification_room_number: str = Field(default='', max_length=24)


class ServicePayload(StrictRequest):
    room_number: str | None = Field(default=None, max_length=24)
    quantity: int | None = Field(default=None, ge=1, le=20)
    preferred_time: str | None = Field(default=None, max_length=40)
    party_size: int | None = Field(default=None, ge=1, le=30)
    note: str = Field(default="", max_length=500)
    price_acknowledged: StrictBool = False

    @field_validator('room_number', 'preferred_time', 'note')
    @classmethod
    def strip_text(cls, value: str | None):
        return value.strip() if isinstance(value, str) else value




class GuestVerificationInput(StrictRequest):
    room_number: str = Field(min_length=1, max_length=24)
    last_name: str = Field(default="", max_length=80)
    room_qr_token: str = Field(default="", max_length=1024)

    @field_validator("room_number", "last_name", "room_qr_token")
    @classmethod
    def strip_verification_text(cls, value: str):
        return value.strip()

    @field_validator("room_qr_token")
    @classmethod
    def require_one_credential(cls, value: str, info):
        # Cross-field enforcement happens at the workflow boundary because
        # Pydantic field validators do not reliably see following fields.
        return value

class Prepare(StrictRequest):
    kind: str
    language: str

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str):
        if value not in REQUEST_KINDS:
            raise ValueError("Unsupported request kind")
        return value

    @field_validator("language")
    @classmethod
    def validate_prepare_language(cls, value: str):
        if value not in LANGUAGES:
            raise ValueError("Unsupported language")
        return value
    details: str = Field(min_length=8, max_length=500)
    nonce: str = Field(min_length=8, max_length=80)
    payload: ServicePayload | None = None
    data_consent: StrictBool = False


class Consent(StrictRequest):
    purpose: Literal['service_request', 'proactive_suggestions']
    policy_version: str = Field(default='privacy-v1', min_length=1, max_length=32)
    granted: StrictBool


class Confirm(StrictRequest):
    proposal_id: str = Field(min_length=32, max_length=32)
    confirmed: StrictBool
    price_acknowledged: StrictBool = False
    verification: GuestVerificationInput | None = None


class CancelProposal(StrictRequest):
    proposal_id: str = Field(min_length=32, max_length=32)




class GuestRequestChange(StrictRequest):
    action: Literal["cancel", "modify"]
    nonce: str = Field(min_length=8, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    payload: ServicePayload | None = None
    note: str = Field(default="", max_length=300)


class RequestFeedback(StrictRequest):
    rating: int = Field(ge=1, le=5)
    note: str = Field(default='', max_length=300)

    @field_validator('note')
    @classmethod
    def strip_feedback_note(cls, value: str):
        return value.strip()


class GuestChangeReview(StrictRequest):
    action: Literal["approve", "reject"]
    note: str = Field(min_length=8, max_length=300)

class EmergencyTransition(StrictRequest):
    action: Literal['acknowledge', 'resolve']
    note: str = Field(default='', max_length=300)

class Transition(StrictRequest):
    action: Literal["approve", "reject", "start", "pause", "resume", "complete"]
    verified: StrictBool = False
    note: str = Field(default="", max_length=300)
    eta_minutes: int | None = Field(default=None, ge=1, le=720)
    assignee: str = Field(default="", max_length=80)

    @field_validator('assignee')
    @classmethod
    def strip_assignee(cls, value: str):
        return value.strip()


class AgentSession(StrictRequest):
    token: str
    csrf: str


class AgentAsk(Ask, AgentSession):
    pass


class AgentPrepare(Prepare, AgentSession):
    pass


class AgentConfirm(Confirm, AgentSession):
    pass


class ClientTelemetry(StrictRequest):
    # Exactly one allowlisted numerical observation. No arbitrary metadata/text.
    event_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    language: str

    @field_validator("language")
    @classmethod
    def validate_telemetry_language(cls, value: str):
        if value not in LANGUAGES:
            raise ValueError("Unsupported language")
        return value

    stage: Literal["client.vad_end", "client.stt", "client.ask",
                   "client.tts_first_audio", "client.e2e_first_audio",
                   "client.playback_total", "client.barge_pause",
                   "client.barge_false_pause"]
    duration_ms: float = Field(ge=0, le=120000, allow_inf_nan=False)


class SpeechText(StrictRequest):
    language: str

    @field_validator("language")
    @classmethod
    def validate_speech_language(cls, value: str):
        if value not in LANGUAGES:
            raise ValueError("Unsupported language")
        return value

    text: str = Field(min_length=1, max_length=750)


class SpeechChunkRequest(StrictRequest):
    chunk_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class PublicResponse(BaseModel):
    """Response base: document stable fields while preserving additive rollout fields."""
    model_config = ConfigDict(extra='allow')


class SessionResponse(PublicResponse):
    session_id: str
    csrf_token: str
    expires_in: int


class StatusResponse(PublicResponse):
    status: str


class SuggestedActionResponse(PublicResponse):
    kind: str
    details: str


class SpeechPlanChunkResponse(PublicResponse):
    id: str
    ordinal: int


class SpeechPlanResponse(PublicResponse):
    turn_id: str
    protocol: int
    chunks: list[SpeechPlanChunkResponse]


class AskResponse(PublicResponse):
    answer: str
    sources: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    suggested_action: SuggestedActionResponse | None = None
    retrieval_mode: str
    generation_mode: str
    request_completed: bool
    grounding: str
    requires_staff_review: bool
    speech_turn_id: str
    speech_plan: SpeechPlanResponse


class TurnLifecycleEventResponse(PublicResponse):
    sequence: int
    type: str
    elapsed_ms: int


class TurnLifecycleResponse(PublicResponse):
    turn_id: str
    events: list[TurnLifecycleEventResponse]
    latest_sequence: int
    cursor_expired: bool = False
    terminal: bool


class PrepareResponse(PublicResponse):
    proposal_id: str
    kind: str
    details: str
    status: str
    expires_at: int
    requires_confirmation: bool
    staff_verification_required: bool
    orchestration_sync: str
    service_code: str = ''
    price_disclosure_required: bool = False
    price_disclosure: str = ''
    outside_operating_hours: bool = False
    next_open_at: int | None = None


class ConfirmResponse(PublicResponse):
    request_id: str
    status: str
    orchestration_sync: str
    guest_verification_state: str = "staff_required"
    eta_minutes: int | None = None
    external_dispatch_state: str = "not_requested"
    message: str
    confirmation_code: str = ''
    status_url: str = ''
    status_token_expires_at: int | None = None


class CancelResponse(PublicResponse):
    proposal_id: str
    status: str


class GuestRequestResponse(PublicResponse):
    id: str
    confirmation_code: str = ''
    kind: str
    language: str
    status: str
    updated_at: int
    guest_change_state: str = 'none'
    guest_change_updated_at: int | None = None


class GuestRequestsResponse(PublicResponse):
    items: list[GuestRequestResponse]


class StatusHistoryEntryResponse(PublicResponse):
    status: str
    at: int | None = None


class GuestProgressResponse(PublicResponse):
    id: str
    kind: str
    language: str
    status: str
    status_history: list[StatusHistoryEntryResponse]
    can_cancel: bool
    can_modify: bool = False
    change_state: str = 'none'
    change_updated_at: int | None = None
    details: str = ''
    payload: dict = Field(default_factory=dict)
    effective_payload: dict = Field(default_factory=dict)
    staff_review_required: bool
    guest_verification_state: str = "staff_required"
    eta_minutes: int | None = None
    external_dispatch_state: str = "not_requested"
    priority: int = 3
    ack_due_at: int | None = None
    ack_overdue: bool = False
    sla_due_at: int | None = None
    overdue: bool = False
    escalation_level: int = 0
    unverified_room: bool = False
    feedback_requested: bool = False
    feedback_submitted: bool = False
    feedback: dict | None = None


class VoiceTurnResponse(PublicResponse):
    turn_id: str


class CancelVoiceTurnResponse(PublicResponse):
    cancelled: bool


class TranscriptionResponse(PublicResponse):
    text: str
    final: bool
    sequence: int | None = None
    turn_id: str | None = None
    detected_language: str | None = None
    language_probability: float | None = None
    confidence: float | None = None
    reject_reason: str | None = None
    suggest_language_switch: bool = False


class SpeechProofResponse(PublicResponse):
    authorized: bool
    chunk_id: str | None = None


class SpeechAckResponse(PublicResponse):
    acknowledged: bool
    chunk_id: str | None = None


class PublicConfigResponse(PublicResponse):
    product: str
    public_origin: str
    api_version: int
    languages: list[str]
    voice_available: bool
    voice_transport: str
    voice_agent_available: bool
    incremental_voice_available: bool
    incremental_voice_language: str
    stt_modes: dict[str, str]
    tts_languages: list[str]
    max_audio_bytes: int
    max_audio_seconds: float
    retrieval_mode: str
    orchestrator: str
    generation_mode: str
    data_consent_required: bool = False


class ServiceCatalogResponse(PublicResponse):
    items: list[dict]


class UiContractResponse(PublicResponse):
    contract_version: int
    languages: list[dict]
    request_types: list[dict]
    capabilities: dict[str, bool]
