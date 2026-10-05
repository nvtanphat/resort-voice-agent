from __future__ import annotations

import asyncio
import io
import threading
import wave
from collections.abc import Callable

from concierge_kiosk.agent.understanding.intent import emergency_response
from concierge_kiosk.api.shared.contracts import Ask
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.runtime.turn_events import TurnEvents
from concierge_kiosk.voice.agent.agent_processor import ConciergeAgentProcessor
from concierge_kiosk.voice.agent.speech_gate import SpeechGate
from concierge_kiosk.voice.agent.tts import ConciergeTTS
from concierge_kiosk.voice.session.turns import VoiceTurns


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    return output.getvalue()


def _pipecat():
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        InterruptionFrame,
        StartFrame,
        TTSAudioRawFrame,
        TTSStoppedFrame,
        TextFrame,
        TranscriptionFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.workers.runner import WorkerRunner

    return {
        "InputAudioRawFrame": InputAudioRawFrame,
        "InterruptionFrame": InterruptionFrame,
        "StartFrame": StartFrame,
        "TTSAudioRawFrame": TTSAudioRawFrame,
        "TTSStoppedFrame": TTSStoppedFrame,
        "TextFrame": TextFrame,
        "TranscriptionFrame": TranscriptionFrame,
        "Pipeline": Pipeline,
        "PipelineParams": PipelineParams,
        "PipelineWorker": PipelineWorker,
        "FrameProcessor": FrameProcessor,
        "FrameDirection": FrameDirection,
        "WorkerRunner": WorkerRunner,
    }


class _FakeTransport:
    def __init__(self, frame_processor):
        self.processor = frame_processor(enable_direct_mode=True)


