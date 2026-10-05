"""Local ACID persistence. WAL and BEGIN IMMEDIATE serialize sensitive transitions."""
from __future__ import annotations

import sqlite3
import os
import hashlib
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from concierge_kiosk.domain.service_registry import VERIFICATION_KINDS
from .constants import SQLITE_BUSY_TIMEOUT_MS

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, csrf_hash TEXT NOT NULL,
  property_id TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
);
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
-- Status-only diagnostic projection. NEVER a task queue or transaction authority.
CREATE TABLE IF NOT EXISTS read_task_projections (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  property_id TEXT NOT NULL, language TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision > 0),
  tasks_json TEXT NOT NULL CHECK(length(tasks_json) <= 700),
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS read_task_projections_expiry ON read_task_projections(expires_at);
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
SCHEMA_VERSION = 21

# SQLite triggers are defense in depth: the workflow remains responsible for
# authorization, while persistence forbids cross-property/snapshot corruption.


def _sql_text_list(values: frozenset[str]) -> str:
    # Domain schema restricts identifiers to lowercase [a-z0-9_], but quote
    # defensively because this string becomes part of a SQLite trigger body.
    return ','.join("'" + value.replace("'", "''") + "'" for value in sorted(values))


def _migrate_service_request_status_constraint(con: sqlite3.Connection) -> None:
    """Rebuild v19 request storage so the DB itself accepts work states.

    SQLite cannot alter a CHECK constraint in place.  The migration is kept
    explicit and column-driven so older edge databases retain their evidence;
    no guest row is silently discarded.  Foreign keys are disabled only for
    this bounded table swap and are re-enabled before normal startup checks.
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='service_requests'"
    ).fetchone()
    table_sql = str(row[0] if row else '')
    if not table_sql or 'in_progress' in table_sql:
        return
    fields = {item[1] for item in con.execute('PRAGMA table_info(service_requests)')}
    con.execute('PRAGMA foreign_keys=OFF')
    con.execute('BEGIN IMMEDIATE')
    try:
        for trigger in ('audit_property_guard', 'request_insert_state_guard',
                        'request_proposal_guard', 'request_immutable_guard',
                        'request_state_guard', 'request_review_guard', 'request_time_guard',
                        'autonomous_receipt_request_guard', 'emergency_state_guard',
                        'emergency_resolution_guard'):
            con.execute(f'DROP TRIGGER IF EXISTS {trigger}')
        for index in ('service_requests_queue', 'requests_updated_status', 'requests_proposal_property'):
            con.execute(f'DROP INDEX IF EXISTS {index}')
        con.execute('DROP TABLE IF EXISTS service_requests_new')
        con.execute("""
            CREATE TABLE service_requests_new (
              id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),
              property_id TEXT NOT NULL, kind TEXT NOT NULL, language TEXT NOT NULL,
              details TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', service_code TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL CHECK(status IN ('pending_staff','approved','in_progress','paused','rejected','completed')),
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
              staff_note TEXT NOT NULL DEFAULT '', verified_by TEXT NOT NULL DEFAULT '',
              guest_change_state TEXT NOT NULL DEFAULT 'none', guest_change_payload_json TEXT NOT NULL DEFAULT '{}',
              guest_change_note TEXT NOT NULL DEFAULT '', guest_change_nonce_hash TEXT NOT NULL DEFAULT '',
              guest_change_updated_at INTEGER NOT NULL DEFAULT 0,
              guest_verification_state TEXT NOT NULL DEFAULT 'staff_required', guest_verification_provider TEXT NOT NULL DEFAULT '',
              guest_verification_reference TEXT NOT NULL DEFAULT '', eta_minutes INTEGER, eta_updated_at INTEGER NOT NULL DEFAULT 0,
              external_dispatch_state TEXT NOT NULL DEFAULT 'not_requested', external_dispatch_provider TEXT NOT NULL DEFAULT '',
              external_reference TEXT NOT NULL DEFAULT '', external_dispatch_error TEXT NOT NULL DEFAULT '',
              department_id TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
              ack_due_at INTEGER NOT NULL DEFAULT 0, ack_overdue INTEGER NOT NULL DEFAULT 0 CHECK(ack_overdue IN (0,1)),
              ack_escalation_sent_at INTEGER NOT NULL DEFAULT 0, sla_due_at INTEGER NOT NULL DEFAULT 0,
              overdue INTEGER NOT NULL DEFAULT 0 CHECK(overdue IN (0,1)), escalation_sent_at INTEGER NOT NULL DEFAULT 0,
              escalation_level INTEGER NOT NULL DEFAULT 0 CHECK(escalation_level BETWEEN 0 AND 2),
              assignee TEXT NOT NULL DEFAULT '', assigned_at INTEGER NOT NULL DEFAULT 0,
              started_at INTEGER NOT NULL DEFAULT 0, paused_at INTEGER NOT NULL DEFAULT 0, completed_at INTEGER NOT NULL DEFAULT 0,
              unverified_room INTEGER NOT NULL DEFAULT 0 CHECK(unverified_room IN (0,1))
            )
        """)
        target = ('id,proposal_id,property_id,kind,language,details,payload_json,service_code,status,created_at,updated_at,'
                  'staff_note,verified_by,guest_change_state,guest_change_payload_json,guest_change_note,guest_change_nonce_hash,'
                  'guest_change_updated_at,guest_verification_state,guest_verification_provider,guest_verification_reference,'
                  'eta_minutes,eta_updated_at,external_dispatch_state,external_dispatch_provider,external_reference,external_dispatch_error,'
                  'department_id,priority,ack_due_at,ack_overdue,ack_escalation_sent_at,sla_due_at,overdue,escalation_sent_at,'
                  'escalation_level,assignee,assigned_at,started_at,paused_at,completed_at,unverified_room')
        source = []
        for name in target.split(','):
            name = name.strip()
            if name == 'ack_due_at':
                source.append("CASE WHEN status='pending_staff' THEN created_at ELSE 0 END")
            elif name == 'sla_due_at' and name in fields:
                source.append("CASE WHEN status='pending_staff' THEN 0 ELSE sla_due_at END")
            elif name in fields:
                source.append(name)
            elif name == 'service_code':
                source.append("''")
            elif name == 'priority':
                source.append('3')
            elif name in {'ack_overdue', 'ack_escalation_sent_at', 'escalation_level',
                          'assignee', 'assigned_at', 'started_at', 'paused_at', 'completed_at'}:
                source.append('0' if name not in {'assignee'} else "''")
            else:
                source.append("''" if name in {'staff_note','verified_by','guest_change_state',
                                                'guest_change_payload_json','guest_change_note','guest_change_nonce_hash',
                                                'guest_verification_state','guest_verification_provider','guest_verification_reference',
                                                'external_dispatch_state','external_dispatch_provider','external_reference','external_dispatch_error',
                                                'department_id'} else 'NULL')
        con.execute(f'INSERT INTO service_requests_new({target}) SELECT {", ".join(source)} FROM service_requests')  # nosec B608  # migration columns/expressions are constructed from fixed internal names
        con.execute('DROP TABLE service_requests')
        con.execute('ALTER TABLE service_requests_new RENAME TO service_requests')
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.execute('PRAGMA foreign_keys=ON')


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


class Store:
    _METRIC_FLUSH_BATCH = 32
    _METRIC_FLUSH_INTERVAL_SECONDS = 2.0

    def __init__(self, path: Path):
        self.path = Path(path)
        self._metric_lock = threading.Lock()
        self._metric_buffer: dict[tuple[str, str, str], int] = {}
        self._metric_buffered = 0
        self._metric_last_flush = time.monotonic()
        if self.path.is_symlink():
            raise ValueError('Refusing to use a symlink as an edge database')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Re-protect legacy owner-owned files *before* opening/migrating them.
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            os.chmod(self.path, 0o600)
        is_new = not self.path.exists()
        if is_new:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        con = self._connect()
        try:
            # WAL is configured before BEGIN. executescript commits an existing
            # transaction, so the BEGIN is deliberately part of this one script.
            # Preflight BEFORE executing any DDL: a newer schema must remain
            # byte-for-byte untouched rather than gaining this release's tables.
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError('Database schema is newer than this application')
            con.execute('PRAGMA journal_mode=WAL')
            _migrate_service_request_status_constraint(con)
            con.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
            # explicit semantic parent and hotel-domain metadata. These
            # Additive columns allow upgrades from older schemas without a table rewrite.
            knowledge_fields = {row[1] for row in con.execute('PRAGMA table_info(knowledge)')}
            if 'domain' not in knowledge_fields:
                con.execute("ALTER TABLE knowledge ADD COLUMN domain TEXT NOT NULL DEFAULT 'general'")
            if 'parent_id' not in knowledge_fields:
                con.execute("ALTER TABLE knowledge ADD COLUMN parent_id TEXT NOT NULL DEFAULT ''")
            if 'section_id' not in knowledge_fields:
                con.execute("ALTER TABLE knowledge ADD COLUMN section_id TEXT NOT NULL DEFAULT ''")
            if 'section_ordinal' not in knowledge_fields:
                con.execute("ALTER TABLE knowledge ADD COLUMN section_ordinal INTEGER NOT NULL DEFAULT 0")
            for field, statement in {
                'entity_id': "ALTER TABLE knowledge ADD COLUMN entity_id TEXT NOT NULL DEFAULT ''",
                'fact_type': "ALTER TABLE knowledge ADD COLUMN fact_type TEXT NOT NULL DEFAULT ''",
                'fact_context': "ALTER TABLE knowledge ADD COLUMN fact_context TEXT NOT NULL DEFAULT ''",
                'canonical_fact_id': "ALTER TABLE knowledge ADD COLUMN canonical_fact_id TEXT NOT NULL DEFAULT ''",
                'context_text': "ALTER TABLE knowledge ADD COLUMN context_text TEXT NOT NULL DEFAULT ''",
                'metadata_json': "ALTER TABLE knowledge ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if field not in knowledge_fields:
                    con.execute(statement)
            parent_fields = {row[1] for row in con.execute('PRAGMA table_info(knowledge_parents)')}
            if 'section_id' not in parent_fields:
                con.execute("ALTER TABLE knowledge_parents ADD COLUMN section_id TEXT NOT NULL DEFAULT ''")
            if 'section_ordinal' not in parent_fields:
                con.execute("ALTER TABLE knowledge_parents ADD COLUMN section_ordinal INTEGER NOT NULL DEFAULT 0")
            fields = {row[1] for row in con.execute('PRAGMA table_info(service_requests)')}
            if 'staff_note' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN staff_note TEXT NOT NULL DEFAULT ''")
            if 'verified_by' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN verified_by TEXT NOT NULL DEFAULT ''")
            if 'guest_change_state' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN guest_change_state TEXT NOT NULL DEFAULT 'none'")
            if 'guest_change_payload_json' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN guest_change_payload_json TEXT NOT NULL DEFAULT '{}'")
            if 'guest_change_note' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN guest_change_note TEXT NOT NULL DEFAULT ''")
            if 'guest_change_nonce_hash' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN guest_change_nonce_hash TEXT NOT NULL DEFAULT ''")
            if 'guest_change_updated_at' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN guest_change_updated_at INTEGER NOT NULL DEFAULT 0")
            if 'guest_verification_state' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN guest_verification_state TEXT NOT NULL DEFAULT 'staff_required'")
            if 'guest_verification_provider' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN guest_verification_provider TEXT NOT NULL DEFAULT ''")
            if 'guest_verification_reference' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN guest_verification_reference TEXT NOT NULL DEFAULT ''")
            if 'eta_minutes' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN eta_minutes INTEGER")
            if 'eta_updated_at' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN eta_updated_at INTEGER NOT NULL DEFAULT 0")
            if 'external_dispatch_state' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN external_dispatch_state TEXT NOT NULL DEFAULT 'not_requested'")
            if 'external_dispatch_provider' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN external_dispatch_provider TEXT NOT NULL DEFAULT ''")
            if 'external_reference' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN external_reference TEXT NOT NULL DEFAULT ''")
            if 'external_dispatch_error' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN external_dispatch_error TEXT NOT NULL DEFAULT ''")
            if 'department_id' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN department_id TEXT NOT NULL DEFAULT ''")
            if 'sla_due_at' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN sla_due_at INTEGER NOT NULL DEFAULT 0")
            if 'overdue' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN overdue INTEGER NOT NULL DEFAULT 0")
            if 'escalation_sent_at' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN escalation_sent_at INTEGER NOT NULL DEFAULT 0")
            if 'unverified_room' not in fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN unverified_room INTEGER NOT NULL DEFAULT 0")
            audit_fields = {row[1] for row in con.execute('PRAGMA table_info(audit_events)')}
            if 'note' not in audit_fields:
                con.execute("ALTER TABLE audit_events ADD COLUMN note TEXT NOT NULL DEFAULT ''")
            if 'independently_verified' not in audit_fields:
                con.execute('ALTER TABLE audit_events ADD COLUMN independently_verified INTEGER NOT NULL DEFAULT 0')
            release_fields = {row[1] for row in con.execute('PRAGMA table_info(knowledge_releases)')}
            if 'chunk_policy_hash' not in release_fields:
                con.execute("ALTER TABLE knowledge_releases ADD COLUMN chunk_policy_hash TEXT NOT NULL DEFAULT ''")
            rate_fields = {row[1] for row in con.execute('PRAGMA table_info(rate_limits)')}
            if 'window_seconds' not in rate_fields:
                con.execute('ALTER TABLE rate_limits ADD COLUMN window_seconds INTEGER NOT NULL DEFAULT 60')
            proposal_fields = {row[1] for row in con.execute('PRAGMA table_info(proposals)')}
            if 'payload_json' not in proposal_fields:
                con.execute("ALTER TABLE proposals ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
            request_fields = {row[1] for row in con.execute('PRAGMA table_info(service_requests)')}
            if 'payload_json' not in request_fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
            request_fields = {row[1] for row in con.execute('PRAGMA table_info(service_requests)')}
            additive_request_fields = {
                'service_code': "ALTER TABLE service_requests ADD COLUMN service_code TEXT NOT NULL DEFAULT ''",
                'priority': "ALTER TABLE service_requests ADD COLUMN priority INTEGER NOT NULL DEFAULT 3",
                'ack_due_at': "ALTER TABLE service_requests ADD COLUMN ack_due_at INTEGER NOT NULL DEFAULT 0",
                'ack_overdue': "ALTER TABLE service_requests ADD COLUMN ack_overdue INTEGER NOT NULL DEFAULT 0",
                'ack_escalation_sent_at': "ALTER TABLE service_requests ADD COLUMN ack_escalation_sent_at INTEGER NOT NULL DEFAULT 0",
                'escalation_level': "ALTER TABLE service_requests ADD COLUMN escalation_level INTEGER NOT NULL DEFAULT 0",
                'assignee': "ALTER TABLE service_requests ADD COLUMN assignee TEXT NOT NULL DEFAULT ''",
                'assigned_at': "ALTER TABLE service_requests ADD COLUMN assigned_at INTEGER NOT NULL DEFAULT 0",
                'started_at': "ALTER TABLE service_requests ADD COLUMN started_at INTEGER NOT NULL DEFAULT 0",
                'paused_at': "ALTER TABLE service_requests ADD COLUMN paused_at INTEGER NOT NULL DEFAULT 0",
                'completed_at': "ALTER TABLE service_requests ADD COLUMN completed_at INTEGER NOT NULL DEFAULT 0",
            }
            for field, statement in additive_request_fields.items():
                if field not in request_fields:
                    con.execute(statement)
            emergency_fields = {row[1] for row in con.execute('PRAGMA table_info(emergency_alerts)')}
            additive_emergency_fields = {
                'kiosk_location': "ALTER TABLE emergency_alerts ADD COLUMN kiosk_location TEXT NOT NULL DEFAULT ''",
                'escalation_due_at': "ALTER TABLE emergency_alerts ADD COLUMN escalation_due_at INTEGER NOT NULL DEFAULT 0",
                'escalation_level': "ALTER TABLE emergency_alerts ADD COLUMN escalation_level INTEGER NOT NULL DEFAULT 0",
                'escalated_at': "ALTER TABLE emergency_alerts ADD COLUMN escalated_at INTEGER NOT NULL DEFAULT 0",
            }
            for field, statement in additive_emergency_fields.items():
                if field not in emergency_fields:
                    con.execute(statement)
            if version < 17:
                # Preserve Vietnamese diacritics in the primary FTS channel.
                # Legacy rows stored an accent-folded search_text; prefix the
                # original approved title/heading/body so upgraded databases gain
                # exact accented tokens while retaining the old no-accent fallback.
                con.execute('DROP TABLE IF EXISTS knowledge_fts')
                con.execute("CREATE VIRTUAL TABLE knowledge_fts USING fts5("
                            "doc_id UNINDEXED, revision UNINDEXED, search_text, "
                            "tokenize='unicode61 remove_diacritics 0')")
                rows = con.execute('SELECT id,revision,title,heading,body,search_text FROM knowledge').fetchall()
                for row in rows:
                    search_text = f"{row['title']} {row['heading']} {row['body']} {row['search_text']}".strip()
                    con.execute('UPDATE knowledge SET search_text=? WHERE id=? AND revision=?',
                                (search_text, row['id'], row['revision']))
                    con.execute('INSERT INTO knowledge_fts(doc_id,revision,search_text) VALUES(?,?,?)',
                                (row['id'], row['revision'], search_text))
            if version < 21:
                # Context text is part of the retrieval surface. Rebuild FTS for
                # upgraded databases so an old index cannot silently omit it.
                con.execute('DROP TABLE IF EXISTS knowledge_fts')
                con.execute("CREATE VIRTUAL TABLE knowledge_fts USING fts5("
                            "doc_id UNINDEXED, revision UNINDEXED, search_text, "
                            "tokenize='unicode61 remove_diacritics 0')")
                rows = con.execute('SELECT id,revision,title,heading,body,context_text,search_text FROM knowledge').fetchall()
                for row in rows:
                    search_text = f"{row['title']} {row['heading']} {row['context_text']} {row['body']} {row['search_text']}".strip()
                    con.execute('UPDATE knowledge SET search_text=? WHERE id=? AND revision=?',
                                (search_text, row['id'], row['revision']))
                    con.execute('INSERT INTO knowledge_fts(doc_id,revision,search_text) VALUES(?,?,?)',
                                (row['id'], row['revision'], search_text))
            if version < 10:
                # Guard SQL changed in to include immutable structured payloads.
                # SQLite CREATE TRIGGER IF NOT EXISTS would otherwise keep the old
                # trigger body forever on upgraded installations.
                for trigger in (
                    'audit_property_guard', 'proposal_property_guard', 'proposal_identity_guard',
                    'proposal_state_guard', 'request_insert_state_guard', 'request_proposal_guard',
                    'request_immutable_guard', 'request_state_guard', 'request_review_guard',
                    'request_time_guard',
                ):
                    con.execute(f'DROP TRIGGER IF EXISTS {trigger}')
            con.execute('CREATE INDEX IF NOT EXISTS proposals_session_expiry ON proposals(session_id,status,expires_at)')
            con.execute('CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at)')
            con.execute('CREATE INDEX IF NOT EXISTS requests_updated_status ON service_requests(status,updated_at)')
            con.execute('CREATE INDEX IF NOT EXISTS requests_proposal_property ON service_requests(proposal_id,property_id)')
            con.execute('CREATE INDEX IF NOT EXISTS audit_request_order ON audit_events(request_id,id)')
            con.execute('CREATE INDEX IF NOT EXISTS audit_property_request_order ON audit_events(property_id,request_id,id)')
            # This guard embeds domain-owned request kinds, so rebuild it on every
            # startup. A profile change must not leave stale policy in SQLite.
            con.execute('DROP TRIGGER IF EXISTS request_review_guard')
            con.execute('DROP TRIGGER IF EXISTS request_state_guard')
            con.execute('DROP TRIGGER IF EXISTS request_insert_state_guard')
            con.execute('DROP TRIGGER IF EXISTS request_proposal_guard')
            con.execute('DROP TRIGGER IF EXISTS request_immutable_guard')
            con.execute('DROP TRIGGER IF EXISTS request_time_guard')
            con.execute('DROP TRIGGER IF EXISTS audit_property_guard')
            con.execute('DROP TRIGGER IF EXISTS emergency_state_guard')
            con.execute('DROP TRIGGER IF EXISTS emergency_resolution_guard')
            for guard in GUARDS:
                con.execute(guard)
            # Existing installations with invalid links must be repaired, never
            # implicitly marked healthy by an upgrade.
            if con.execute('PRAGMA foreign_key_check').fetchone():
                raise sqlite3.IntegrityError('Existing database has broken foreign keys')
            if con.execute(
                'SELECT 1 FROM audit_events a JOIN service_requests r ON r.id=a.request_id '
                'WHERE a.property_id!=r.property_id LIMIT 1'
            ).fetchone():
                raise sqlite3.IntegrityError('Existing audit contains cross-property records')
            con.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()
        if is_new:
            os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
                              isolation_level=None,
                              check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute(f'PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}')
        con.execute('PRAGMA foreign_keys=ON')
        # Prefer durability over speed for the local, business-authoritative DB.
        con.execute('PRAGMA synchronous=FULL')
        return con

    @contextmanager
    def connection(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            if write:
                con.execute('BEGIN IMMEDIATE')
            yield con
            if write:
                con.commit()
        except BaseException:
            if write:
                con.rollback()
            raise
        finally:
            con.close()

    def check_integrity(self) -> dict:
        with self.connection() as con:
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version != SCHEMA_VERSION:
                raise sqlite3.DatabaseError('Unsupported database schema version')
            quick = con.execute('PRAGMA quick_check(1)').fetchone()[0]
            if quick != 'ok':
                raise sqlite3.DatabaseError('SQLite quick_check failed')
            if con.execute('PRAGMA foreign_key_check').fetchone():
                raise sqlite3.IntegrityError('SQLite foreign_key_check failed')
        return {'schema_version': version, 'integrity': 'ok'}

    def throttle(self, key: str, limit: int, window: int, now: int) -> bool:
        if limit < 1 or window < 1:
            raise ValueError('Invalid rate limit')
        # Do not retain raw IP addresses or session tokens in the local database.
        bucket = hashlib.sha256(f'{window}:{key}'.encode('utf-8')).hexdigest()
        with self.connection(write=True) as con:
            period = now // window
            con.execute('INSERT INTO rate_limits(bucket,period,count,window_seconds) VALUES(?,?,1,?) '
                        'ON CONFLICT(bucket,period) DO UPDATE SET count=count+1',
                        (bucket, period, window))
            count = con.execute('SELECT count FROM rate_limits WHERE bucket=? AND period=?',
                                (bucket, period)).fetchone()[0]
            return count <= limit

    def _write_metric_batch(self, pending: dict[tuple[str, str, str], int]) -> None:
        if not pending:
            return
        # Metrics are diagnostic, not business authority. WAL + NORMAL avoids a
        # FULL fsync for every observation while business transactions continue
        # to use the normal FULL-durability connection path.
        con = self._connect()
        try:
            con.execute('PRAGMA synchronous=NORMAL')
            con.execute('BEGIN IMMEDIATE')
            con.executemany(
                'INSERT INTO metric_counts(day,language,metric,count) VALUES(?,?,?,?) '
                'ON CONFLICT(day,language,metric) DO UPDATE SET count=count+excluded.count',
                [(day, language, metric, count) for (day, language, metric), count in pending.items()],
            )
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _take_metric_batch(self, *, force: bool = False) -> dict[tuple[str, str, str], int]:
        with self._metric_lock:
            elapsed = time.monotonic() - self._metric_last_flush
            if not force and self._metric_buffered < self._METRIC_FLUSH_BATCH and elapsed < self._METRIC_FLUSH_INTERVAL_SECONDS:
                return {}
            pending = self._metric_buffer
            self._metric_buffer = {}
            self._metric_buffered = 0
            self._metric_last_flush = time.monotonic()
            return pending

    def _restore_metric_batch(self, pending: dict[tuple[str, str, str], int]) -> None:
        with self._metric_lock:
            for key, count in pending.items():
                self._metric_buffer[key] = self._metric_buffer.get(key, 0) + count
                self._metric_buffered += count

    def metric(self, metric: str, language: str, now: int) -> None:
        """Buffer aggregate counters; never retain guest text or identifiers."""
        from datetime import datetime, timezone
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self._metric_lock:
            key = (day, language, metric)
            self._metric_buffer[key] = self._metric_buffer.get(key, 0) + 1
            self._metric_buffered += 1
        pending = self._take_metric_batch()
        if pending:
            try:
                self._write_metric_batch(pending)
            except BaseException:
                self._restore_metric_batch(pending)
                raise

    def flush_metrics(self) -> None:
        """Synchronously flush buffered diagnostic counters (used at shutdown/tests)."""
        pending = self._take_metric_batch(force=True)
        if not pending:
            return
        try:
            self._write_metric_batch(pending)
        except BaseException:
            self._restore_metric_batch(pending)
            raise

    def record_client_latency(self, event_id: str, language: str, stage: str,
                              duration_ms: float, now: int) -> bool:
        """One atomic receipt + histogram update; retry cannot double count.

        Receipt is an unlinkable random event nonce, never the session ID. It is
        retained for 24h to deduplicate retries and then purged.
        """
        from datetime import datetime, timezone
        from ..runtime.metrics import CLIENT_STAGES, latency_bucket
        if stage not in CLIENT_STAGES:
            raise ValueError('Unsupported telemetry stage')
        bucket = latency_bucket(stage, duration_ms)
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self.connection(write=True) as con:
            result = con.execute(
                'INSERT OR IGNORE INTO telemetry_receipts(event_id,expires_at) VALUES(?,?)',
                (event_id, now + 86400))
            if not result.rowcount:
                return False
            con.execute('INSERT INTO metric_counts(day,language,metric,count) VALUES(?,?,?,1) '
                        'ON CONFLICT(day,language,metric) DO UPDATE SET count=count+1',
                        (day, language, bucket))
            return True

    def latency_histograms(self) -> list[dict]:
        from ..runtime.metrics import aggregate_histograms
        with self.connection() as con:
            rows = [dict(r) for r in con.execute(
                'SELECT language,metric,SUM(count) count FROM metric_counts '
                "WHERE metric LIKE '%.latency_le_%' GROUP BY language,metric")]
        return aggregate_histograms(rows)

    def slm_throughput(self) -> list[dict]:
        from ..runtime.metrics import aggregate_slm_throughput
        with self.connection() as con:
            rows=[dict(r) for r in con.execute(
                'SELECT language,metric,SUM(count) count FROM metric_counts '
                "WHERE metric LIKE 'voice.slm_tokens_per_sec.le_%' GROUP BY language,metric")]
        return aggregate_slm_throughput(rows)

    def metrics(self) -> list[dict]:
        with self.connection() as con:
            return [dict(r) for r in con.execute(
                'SELECT day,language,metric,count FROM metric_counts ORDER BY day DESC,language,metric LIMIT 1000')]

    def purge(self, now: int, retention_days: int) -> None:
        if retention_days < 1:
            raise ValueError('Retention days must be positive')
        cutoff = now - retention_days * 86400
        with self.connection(write=True) as con:
            con.execute("UPDATE proposals SET status='expired' WHERE status='awaiting_confirmation' AND expires_at<=?",
                        (now,))
            # Staff receipts reference service_requests with a real FK. Remove
            # only receipts belonging to *eligible terminal* tickets first,
            # in the same transaction. Never purge receipts for active work.
            con.execute("DELETE FROM staff_idempotency WHERE request_id IN ("
                        "SELECT id FROM service_requests WHERE updated_at < ? "
                        "AND status IN ('rejected','completed'))", (cutoff,))
            con.execute("DELETE FROM autonomous_action_receipts WHERE request_id IN ("
                        "SELECT id FROM service_requests WHERE updated_at < ? "
                        "AND status IN ('rejected','completed'))", (cutoff,))
            con.execute("DELETE FROM service_requests WHERE updated_at < ? AND status IN ('rejected','completed')",
                        (cutoff,))
            con.execute('DELETE FROM proposals WHERE created_at < ? '
                        'AND id NOT IN (SELECT proposal_id FROM service_requests)', (cutoff,))
            # Proposals may remain for active staff work. Scrub expired tokens
            # without deleting the parent session required by foreign keys.
            con.execute("UPDATE sessions SET token_hash='revoked:'||lower(hex(randomblob(32))), "
                        "csrf_hash=lower(hex(randomblob(32))) WHERE expires_at<=? "
                        "AND token_hash NOT LIKE 'revoked:%'", (now,))
            con.execute('DELETE FROM sessions WHERE expires_at < ? AND id NOT IN '
                        '(SELECT session_id FROM proposals)', (cutoff,))
            con.execute('DELETE FROM audit_events WHERE at < ? AND request_id NOT IN '
                        "(SELECT id FROM service_requests WHERE status IN ('pending_staff','approved'))", (cutoff,))
            # Every rate bucket tracks the window used to create it.
            con.execute('DELETE FROM rate_limits WHERE (period+1)*window_seconds < ?', (cutoff,))
            from datetime import datetime, timezone
            con.execute('DELETE FROM telemetry_receipts WHERE expires_at < ?', (now,))
            con.execute('DELETE FROM read_task_projections WHERE expires_at <= ?', (now,))
            con.execute('DELETE FROM metric_counts WHERE day < ?',
                        (datetime.fromtimestamp(max(0, cutoff), timezone.utc).date().isoformat(),))
