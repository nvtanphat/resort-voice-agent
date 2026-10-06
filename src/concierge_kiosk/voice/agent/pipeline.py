"""Optional Pipecat pipeline assembly for the kiosk websocket transport."""
from __future__ import annotations

from .agent_processor import ConciergeAgentProcessor
from .speech_gate import SpeechGate
from .stt import ConciergeSTT
from .tts import ConciergeTTS


def pipecat_available() -> bool:
    try:
        import pipecat  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def build_pipeline(*, websocket, cfg, session: str, language: str,
                   voice_turns, turn_events, store, answer, finalize_answer,
                   finalize_service_turn, transcribe_fn, synthesize_fn):
    """Build a fully local Pipecat pipeline without an LLM-generated prose path."""
    if not pipecat_available():
        raise RuntimeError("Pipecat voice extra is not installed")
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.serializers.protobuf import ProtobufFrameSerializer
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams, FastAPIWebsocketTransport,
    )
    from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
    from pipecat.turns.user_turn_processor import UserTurnProcessor
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    gate = SpeechGate(
        voice_turns=voice_turns,
        current_evidence=lambda current_session, turn_id: __import__(
            "concierge_kiosk.voice.agent.speech_gate",
            fromlist=["evidence_is_current"],
        ).evidence_is_current(
            store=store, cfg=cfg, voice_turns=voice_turns,
            session=current_session, turn_id=turn_id,
        ),
        emit=lambda current_session, turn_id, event: turn_events.emit_diagnostic(
            current_session, turn_id, event),
    )
    vad = SileroVADAnalyzer(params=VADParams(
        stop_secs=float(cfg.voice_vad_stop_secs),
    ))
    vad_processor = VADProcessor(vad_analyzer=vad)
    turn_processor = UserTurnProcessor(
        user_turn_strategies=UserTurnStrategies(stop=[
            TurnAnalyzerUserTurnStopStrategy(
                turn_analyzer=LocalSmartTurnAnalyzerV3()),
        ]),
        user_turn_stop_timeout=float(cfg.voice_user_turn_stop_timeout_seconds),
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            serializer=ProtobufFrameSerializer(),
        ),
    )
    stt = ConciergeSTT(cfg=cfg, language=language, transcribe_fn=transcribe_fn)
    agent = ConciergeAgentProcessor(
        cfg=cfg, session=session, language=language, voice_turns=voice_turns,
        turn_events=turn_events, answer=answer, finalize_answer=finalize_answer,
        finalize_service_turn=finalize_service_turn, gate=gate,
    )
    tts = ConciergeTTS(cfg=cfg, gate=gate, synthesize_fn=synthesize_fn)
    return Pipeline([
        transport.input(), vad_processor, turn_processor, stt, agent, tts,
        transport.output(),
    ]), transport


async def run_pipeline(pipeline, *, transport, idle_timeout: float):
    """Run with RTVI enabled on Pipecat 1.x, falling back for older installs."""
    try:
        from pipecat.pipeline.worker import PipelineParams, PipelineWorker
        from pipecat.workers.runner import WorkerRunner
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=False),
            idle_timeout_secs=idle_timeout,
        )
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        await runner.run()
        return
    except ImportError:  # pragma: no cover - compatibility with Pipecat 0.x
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.pipeline.runner import PipelineRunner
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=False,
        ),
    )
    await PipelineRunner().run(task)


__all__ = ["build_pipeline", "pipecat_available", "run_pipeline"]