def _build_pipeline_harness(*, transcript: str, synthesize_fn: Callable):
    pc = _pipecat()
    FrameProcessor = pc["FrameProcessor"]
    InputAudioRawFrame = pc["InputAudioRawFrame"]
    TranscriptionFrame = pc["TranscriptionFrame"]
    StartFrame = pc["StartFrame"]
    TTSStoppedFrame = pc["TTSStoppedFrame"]
    Pipeline = pc["Pipeline"]
    PipelineParams = pc["PipelineParams"]
    PipelineWorker = pc["PipelineWorker"]
    WorkerRunner = pc["WorkerRunner"]

    class FakeTransportInput(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    class FakeSTT(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                frame = TranscriptionFrame(
                    text=transcript, user_id="guest", timestamp="now", finalized=True)
            await self.push_frame(frame, direction)

    class FakeTransportOutput(FrameProcessor):
        def __init__(self):
            super().__init__(enable_direct_mode=True)
            self.frames = []
            self.ready = asyncio.Event()
            self.stopped = asyncio.Event()

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            self.frames.append(frame)
            if isinstance(frame, StartFrame):
                self.ready.set()
            if isinstance(frame, TTSStoppedFrame):
                self.stopped.set()
            await self.push_frame(frame, direction)

    transport_input = FakeTransportInput()
    transport_output = FakeTransportOutput()
    stt = FakeSTT()
    turns = VoiceTurns(ttl_seconds=60)
    turn_events = TurnEvents()
    gate_events: list[tuple[str, str, str]] = []
    gate = SpeechGate(
        voice_turns=turns,
        current_evidence=lambda _session, _turn: True,
        emit=lambda session, turn, event: gate_events.append((session, turn, event)),
    )
    return {
        "pc": pc,
        "transport_input": transport_input,
        "transport_output": transport_output,
        "stt": stt,
        "turns": turns,
        "turn_events": turn_events,
        "gate": gate,
        "gate_events": gate_events,
        "settings": Settings(),
        "tts": ConciergeTTS(cfg=Settings(), gate=gate, synthesize_fn=synthesize_fn),
        "Pipeline": Pipeline,
        "PipelineParams": PipelineParams,
        "PipelineWorker": PipelineWorker,
        "WorkerRunner": WorkerRunner,
    }


async def _run_harness(harness, processors):
    pipeline = harness["Pipeline"](
        [harness["transport_input"], *processors, harness["transport_output"]],
    )
    worker = harness["PipelineWorker"](
        pipeline,
        params=harness["PipelineParams"](enable_metrics=False),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    runner = harness["WorkerRunner"](handle_sigint=False)
    await runner.add_workers(worker)
    runner_task = asyncio.create_task(runner.run(auto_end=False))
    # StartFrame reaches the output processor just before PipelineWorker marks
    # itself ready. Wait for the worker activation before injecting input.
    for _ in range(200):
        if worker.active and worker.started_at is not None:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("Pipecat worker did not become active")
    return worker, runner, runner_task


async def _stop_harness(runner, runner_task):
    await runner.cancel("test complete")
    await asyncio.wait_for(runner_task, 2)


def test_real_pipecat_pipeline_transcript_calls_engine_once_with_voice_input():
    async def scenario():
        calls: list[tuple[str, str, str, bool]] = []

        def answer(body: Ask, session: str, turn_id: str, *, voice_input: bool):
            calls.append((body.query, session, turn_id, voice_input))
            return {"answer": "Approved response.", "citations": []}

        harness = _build_pipeline_harness(transcript="Please help", synthesize_fn=lambda *_: _wav())
        agent = ConciergeAgentProcessor(
            cfg=harness["settings"], session="guest-1", language="en",
            voice_turns=harness["turns"], turn_events=harness["turn_events"],
            answer=answer, finalize_answer=lambda result, *_: result,
            commit_autonomous_action=lambda result, *_: result, gate=harness["gate"],
        )
        worker, runner, runner_task = await _run_harness(
            harness, [harness["stt"], agent, harness["tts"]])
        try:
            await worker.queue_frame(harness["pc"]["InputAudioRawFrame"](
                audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1))
            await asyncio.wait_for(harness["transport_output"].stopped.wait(), 2)
            assert calls and len(calls) == 1
            assert calls[0][0:2] == ("Please help", "guest-1")
            assert calls[0][3] is True
            assert any(isinstance(frame, harness["pc"]["TTSAudioRawFrame"])
                       for frame in harness["transport_output"].frames)
        finally:
            await _stop_harness(runner, runner_task)

    asyncio.run(scenario())


def test_real_pipecat_pipeline_sends_only_authorized_text_to_tts():
    async def scenario():
        synthesized: list[str] = []

        def synthesize(_cfg, text, _language):
            synthesized.append(text)
            return _wav()

        harness = _build_pipeline_harness(transcript="unused", synthesize_fn=synthesize)
        session = "guest-2"
        turn_id = harness["turns"].begin(session)
        assert harness["turns"].finish(session, turn_id)
        assert harness["gate"].authorize(session, turn_id, "Authorized answer.", "en")
        plan = harness["turns"].speech_plan(session, turn_id)
        assert plan
        chunk_id = plan["chunks"][0]["id"]
        lease = harness["gate"].reserve(session, chunk_id)
        assert lease is not None and harness["gate"].complete(lease)

        worker, runner, runner_task = await _run_harness(harness, [harness["tts"]])
        try:
            await worker.queue_frame(harness["pc"]["TextFrame"](text="not authorized"))
            authorized = harness["pc"]["TextFrame"](text=lease.text)
            authorized.metadata.update({
                "voice_session": session,
                "voice_turn_id": turn_id,
                "voice_chunk_id": chunk_id,
                "voice_language": "en",
            })
            await worker.queue_frame(authorized)
            await asyncio.wait_for(harness["transport_output"].stopped.wait(), 2)
            assert synthesized == ["Authorized answer."]
            assert harness["turns"].mark_chunk_spoken(session, chunk_id)
        finally:
            await _stop_harness(runner, runner_task)

    asyncio.run(scenario())


def test_real_pipecat_interruption_nacks_chunk_without_cancelling_committed_turn():
    async def scenario():
        started = threading.Event()
        release = threading.Event()
        committed = []

        def synthesize(_cfg, _text, _language):
            started.set()
            assert release.wait(2)
            return _wav()

        def answer(_body: Ask, _session: str, _turn_id: str, *, voice_input: bool):
            return {"answer": "A response that is currently being spoken.", "citations": []}

        harness = _build_pipeline_harness(transcript="Please help", synthesize_fn=synthesize)
        agent = ConciergeAgentProcessor(
            cfg=harness["settings"], session="guest-3", language="en",
            voice_turns=harness["turns"], turn_events=harness["turn_events"],
            answer=answer, finalize_answer=lambda result, *_: result,
            commit_autonomous_action=lambda result, *_: (committed.append(True) or result),
            gate=harness["gate"],
        )
        worker, runner, runner_task = await _run_harness(
            harness, [harness["stt"], agent, harness["tts"]])
        try:
            await worker.queue_frame(harness["pc"]["InputAudioRawFrame"](
                audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1))
            assert await asyncio.to_thread(started.wait, 2)
            # Deliver the system frame to the live TTS processor while its
            # synthesis task is waiting; the processor must NACK the active
            # chunk without touching the committed turn.
            await harness["tts"].process_frame(
                harness["pc"]["InterruptionFrame"](),
                harness["pc"]["FrameDirection"].DOWNSTREAM,
            )
            await asyncio.wait_for(
                _wait_for_event(harness["gate_events"], "tts.chunk.playback_failed"), 2)
            assert committed == [True]
            turn_id = next(item[1] for item in harness["gate_events"] if item[2] == "response.approved")
            journal = harness["turn_events"].read("guest-3", turn_id)
            assert journal and journal["terminal"] is False
            assert harness["turns"].current("guest-3", turn_id)
        finally:
            release.set()
            await _stop_harness(runner, runner_task)

    asyncio.run(scenario())


async def _wait_for_event(events: list[tuple[str, str, str]], event: str):
    for _ in range(200):
        if any(item[2] == event for item in events):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"event not observed: {event}")


def test_real_pipecat_emergency_reply_is_deterministic_without_slm(monkeypatch):
    async def scenario():
        slm_calls = []

        def forbidden_slm(*_args, **_kwargs):
            slm_calls.append(True)
            raise AssertionError("emergency handling must not call the SLM")

        monkeypatch.setattr(
            "concierge_kiosk.agent.understanding.commands.model_commands", forbidden_slm)

        def answer(body: Ask, _session: str, _turn_id: str, *, voice_input: bool):
            reply = emergency_response(body.query, body.language)
            assert reply
            return {"answer": reply, "citations": []}

        harness = _build_pipeline_harness(
            transcript="Có người chảy máu", synthesize_fn=lambda *_: _wav())
        agent = ConciergeAgentProcessor(
            cfg=harness["settings"], session="guest-4", language="vi",
            voice_turns=harness["turns"], turn_events=harness["turn_events"],
            answer=answer, finalize_answer=lambda result, *_: result,
            commit_autonomous_action=lambda result, *_: result, gate=harness["gate"],
        )
        worker, runner, runner_task = await _run_harness(
            harness, [harness["stt"], agent, harness["tts"]])
        try:
            await worker.queue_frame(harness["pc"]["InputAudioRawFrame"](
                audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1))
            await asyncio.wait_for(harness["transport_output"].stopped.wait(), 2)
            assert slm_calls == []
        finally:
            await _stop_harness(runner, runner_task)

    asyncio.run(scenario())
