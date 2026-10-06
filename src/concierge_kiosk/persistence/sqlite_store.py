"""Local ACID persistence. WAL and BEGIN IMMEDIATE serialize sensitive transitions."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .constants import SQLITE_BUSY_TIMEOUT_MS
from .migrations import migrate_service_request_status_constraint
from .schema import GUARDS, SCHEMA, SCHEMA_VERSION


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
            migrate_service_request_status_constraint(con)
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
            if 'confirmation_code' not in request_fields:
                con.execute("ALTER TABLE service_requests ADD COLUMN confirmation_code TEXT NOT NULL DEFAULT ''")
            # Backfill a guest-safe reference for rows created before the
            # public status projection existed.  It is derived solely from
            # the already-random request id and contains no PII.
            from concierge_kiosk.domain.public_reference import public_reference
            for legacy in con.execute("SELECT id FROM service_requests WHERE confirmation_code='' ").fetchall():
                con.execute("UPDATE service_requests SET confirmation_code=? WHERE id=?",
                            (public_reference(legacy['id']), legacy['id']))
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS service_requests_public_reference "
                        "ON service_requests(property_id,confirmation_code) WHERE confirmation_code!=''")
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
            con.execute('DELETE FROM guest_consents WHERE expires_at < ?', (now,))
            con.execute('DELETE FROM read_task_projections WHERE expires_at <= ?', (now,))
            con.execute('DELETE FROM metric_counts WHERE day < ?',
                        (datetime.fromtimestamp(max(0, cutoff), timezone.utc).date().isoformat(),))
