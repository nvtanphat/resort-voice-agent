"""Kiosk session lifecycle operations."""
from __future__ import annotations
import secrets
import time
from .base import digest

class SessionWorkflowMixin:
    def new_session(self, ttl: int = 1200) -> tuple[str, str, str]:
        session_id, token, csrf, _, _ = self.rotate_session('', ttl)
        return session_id, token, csrf

    def rotate_session(self, previous_token: str, ttl: int = 1200) -> tuple[str, str, str, str | None, list[str]]:
        """Replace an existing browser session in one SQLite transaction.

        A reload/new-session action on a *shared* kiosk must not leave the old
        cookie actionable for its remaining TTL. Revocation and draft cancellation
        commit together; an already submitted staff ticket remains untouched.
        Graph/voice cleanup happens afterwards and cannot reverse this commit.
        """
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        session_id, now = secrets.token_hex(16), int(time.time())
        previous_id: str | None = None
        abandoned: list[str] = []
        with self.store.connection(write=True) as con:
            if previous_token:
                old = con.execute(
                    'SELECT id FROM sessions WHERE token_hash=? AND property_id=?',
                    (digest(previous_token), self.property_id),
                ).fetchone()
                if old is not None:
                    previous_id = old['id']
                    abandoned = [row['id'] for row in con.execute(
                        "SELECT id FROM proposals WHERE session_id=? AND status='awaiting_confirmation'",
                        (previous_id,),
                    )]
                    con.execute('UPDATE sessions SET expires_at=0 WHERE id=? AND property_id=?',
                                (previous_id, self.property_id))
                    con.execute("UPDATE proposals SET status='cancelled' WHERE session_id=? "
                                "AND status='awaiting_confirmation'", (previous_id,))
                    # Shared-kiosk privacy boundary: conversation-derived durable
                    # projections die with the browser session. Submitted staff
                    # tickets/audit records are intentionally preserved.
                    con.execute('DELETE FROM agent_checkpoints WHERE session_id=? AND property_id=?',
                                (previous_id, self.property_id))
                    con.execute('DELETE FROM agent_memory_facts WHERE session_id=? AND property_id=?',
                                (previous_id, self.property_id))
                    con.execute('DELETE FROM agent_session_preferences WHERE session_id=? AND property_id=?',
                                (previous_id, self.property_id))
                    con.execute('DELETE FROM read_task_projections WHERE session_id=? AND property_id=?',
                                (previous_id, self.property_id))
            con.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)",
                        (session_id, digest(token), digest(csrf), self.property_id, now + ttl, now))
        return session_id, token, csrf, previous_id, abandoned

    def session_for(self, token: str, csrf: str) -> str:
        if not token or not csrf:
            raise PermissionError("Missing session/CSRF")
        with self.store.connection() as con:
            row = con.execute("SELECT id, csrf_hash FROM sessions WHERE token_hash=? AND property_id=? "
                              "AND expires_at>?", (digest(token), self.property_id, int(time.time()))).fetchone()
        if row is None or not secrets.compare_digest(row["csrf_hash"], digest(csrf)):
            raise PermissionError("Invalid or expired kiosk session")
        return row["id"]

    def end_session(self, token: str, csrf: str) -> None:
        session_id = self.session_for(token, csrf)
        with self.store.connection(write=True) as con:
            con.execute("UPDATE sessions SET expires_at=0 WHERE id=? AND property_id=?",
                        (session_id, self.property_id))
            con.execute("UPDATE proposals SET status='cancelled' WHERE session_id=? "
                        "AND status='awaiting_confirmation'", (session_id,))
            con.execute('DELETE FROM agent_checkpoints WHERE session_id=? AND property_id=?',
                        (session_id, self.property_id))
            con.execute('DELETE FROM agent_memory_facts WHERE session_id=? AND property_id=?',
                        (session_id, self.property_id))
            con.execute('DELETE FROM agent_session_preferences WHERE session_id=? AND property_id=?',
                        (session_id, self.property_id))
            con.execute('DELETE FROM read_task_projections WHERE session_id=? AND property_id=?',
                        (session_id, self.property_id))

