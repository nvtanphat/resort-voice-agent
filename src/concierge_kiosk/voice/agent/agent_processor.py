"""Frame processor that connects final transcripts to the governed concierge."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from concierge_kiosk.api.shared.contracts import Ask
from .speech_gate import SpeechGate

try:
    from pipecat.frames.frames import Frame, InterruptionFrame, TextFrame, TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ModuleNotFoundError:  # pragma: no cover
    Frame = InterruptionFrame = TextFrame = TranscriptionFrame = FrameDirection = None
    FrameProcessor = object


def pipecat_agent_available() -> bool:
    return FrameProcessor is not object


if FrameProcessor is object:

    class ConciergeAgentProcessor:  # pragma: no cover
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("Pipecat voice extra is not installed")

else:

    class ConciergeAgentProcessor(FrameProcessor):
        """Run the existing engine once per final transcript.

        The engine remains the authority for emergency handling, evidence,
        workflow writes and confirmation. This processor only translates its
        approved speech plan into server-owned TextFrames for the guarded TTS.
        """

        def __init__(self, *, cfg, session: str, language: str, voice_turns,
                     turn_events, answer: Callable, finalize_answer: Callable,
                     commit_autonomous_action: Callable, gate: SpeechGate,
                     on_progress: Callable[[str, str], object] | None = None):
            super().__init__()
            self.cfg = cfg
            self.session = session
            self.language = language
            self.voice_turns = voice_turns
            self.turn_events = turn_events
            self.answer = answer
            self.finalize_answer = finalize_answer
            self.commit_autonomous_action = commit_autonomous_action
            self.gate = gate
            self.on_progress = on_progress or (lambda _kind, _turn: None)
            self._answer_task: asyncio.Task | None = None

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, InterruptionFrame):
                if self._answer_task is not None:
                    self._answer_task.cancel()
                await self.push_frame(frame, direction)
                return
            if not isinstance(frame, TranscriptionFrame):
                await self.push_frame(frame, direction)
                return
            if self._answer_task is not None and not self._answer_task.done():
                self._answer_task.cancel()
            text = str(frame.text or "").strip()
            if not text:
                return
            self._answer_task = self._spawn(self._answer_turn(text, direction), "concierge-answer")

        def _spawn(self, coroutine, name: str):
            create_task = getattr(self, "create_task", None)
            if create_task is not None:
                return create_task(coroutine, name=name)
            return asyncio.create_task(coroutine, name=name)

        async def _answer_turn(self, query: str, direction: FrameDirection):
            turn_id = self.voice_turns.begin(self.session)
            if not self.turn_events.begin(self.session, turn_id):
                self.voice_turns.cancel(self.session, turn_id)
                return
            try:
                self.turn_events.emit_diagnostic(self.session, turn_id, "stt.final")
                self.on_progress("agent.plan.started", turn_id)
                body = Ask(query=query, language=self.language)
                result = await asyncio.to_thread(
                    self.answer, body, self.session, turn_id, voice_input=True)
                if not self.voice_turns.finish(self.session, turn_id):
                    raise RuntimeError("voice turn superseded")
                result = self.finalize_answer(result, self.language, self.session)
                result = self.commit_autonomous_action(result, self.session)
                evidence = tuple(
                    (item["chunk_id"], item["source_id"], item["revision"],
                     item["quote"], item["language"])
                    for item in result.get("citations", [])
                )
                effective_date = str(result.get("_turn_effective_date", ""))
                if not self.gate.authorize(
                        self.session, turn_id, result.get("answer", ""), self.language,
                        evidence=evidence, effective_date=effective_date):
                    raise RuntimeError("speech authorization failed")
                plan = self.voice_turns.speech_plan(self.session, turn_id)
                if not plan:
                    raise RuntimeError("speech plan unavailable")
                self.on_progress("response.approved", turn_id)
                for item in plan["chunks"]:
                    chunk_id = item["id"]
                    lease = self.gate.reserve(self.session, chunk_id)
                    if lease is None or not self.gate.complete(lease):
                        raise RuntimeError("speech chunk authorization failed")
                    text_frame = TextFrame(text=lease.text)
                    metadata = getattr(text_frame, "metadata", None)
                    if not isinstance(metadata, dict):
                        metadata = {}
                        setattr(text_frame, "metadata", metadata)
                    metadata.update({
                        "voice_session": self.session,
                        "voice_turn_id": turn_id,
                        "voice_chunk_id": chunk_id,
                        "voice_language": lease.language,
                    })
                    await self.push_frame(text_frame, direction)
                    self.on_progress("agent.step.completed", turn_id)
            except asyncio.CancelledError:
                self.voice_turns.cancel(self.session, turn_id)
                self.turn_events.emit_diagnostic(self.session, turn_id, "turn.cancelled")
                raise
            except (RuntimeError, OSError, ValueError, TypeError, KeyError):
                self.voice_turns.cancel(self.session, turn_id)
                self.turn_events.emit_diagnostic(self.session, turn_id, "turn.failed")


__all__ = ["ConciergeAgentProcessor", "pipecat_agent_available"]
