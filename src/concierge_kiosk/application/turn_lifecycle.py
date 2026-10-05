"""Accepted-turn state projection.

Inference is speculative.  This service commits only conversation/task projection
after the owning HTTP/voice turn survived stale-turn checks.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable

from fastapi import HTTPException

from ..api.shared.workflow_progress import project_task_progress


class TurnFinalizer:
    def __init__(self, *, conversations, task_checkpoints, agent_checkpoints=None, agent_memory=None,
                 preference_memory=None, ensure_session: Callable[[str], None], logger: logging.Logger):
        self._conversations = conversations
        self._task_checkpoints = task_checkpoints
        self._agent_checkpoints = agent_checkpoints
        self._agent_memory = agent_memory
        self._preference_memory = preference_memory
        self._ensure_session = ensure_session
        self._logger = logger

    def finalize(self, result: dict, language: str, session: str) -> dict:
        """Commit conversation state after acceptance, never during inference."""
        self._ensure_session(session)
        clean = dict(result)
        sources = clean.pop('_remember_sources', None)
        query = clean.pop('_remember_query', None)
        revoked = clean.pop('_forget_anchor', None)
        expected_version = int(clean.pop('_memory_version', self._conversations.topic_version(session)))
        clean.pop('_turn_effective_date', None)
        agent_checkpoint = clean.pop('_agent_checkpoint', None)
        agent_memory = clean.pop('_agent_memory', None)
        preference_memory = clean.pop('_preference_memory', None)
        expected_reply = clean.pop('_expected_reply', None)
        if sources is not None:
            committed, turn_number = self._conversations.commit_topic(
                session, language, expected_version=expected_version, sources=sources,
                query=query, revoked=revoked)
            if not committed:
                raise HTTPException(status_code=409, detail='Conversation context changed; retry turn')
            clean['turn_number'] = turn_number
        elif clean.get('grounding') == 'safety_route':
            committed, _ = self._conversations.commit_topic(
                session, language, expected_version=expected_version, clear=True)
            if not committed:
                raise HTTPException(status_code=409, detail='Conversation context changed; retry turn')
            self._task_checkpoints.clear(session)
        elif (clean.get('suggested_action') is not None or clean.get('tool_route') == 'planning'
              or isinstance(clean.get('_autonomous_action'), dict)
              or isinstance(clean.get('_autonomous_actions'), list)
              or bool(clean.get('proposed_actions'))):
            committed, _ = self._conversations.commit_topic(
                session, language, expected_version=expected_version, suspend=True)
            if not committed:
                raise HTTPException(status_code=409, detail='Conversation context changed; retry turn')

        workflow = clean.get('mixed_workflow')
        if isinstance(workflow, dict) and workflow.get('business_writes') == 0:
            kinds = tuple(option['kind'] for option in workflow.get('action_options', [])
                          if isinstance(option, dict) and isinstance(option.get('kind'), str))
            self._conversations.remember_review(session, language, kinds)
            execution = clean.get('task_execution')
            if isinstance(execution, dict):
                self._conversations.remember_task_execution(session, language, execution)
                try:
                    self._task_checkpoints.save(
                        session, language, self._conversations.task_projection(session, language))
                except (OSError, ValueError, sqlite3.Error):
                    self._logger.exception('read_task_projection_sync_deferred')

        if self._agent_checkpoints is not None and isinstance(agent_checkpoint, dict):
            try:
                action = agent_checkpoint.get('action')
                if action == 'save' and isinstance(agent_checkpoint.get('projection'), dict):
                    self._agent_checkpoints.save(session, language, agent_checkpoint['projection'])
                elif action == 'clear':
                    self._agent_checkpoints.clear(session)
            except (OSError, ValueError, sqlite3.Error):
                self._logger.exception('agent_checkpoint_sync_deferred')

        if self._agent_memory is not None and isinstance(agent_memory, dict):
            try:
                action = agent_memory.get('action')
                if action == 'merge' and isinstance(agent_memory.get('facts'), list):
                    self._agent_memory.merge(session, language, agent_memory['facts'])
                elif action == 'clear':
                    self._agent_memory.clear(session)
            except (OSError, ValueError, sqlite3.Error):
                self._logger.exception('agent_semantic_memory_sync_deferred')

        if self._preference_memory is not None and isinstance(preference_memory, dict):
            try:
                action = preference_memory.get('action')
                if action == 'merge' and isinstance(preference_memory.get('preferences'), dict):
                    self._preference_memory.merge(session, preference_memory['preferences'])
                elif action == 'clear':
                    self._preference_memory.clear(session)
            except (OSError, ValueError, sqlite3.Error):
                self._logger.exception('agent_preference_memory_sync_deferred')

        if isinstance(expected_reply, dict):
            try:
                action = expected_reply.get('action')
                if action == 'save' and isinstance(expected_reply.get('value'), str):
                    self._conversations.remember_expected_reply(
                        session, language, expected_reply['value'])
                elif action == 'clear':
                    self._conversations.remember_expected_reply(session, language, None)
            except (OSError, ValueError, sqlite3.Error):
                self._logger.exception('expected_reply_sync_deferred')

        clean['review_state'] = self._conversations.review_projection(session, language)
        live_projection = self._conversations.task_projection(session, language)
        persisted_projection = self._task_checkpoints.load(session, language)
        clean['task_state'] = persisted_projection or live_projection
        clean['task_progress'] = project_task_progress(clean)
        # The detailed orchestration structures are internal validation state.
        # Persist/use them above, then expose only the bounded guest-safe
        # projections (task_progress/task_state/review_state) to the client.
        for internal_key in ('task_graph', 'task_plan', 'mixed_workflow',
                             'task_execution', 'composite_plan'):
            clean.pop(internal_key, None)
        return clean
