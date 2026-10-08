from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from concierge_kiosk.agent.memory.conversation import ConversationMemory
from concierge_kiosk.agent.memory.preferences import SessionPreferenceMemoryStore
from concierge_kiosk.agent.memory.reference_resolver import parse_reference_choice
from concierge_kiosk.agent.memory.task_memory import AgentTaskMemory
from concierge_kiosk.agent.runtime.persistence import AgentCheckpointStore, SessionSemanticMemoryStore
from concierge_kiosk.agent.runtime.state import build_initial_state
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.agent.tools.scheduling import approved_schedule, proposed_slots
from concierge_kiosk.agent.tools.service_slots import assess_service
from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.persistence.sqlite_store import Store


class UserJourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'edge.sqlite3')
        self.workflows = Workflows(self.store, 'FURAMA_DANANG')
        self.session, self.token, self.csrf = self.workflows.new_session()

    def tearDown(self):
        self.tmp.cleanup()

    def _submitted(self, payload=None):
        proposal = self.workflows.prepare(
            self.session, 'facilities', 'vi', 'Mang thêm khăn lên phòng 305',
            'guest-create-001', payload=payload or {'room_number': '305', 'quantity': 2})
        return self.workflows.confirm(self.session, proposal['id'], True)

    def test_vietnamese_daypart_is_unambiguous(self):
        result = assess_service('Đặt bàn cho 2 người lúc 7 giờ tối', 'vi', 'dining',
                                mode='dining_reservation')
        self.assertEqual(result.slots['preferred_time'], '19:00')
        self.assertEqual(result.slots['party_size'], 2)


    def test_evening_dinner_then_spa_can_receive_source_bound_slots(self):
        release = Path(__file__).resolve().parents[2] / 'releases/planning-release.json'
        import hashlib
        schedule = approved_schedule(
            Store(Path(__file__).resolve().parents[2] / 'data/concierge.sqlite3'),
            path=str(release), expected_sha256=hashlib.sha256(release.read_bytes()).hexdigest(),
            property_id='FURAMA_DANANG', language='vi', as_of='2026-10-02')
        slots = proposed_slots(
            ('don_cipriani_dinner', 'v_senses_spa'), 1, schedule,
            preferred_window=(17 * 60, 21 * 60), as_of=__import__('datetime').date(2026, 10, 2),
            max_activities_per_day=3)
        self.assertEqual(set(slots), {'don_cipriani_dinner', 'v_senses_spa'})
        self.assertGreaterEqual(slots['don_cipriani_dinner']['suggested_time']['start'], '18:00')
        self.assertLessEqual(slots['v_senses_spa']['suggested_time']['end'], '21:00')
        self.assertFalse(slots['don_cipriani_dinner']['verified_availability'])


    def test_guest_cancel_is_staff_reviewed(self):
        row = self._submitted()
        changed = self.workflows.request_guest_change(
            self.session, row['id'], 'cancel', 'cancel-guest-001')
        self.assertEqual(changed['change_state'], 'cancel_requested')
        before = self.workflows.guest_request_progress(self.session, row['id'])
        self.assertFalse(before['can_cancel'])
        self.assertEqual(before['status'], 'pending_staff')
        reviewed = self.workflows.staff_review_guest_change(
            row['id'], 'approve', 'receptionist', note='Guest cancellation confirmed by staff.')
        self.assertEqual(reviewed['guest_change_state'], 'cancelled')
        after = self.workflows.guest_request_progress(self.session, row['id'])
        self.assertEqual(after['change_state'], 'cancelled')
        self.assertFalse(after['can_modify'])
        active_ids = {item['id'] for item in self.workflows.list_requests()}
        self.assertNotIn(row['id'], active_ids)

    def test_guest_modification_keeps_original_and_exposes_effective_payload_after_review(self):
        row = self._submitted({'room_number': '305', 'quantity': 2})
        self.workflows.request_guest_change(
            self.session, row['id'], 'modify', 'modify-guest-001',
            payload={'quantity': 4})
        pending = self.workflows.guest_request_progress(self.session, row['id'])
        self.assertEqual(pending['payload']['quantity'], 2)
        self.assertEqual(pending['effective_payload']['quantity'], 2)
        self.workflows.staff_review_guest_change(
            row['id'], 'approve', 'receptionist', note='Updated towel quantity confirmed with operations.')
        accepted = self.workflows.guest_request_progress(self.session, row['id'])
        self.assertEqual(accepted['payload']['quantity'], 2)
        self.assertEqual(accepted['effective_payload']['quantity'], 4)

    def test_end_session_blocks_next_guest_from_previous_ticket(self):
        row = self._submitted()
        self.workflows.end_session(self.token, self.csrf)
        new_session, _, _ = self.workflows.new_session()
        with self.assertRaises(PermissionError):
            self.workflows.guest_request_progress(new_session, row['id'])
        # The staff ticket remains durable even though the shared kiosk context is gone.
        self.assertEqual(self.workflows.request_detail(row['id'])['status'], 'pending_staff')

    def test_session_preferences_are_explicit_bounded_and_feed_agent_constraints(self):
        # Preferences reach memory only as validated SetPreference commands.
        parsed = {'dietary': 'vegetarian', 'party_size': 3, 'children': 1, 'mobility': 'minimal_walking'}
        memory = SessionPreferenceMemoryStore(self.store, 'FURAMA_DANANG', 900)
        self.assertEqual(memory.merge(self.session, parsed), parsed)
        state = build_initial_state(
            query='Lên kế hoạch ăn tối rồi đi spa', language='vi',
            decision=RouteDecision('planning'), preferences=memory.load(self.session))
        self.assertEqual(state.preferences['dietary'], 'vegetarian')
        self.assertIn('minimal_walking', state.constraints)
        self.assertIn('party_size:3', state.constraints)

    def test_agent_checkpoint_and_semantic_memory_survive_language_switch(self):
        checkpoints = AgentCheckpointStore(self.store, 'FURAMA_DANANG', 900)
        projection = {
            'schema': 1, 'goal_summary': 'knowledge:verified_answer',
            'desired_outcomes': ['verified_answer'], 'unresolved_outcomes': ['verified_answer'],
            'requirements': [{'outcome': 'verified_answer', 'topic': 'restaurant'}],
            'constraints': [], 'facts': [], 'pending_question': None,
            'status': 'partial', 'termination_reason': 'waiting',
        }
        self.assertTrue(checkpoints.save(self.session, 'vi', projection))
        self.assertIsNotNone(checkpoints.load(self.session, 'en'))

        semantic = SessionSemanticMemoryStore(self.store, 'FURAMA_DANANG', 900)
        import time
        now = int(time.time())
        fact = {
            'key': 'knowledge:test', 'fact_type': 'evidence_summary',
            'value': {'topic': 'restaurant', 'summary': 'Verified restaurant information',
                      'evidence_status': 'SUPPORTED'},
            'provenance': {'source_type': 'rag', 'citations': [
                {'source_id': 's1', 'revision': 'r1', 'chunk_id': 'c1'}]},
            'confidence': 'verified', 'sensitivity': 'public',
            'observed_at': now, 'expires_at': now + 600,
        }
        self.assertEqual(semantic.merge(self.session, 'vi', [fact]), 1)
        self.assertEqual(len(semantic.load(self.session, 'en')), 1)

    def test_pending_service_draft_survives_language_switch(self):
        memory = AgentTaskMemory(ttl=600)
        memory.save(self.session, kind='dining', language='vi', mode='dining_reservation',
                    details='Đặt bàn', slots={'party_size': 2}, missing=('preferred_time',))
        task = memory.load(self.session, 'en')
        self.assertIsNotNone(task)
        self.assertEqual(task.slots['party_size'], 2)

    def test_end_session_erases_session_preference_memory(self):
        memory = SessionPreferenceMemoryStore(self.store, 'FURAMA_DANANG', 900)
        memory.merge(self.session, {'dietary': 'vegetarian', 'party_size': 2})
        self.assertEqual(memory.load(self.session)['party_size'], 2)
        self.workflows.end_session(self.token, self.csrf)
        self.assertEqual(memory.load(self.session), {})
        with self.store.connection() as con:
            self.assertIsNone(con.execute(
                'SELECT 1 FROM agent_session_preferences WHERE session_id=?',
                (self.session,)).fetchone())

    def test_reference_resolver_accepts_only_bounded_candidate_index(self):
        self.assertEqual(parse_reference_choice('{"anchor_index":1}', 3), 1)
        self.assertIsNone(parse_reference_choice('{"anchor_index":9}', 3))
        self.assertIsNone(parse_reference_choice('{"anchor_index":null}', 3))
        self.assertIsNone(parse_reference_choice('{"anchor_index":0,"reason":"x"}', 3))


if __name__ == '__main__':
    unittest.main()
