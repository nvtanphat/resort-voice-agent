"""One-off, transactional schema upgrades applied before the DDL script runs."""
from __future__ import annotations

import sqlite3


def migrate_remove_read_task_projections(con: sqlite3.Connection) -> None:
    """Remove the obsolete non-authoritative read-task projection table."""
    con.execute('DROP TABLE IF EXISTS read_task_projections')


def migrate_service_request_status_constraint(con: sqlite3.Connection) -> None:
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
              details TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', confirmation_code TEXT NOT NULL DEFAULT '', service_code TEXT NOT NULL DEFAULT '',
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
        target = ('id,proposal_id,property_id,kind,language,details,payload_json,confirmation_code,service_code,status,created_at,updated_at,'
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
            elif name == 'confirmation_code':
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
