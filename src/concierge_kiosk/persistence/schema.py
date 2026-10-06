"""SQLite DDL, schema version and cross-property integrity triggers."""
from __future__ import annotations

from concierge_kiosk.domain.service_registry import VERIFICATION_KINDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, csrf_hash TEXT NOT NULL,
  property_id TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS guest_consents (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  property_id TEXT NOT NULL, purpose TEXT NOT NULL,
  policy_version TEXT NOT NULL, granted INTEGER NOT NULL CHECK(granted IN (0,1)),
  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS guest_consents_scope
  ON guest_consents(property_id,session_id,purpose,created_at);
CREATE TABLE IF NOT EXISTS rate_limits (
  bucket TEXT NOT NULL, period INTEGER NOT NULL, count INTEGER NOT NULL,
  window_seconds INTEGER NOT NULL DEFAULT 60 CHECK(window_seconds > 0),
  PRIMARY KEY(bucket,period)
);
CREATE TABLE IF NOT EXISTS knowledge (
  id TEXT NOT NULL, property_id TEXT NOT NULL, language TEXT NOT NULL,
  title TEXT NOT NULL, heading TEXT NOT NULL, body TEXT NOT NULL, search_text TEXT NOT NULL,
  source TEXT NOT NULL, revision TEXT NOT NULL, classification TEXT NOT NULL,
  effective_from TEXT NOT NULL, effective_to TEXT, active INTEGER NOT NULL DEFAULT 1,
  embedding TEXT, embedding_model TEXT,
  domain TEXT NOT NULL DEFAULT 'general', parent_id TEXT NOT NULL DEFAULT '',
  section_id TEXT NOT NULL DEFAULT '', section_ordinal INTEGER NOT NULL DEFAULT 0,
  entity_id TEXT NOT NULL DEFAULT '', fact_type TEXT NOT NULL DEFAULT '',
  fact_context TEXT NOT NULL DEFAULT '', canonical_fact_id TEXT NOT NULL DEFAULT '',
  context_text TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(id, revision)
);
CREATE INDEX IF NOT EXISTS knowledge_scope ON knowledge(property_id, language, classification, active);
CREATE TABLE IF NOT EXISTS knowledge_parents (
  id TEXT NOT NULL, revision TEXT NOT NULL, property_id TEXT NOT NULL,
  language TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL,
  heading TEXT NOT NULL, section_id TEXT NOT NULL DEFAULT '',
  section_ordinal INTEGER NOT NULL DEFAULT 0, domain TEXT NOT NULL, body TEXT NOT NULL,
  classification TEXT NOT NULL, effective_from TEXT NOT NULL,
  effective_to TEXT, active INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(id, revision)
);
CREATE INDEX IF NOT EXISTS knowledge_parent_scope ON knowledge_parents
  (property_id, language, source, revision, classification, active);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
  doc_id UNINDEXED, revision UNINDEXED, search_text, tokenize='unicode61 remove_diacritics 0'
);
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY, property_id TEXT NOT NULL, session_id TEXT NOT NULL,
  client_nonce TEXT NOT NULL, kind TEXT NOT NULL, language TEXT NOT NULL,
  details TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL CHECK(status IN ('awaiting_confirmation','confirmed','cancelled','expired')),
  expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
  UNIQUE(session_id,client_nonce), FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS service_requests (
  id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),
  property_id TEXT NOT NULL, kind TEXT NOT NULL, language TEXT NOT NULL,
  details TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
  confirmation_code TEXT NOT NULL DEFAULT '',
  service_code TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK(status IN ('pending_staff','approved','in_progress','paused','rejected','completed')),
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  staff_note TEXT NOT NULL DEFAULT '', verified_by TEXT NOT NULL DEFAULT '',
  guest_change_state TEXT NOT NULL DEFAULT 'none',
  guest_change_payload_json TEXT NOT NULL DEFAULT '{}',
  guest_change_note TEXT NOT NULL DEFAULT '',
  guest_change_nonce_hash TEXT NOT NULL DEFAULT '',
  guest_change_updated_at INTEGER NOT NULL DEFAULT 0,
  guest_verification_state TEXT NOT NULL DEFAULT 'staff_required',
  guest_verification_provider TEXT NOT NULL DEFAULT '',
  guest_verification_reference TEXT NOT NULL DEFAULT '',
  eta_minutes INTEGER,
  eta_updated_at INTEGER NOT NULL DEFAULT 0,
  external_dispatch_state TEXT NOT NULL DEFAULT 'not_requested',
  external_dispatch_provider TEXT NOT NULL DEFAULT '',
  external_reference TEXT NOT NULL DEFAULT '',
  external_dispatch_error TEXT NOT NULL DEFAULT '',
  department_id TEXT NOT NULL DEFAULT '',
  priority INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
  ack_due_at INTEGER NOT NULL DEFAULT 0,
  ack_overdue INTEGER NOT NULL DEFAULT 0 CHECK(ack_overdue IN (0,1)),
  ack_escalation_sent_at INTEGER NOT NULL DEFAULT 0,
  sla_due_at INTEGER NOT NULL DEFAULT 0,
  overdue INTEGER NOT NULL DEFAULT 0 CHECK(overdue IN (0,1)),
  escalation_sent_at INTEGER NOT NULL DEFAULT 0,
  escalation_level INTEGER NOT NULL DEFAULT 0 CHECK(escalation_level BETWEEN 0 AND 2),
  assignee TEXT NOT NULL DEFAULT '',
  assigned_at INTEGER NOT NULL DEFAULT 0,
  started_at INTEGER NOT NULL DEFAULT 0,
  paused_at INTEGER NOT NULL DEFAULT 0,
  completed_at INTEGER NOT NULL DEFAULT 0,
  unverified_room INTEGER NOT NULL DEFAULT 0 CHECK(unverified_room IN (0,1))
);
CREATE INDEX IF NOT EXISTS service_requests_queue ON service_requests(property_id,status,created_at);
CREATE TABLE IF NOT EXISTS emergency_alerts (
  id TEXT PRIMARY KEY, property_id TEXT NOT NULL, session_id TEXT NOT NULL,
  language TEXT NOT NULL, details TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 100 CHECK(priority BETWEEN 1 AND 100),
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','resolved')),
  source TEXT NOT NULL DEFAULT 'dialogue' CHECK(source IN ('dialogue','sos')),
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  acknowledged_by TEXT NOT NULL DEFAULT '', resolution_note TEXT NOT NULL DEFAULT '',
  kiosk_location TEXT NOT NULL DEFAULT '', escalation_due_at INTEGER NOT NULL DEFAULT 0,
  escalation_level INTEGER NOT NULL DEFAULT 0 CHECK(escalation_level BETWEEN 0 AND 2),
  escalated_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS emergency_alerts_queue
  ON emergency_alerts(property_id,status,priority DESC,created_at ASC);
CREATE TABLE IF NOT EXISTS emergency_alert_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id TEXT NOT NULL REFERENCES emergency_alerts(id),
  property_id TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
  at INTEGER NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS emergency_alert_events_order
  ON emergency_alert_events(property_id,alert_id,id);
CREATE TABLE IF NOT EXISTS request_feedback (
  request_id TEXT PRIMARY KEY REFERENCES service_requests(id),
  property_id TEXT NOT NULL, session_id TEXT NOT NULL REFERENCES sessions(id),
  rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
  note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, action TEXT NOT NULL,
  actor TEXT NOT NULL, property_id TEXT NOT NULL, at INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '', independently_verified INTEGER NOT NULL DEFAULT 0
);
-- Durable receipt for staff API retries. A receipt and its audit event commit in
-- the SAME business transaction; the checkpointer is deliberately not involved.
CREATE TABLE IF NOT EXISTS staff_idempotency (
  key_hash TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES service_requests(id),
  actor TEXT NOT NULL, action TEXT NOT NULL, payload_hash TEXT NOT NULL,
  committed_status TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS staff_idempotency_request ON staff_idempotency(request_id);
-- Durable capability receipt for autonomous writes. The business request
-- remains the authority; this row records which canonical service capability
-- was authorized and under which policy version.
CREATE TABLE IF NOT EXISTS autonomous_action_receipts (
  request_id TEXT PRIMARY KEY REFERENCES service_requests(id),
  proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),
  property_id TEXT NOT NULL,
  service_code TEXT NOT NULL, request_kind TEXT NOT NULL,
  authority_level TEXT NOT NULL CHECK(authority_level='safe_write'),
  policy_version INTEGER NOT NULL CHECK(policy_version > 0),
  policy_reason TEXT NOT NULL DEFAULT '', action_nonce_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS autonomous_action_receipts_property
  ON autonomous_action_receipts(property_id,created_at);
CREATE TABLE IF NOT EXISTS metric_counts (
  day TEXT NOT NULL, language TEXT NOT NULL, metric TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(day, language, metric)
);
CREATE TABLE IF NOT EXISTS telemetry_receipts (
  event_id TEXT PRIMARY KEY, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS telemetry_receipts_expiry ON telemetry_receipts(expires_at);
-- non-authoritative AgentState checkpoint. It contains only bounded goal
-- tags/public-status facts and can never authorize a tool or business write.
CREATE TABLE IF NOT EXISTS agent_checkpoints (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  property_id TEXT NOT NULL, language TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision > 0),
  state_json TEXT NOT NULL CHECK(length(state_json) <= 4000),
  expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_checkpoints_expiry ON agent_checkpoints(expires_at);
-- session-scoped semantic memory. This is separate from workflow state and
-- from the hotel knowledge base. Only verified public tool facts may be written.
CREATE TABLE IF NOT EXISTS agent_memory_facts (
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  property_id TEXT NOT NULL, language TEXT NOT NULL,
  fact_key TEXT NOT NULL, fact_type TEXT NOT NULL CHECK(fact_type IN ('evidence_summary','verified_route')),
  value_json TEXT NOT NULL CHECK(length(value_json) <= 700),
  provenance_json TEXT NOT NULL CHECK(length(provenance_json) <= 900),
  confidence TEXT NOT NULL CHECK(confidence='verified'),
  sensitivity TEXT NOT NULL CHECK(sensitivity='public'),
  observed_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  PRIMARY KEY(session_id,fact_key)
);
CREATE INDEX IF NOT EXISTS agent_memory_facts_expiry ON agent_memory_facts(expires_at);
CREATE INDEX IF NOT EXISTS agent_memory_facts_scope ON agent_memory_facts(property_id,session_id,language,expires_at);
-- bounded preference memory. It stores only a small allow-list of explicit
-- operational guest preferences for the active kiosk session, never transcripts.
CREATE TABLE IF NOT EXISTS agent_session_preferences (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  property_id TEXT NOT NULL, preferences_json TEXT NOT NULL CHECK(length(preferences_json) <= 320),
  expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_session_preferences_expiry ON agent_session_preferences(expires_at);
CREATE INDEX IF NOT EXISTS agent_session_preferences_scope ON agent_session_preferences(property_id,session_id,expires_at);
CREATE TABLE IF NOT EXISTS knowledge_releases (
  property_id TEXT PRIMARY KEY, release_version INTEGER NOT NULL,
  bundle_sha256 TEXT NOT NULL, chunk_policy_hash TEXT NOT NULL DEFAULT '',
  applied_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_release_evidence (
  property_id TEXT PRIMARY KEY, release_version INTEGER NOT NULL,
  bundle_sha256 TEXT NOT NULL, chunk_policy_hash TEXT NOT NULL,
  embedding_model_id TEXT NOT NULL, document_count INTEGER NOT NULL CHECK(document_count > 0),
  chunk_count INTEGER NOT NULL CHECK(chunk_count > 0),
  public_language_count INTEGER NOT NULL CHECK(public_language_count >= 0),
  domain_count INTEGER NOT NULL CHECK(domain_count > 0),
  quality_status TEXT NOT NULL CHECK(quality_status='validated'),
  created_at INTEGER NOT NULL
);
"""


# Schema upgrades are transactional and monotonic. Never silently open a newer DB.
SCHEMA_VERSION = 23


# SQLite triggers are defense in depth: the workflow remains responsible for
# authorization, while persistence forbids cross-property/snapshot corruption.
def _sql_text_list(values: frozenset[str]) -> str:
    # Domain schema restricts identifiers to lowercase [a-z0-9_], but quote
    # defensively because this string becomes part of a SQLite trigger body.
    return ','.join("'" + value.replace("'", "''") + "'" for value in sorted(values))


_REVIEW_REQUIRED_KINDS_SQL = _sql_text_list(VERIFICATION_KINDS)

GUARDS = (
    """CREATE TRIGGER IF NOT EXISTS audit_property_guard BEFORE INSERT ON audit_events
    WHEN NOT EXISTS (SELECT 1 FROM service_requests r WHERE r.id=NEW.request_id
                     AND r.property_id=NEW.property_id)
    BEGIN SELECT RAISE(ABORT, 'audit/request property mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS proposal_property_guard BEFORE INSERT ON proposals
    WHEN NOT EXISTS (SELECT 1 FROM sessions s WHERE s.id=NEW.session_id
                     AND s.property_id=NEW.property_id)
    BEGIN SELECT RAISE(ABORT, 'proposal/session property mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS proposal_identity_guard BEFORE UPDATE ON proposals
    WHEN NEW.id!=OLD.id OR NEW.property_id!=OLD.property_id
      OR NEW.session_id!=OLD.session_id OR NEW.client_nonce!=OLD.client_nonce
      OR NEW.kind!=OLD.kind OR NEW.language!=OLD.language OR NEW.details!=OLD.details
      OR NEW.payload_json!=OLD.payload_json
    BEGIN SELECT RAISE(ABORT, 'proposal identity is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS proposal_state_guard BEFORE UPDATE OF status ON proposals
    WHEN NEW.status!=OLD.status AND NOT (
      (OLD.status='awaiting_confirmation' AND NEW.status IN ('confirmed','cancelled','expired')))
    BEGIN SELECT RAISE(ABORT, 'illegal proposal transition'); END""",
    """CREATE TRIGGER IF NOT EXISTS request_insert_state_guard BEFORE INSERT ON service_requests
    WHEN NEW.status!='pending_staff' OR NEW.created_at>NEW.updated_at
    BEGIN SELECT RAISE(ABORT, 'request must start pending staff'); END""",
    """CREATE TRIGGER IF NOT EXISTS request_proposal_guard BEFORE INSERT ON service_requests
    WHEN NOT EXISTS (SELECT 1 FROM proposals p WHERE p.id=NEW.proposal_id
       AND p.property_id=NEW.property_id AND p.kind=NEW.kind
       AND p.language=NEW.language AND p.details=NEW.details
       AND p.payload_json=NEW.payload_json AND p.status='confirmed')
    BEGIN SELECT RAISE(ABORT, 'request must match a confirmed proposal'); END""",
    """CREATE TRIGGER IF NOT EXISTS request_immutable_guard BEFORE UPDATE ON service_requests
    WHEN NEW.id!=OLD.id OR NEW.proposal_id!=OLD.proposal_id
       OR NEW.property_id!=OLD.property_id OR NEW.kind!=OLD.kind
       OR NEW.language!=OLD.language OR NEW.details!=OLD.details
       OR NEW.payload_json!=OLD.payload_json OR NEW.created_at!=OLD.created_at
    BEGIN SELECT RAISE(ABORT, 'request identity and guest details are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS request_state_guard BEFORE UPDATE OF status ON service_requests
    WHEN NEW.status!=OLD.status AND NOT (
      (OLD.status='pending_staff' AND NEW.status IN ('approved','rejected')) OR
      (OLD.status='approved' AND NEW.status IN ('in_progress','completed')) OR
      (OLD.status='in_progress' AND NEW.status IN ('paused','completed')) OR
      (OLD.status='paused' AND NEW.status IN ('in_progress','completed')))
    BEGIN SELECT RAISE(ABORT, 'illegal service request transition'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS request_review_guard BEFORE UPDATE OF status ON service_requests
    WHEN (NEW.status='approved' AND OLD.status='pending_staff'
       AND NEW.kind IN ({_REVIEW_REQUIRED_KINDS_SQL})
       AND NOT EXISTS (SELECT 1 FROM autonomous_action_receipts a WHERE a.request_id=NEW.id AND a.property_id=NEW.property_id)
       AND (length(trim(NEW.verified_by))=0 OR length(trim(NEW.staff_note))<8))
       OR (NEW.status IN ('rejected','completed') AND NEW.status!=OLD.status
       AND length(trim(NEW.staff_note))<8)
    BEGIN SELECT RAISE(ABORT, 'review evidence required'); END""",  # nosec B608  # kinds list is a module constant
    """CREATE TRIGGER IF NOT EXISTS request_time_guard BEFORE UPDATE ON service_requests
    WHEN NEW.updated_at<OLD.updated_at OR NEW.updated_at<NEW.created_at
    BEGIN SELECT RAISE(ABORT, 'request time cannot go backwards'); END""",
    """CREATE TRIGGER IF NOT EXISTS emergency_state_guard BEFORE UPDATE OF status ON emergency_alerts
    WHEN NEW.status!=OLD.status AND NOT (
      (OLD.status='open' AND NEW.status IN ('acknowledged','resolved')) OR
      (OLD.status='acknowledged' AND NEW.status='resolved'))
    BEGIN SELECT RAISE(ABORT, 'illegal emergency alert transition'); END""",
    """CREATE TRIGGER IF NOT EXISTS emergency_resolution_guard BEFORE UPDATE OF status ON emergency_alerts
    WHEN NEW.status='resolved' AND NEW.status!=OLD.status AND length(trim(NEW.resolution_note))<8
    BEGIN SELECT RAISE(ABORT, 'emergency resolution note required'); END""",
    """CREATE TRIGGER IF NOT EXISTS autonomous_receipt_request_guard BEFORE INSERT ON autonomous_action_receipts
    WHEN NOT EXISTS (SELECT 1 FROM service_requests r WHERE r.id=NEW.request_id
      AND r.proposal_id=NEW.proposal_id AND r.property_id=NEW.property_id
      AND r.kind=NEW.request_kind)
    BEGIN SELECT RAISE(ABORT, 'autonomous receipt/request mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS autonomous_receipt_immutable_guard BEFORE UPDATE ON autonomous_action_receipts
    BEGIN SELECT RAISE(ABORT, 'autonomous action receipt is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS agent_memory_property_guard BEFORE INSERT ON agent_memory_facts
    WHEN NOT EXISTS (SELECT 1 FROM sessions s WHERE s.id=NEW.session_id
                     AND s.property_id=NEW.property_id)
    BEGIN SELECT RAISE(ABORT, 'agent memory/session property mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS agent_memory_identity_guard BEFORE UPDATE ON agent_memory_facts
    WHEN NEW.session_id!=OLD.session_id OR NEW.property_id!=OLD.property_id
      OR NEW.language!=OLD.language OR NEW.fact_key!=OLD.fact_key
      OR NEW.fact_type!=OLD.fact_type OR NEW.confidence!='verified'
      OR NEW.sensitivity!='public'
    BEGIN SELECT RAISE(ABORT, 'agent memory identity/policy fields are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS agent_preferences_property_guard BEFORE INSERT ON agent_session_preferences
    WHEN NOT EXISTS (SELECT 1 FROM sessions s WHERE s.id=NEW.session_id
                     AND s.property_id=NEW.property_id)
    BEGIN SELECT RAISE(ABORT, 'agent preferences/session property mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS agent_preferences_identity_guard BEFORE UPDATE ON agent_session_preferences
    WHEN NEW.session_id!=OLD.session_id OR NEW.property_id!=OLD.property_id
    BEGIN SELECT RAISE(ABORT, 'agent preferences identity is immutable'); END""",
)
