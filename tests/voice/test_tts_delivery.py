from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from concierge_kiosk.voice.agent.speech_gate import SpeechGate
from concierge_kiosk.voice.agent.tts import ConciergeTTS
from concierge_kiosk.voice.runtime import tts as runtime_tts
from concierge_kiosk.voice.session.turns import VoiceTurns
from test_voice_agent_pipeline import _wav


def _owned_tts(monkeypatch, language='en'):
    from pipecat.frames.frames import TextFrame
    turns = VoiceTurns(ttl_seconds=60)
    turn = turns.begin('guest')
    assert turns.finish('guest', turn)
    events = []
    gate = SpeechGate(voice_turns=turns, current_evidence=lambda *_: True,
                      emit=lambda *event: events.append(event))
    assert gate.authorize('guest', turn, 'An approved reply.', language)
    chunk = turns.speech_plan('guest', turn)['chunks'][0]['id']
    lease = gate.reserve('guest', chunk)
    assert lease and gate.complete(lease)
    calls = []
    def synthesize(*args):
        calls.append(args)
        return _wav()
    processor = ConciergeTTS(cfg=SimpleNamespace(tts_timeout_seconds=1,
        voice_ws_idle_timeout_seconds=0.01), gate=gate, synthesize_fn=synthesize)
    # These callback-level tests do not create Pipecat worker tasks. The separate
    # pipeline tests exercise its real queues and interruption task lifecycle.
    monkeypatch.setattr(processor, '_enable_direct_mode', True)
    output = []
    async def capture(frame, direction):
        output.append(frame)
    monkeypatch.setattr(processor, 'push_frame', capture)
    frame = TextFrame(text=lease.text)
    frame.metadata.update(voice_session='guest', voice_turn_id=turn,
                          voice_chunk_id=chunk, voice_language=language)
    return processor, frame, events, output, calls, turns, turn, chunk


async def _wait_token(output):
    from pipecat.frames.frames import OutputTransportMessageFrame
    for _ in range(100):
        for frame in output:
            if isinstance(frame, OutputTransportMessageFrame):
                data = frame.message['data']
                if data['type'] == 'speech.chunk.end':
                    return data['token']
        await asyncio.sleep(0.001)
    raise AssertionError('Playback marker missing')


def _ack(token, status='played'):
    from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
    return RTVIClientMessageFrame(msg_id='ack', type='speech.playback',
                                 data={'token': token, 'status': status})


def test_audio_is_not_marked_played_until_owned_browser_ack(monkeypatch):
    async def scenario():
        from pipecat.processors.frame_processor import FrameDirection
        processor, frame, events, output, *_ = _owned_tts(monkeypatch)
        direction = FrameDirection.DOWNSTREAM
        task = asyncio.create_task(processor.process_frame(frame, direction))
        token = await _wait_token(output)
        assert not any(event[2] == 'tts.chunk.played' for event in events)
        await processor.process_frame(_ack('invalid-token'), direction)
        assert not task.done()
        await processor.process_frame(_ack(token), direction)
        await asyncio.wait_for(task, 1)
        await processor.process_frame(_ack(token), direction)
        assert sum(event[2] == 'tts.chunk.played' for event in events) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('control', ['interrupt', 'disconnect', 'failed'])
def test_unheard_audio_is_failed_and_late_ack_is_ignored(monkeypatch, control):
    async def scenario():
        from pipecat.frames.frames import InterruptionFrame, CancelFrame
        from pipecat.processors.frame_processor import FrameDirection
        processor, frame, events, output, _, turns, turn, _ = _owned_tts(monkeypatch)
        direction = FrameDirection.DOWNSTREAM
        task = asyncio.create_task(processor.process_frame(frame, direction))
        token = await _wait_token(output)
        if control == 'failed':
            await processor.process_frame(_ack(token, 'failed'), direction)
        else:
            await processor.process_frame(InterruptionFrame() if control == 'interrupt'
                                          else CancelFrame(), direction)
        with pytest.raises(RuntimeError, match='playback failed'):
            await task
        await processor.process_frame(_ack(token), direction)
        assert not any(event[2] == 'tts.chunk.played' for event in events)
        assert any(event[2] == 'tts.chunk.playback_failed' for event in events)
        assert turns.current('guest', turn)
    asyncio.run(scenario())


def test_missing_ack_times_out_without_recording_playback(monkeypatch):
    monkeypatch.setattr('concierge_kiosk.voice.agent.tts.MAX_TTS_SECONDS', 0)
    async def scenario():
        from pipecat.processors.frame_processor import FrameDirection
        processor, frame, events, *_ = _owned_tts(monkeypatch)
        with pytest.raises(asyncio.TimeoutError):
            await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
        assert not any(event[2] == 'tts.chunk.played' for event in events)
        assert any(event[2] == 'tts.chunk.playback_failed' for event in events)
    asyncio.run(scenario())


@pytest.mark.parametrize('language', [None, 'unknown', 'vi', 7])
def test_missing_invalid_or_mismatched_language_never_calls_tts(monkeypatch, language):
    async def scenario():
        from pipecat.processors.frame_processor import FrameDirection
        processor, frame, _, _, calls, *_ = _owned_tts(monkeypatch)
        if language is None:
            frame.metadata.pop('voice_language')
        else:
            frame.metadata['voice_language'] = language
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
        assert calls == []
    asyncio.run(scenario())


@pytest.mark.parametrize('language', ['vi', 'en', 'zh', 'ko'])
def test_owned_language_reaches_synthesizer_unchanged(monkeypatch, language):
    async def scenario():
        from pipecat.processors.frame_processor import FrameDirection
        processor, frame, _, output, calls, *_ = _owned_tts(monkeypatch, language)
        direction = FrameDirection.DOWNSTREAM
        task = asyncio.create_task(processor.process_frame(frame, direction))
        await processor.process_frame(_ack(await _wait_token(output)), direction)
        await task
        assert calls[0][2] == language
    asyncio.run(scenario())


def _silent_worker(connection):
    # A real child blocked in work: no model, network or audio device.
    try:
        connection.recv()
        connection.recv()
    except EOFError:
        pass


def _echo_worker(connection):
    try:
        while True:
            _, text, _ = connection.recv()
            connection.send((True, text.encode()))
    except EOFError:
        pass


@pytest.mark.parametrize('cancel', [False, True])
def test_stuck_piper_worker_is_terminated_and_next_request_recovers(monkeypatch, cancel):
    runtime_tts._stop_piper_worker()
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    worker = context.Process(target=_silent_worker, args=(child,), daemon=True)
    worker.start()
    child.close()
    runtime_tts._PIPER_WORKER, runtime_tts._PIPER_CONNECTION = worker, parent
    checks = []
    def cancelled():
        checks.append(True)
        return cancel and len(checks) > 1
    try:
        with pytest.raises(RuntimeError, match='cancelled or timed out'):
            runtime_tts._bounded_piper_synthesis(Path('unused'), 'request', timeout=0.1,
                                                 cancelled=cancelled)
        assert runtime_tts._PIPER_WORKER is None
        assert worker._closed  # terminate/join/close completed, not just a cancelled future
        monkeypatch.setattr(runtime_tts, '_piper_worker', _echo_worker)
        assert runtime_tts._bounded_piper_synthesis(Path('unused'), 'recovered', timeout=5) == b'recovered'
        current = runtime_tts._PIPER_WORKER
        assert runtime_tts._bounded_piper_synthesis(Path('unused'), 'warm', timeout=1) == b'warm'
        assert runtime_tts._PIPER_WORKER is current
    finally:
        runtime_tts._stop_piper_worker()


@pytest.mark.parametrize('cancellable', [False, True])
def test_piper_entrypoints_pass_configured_timeout(monkeypatch, cancellable):
    monkeypatch.setattr(runtime_tts, 'tts_model_paths', lambda *_: (Path('voice'), Path('metadata')))
    monkeypatch.setattr(runtime_tts, '_tts_synthesis_config', lambda *_: (None, {}))
    monkeypatch.setattr(runtime_tts, 'inprocess_piper_available', lambda: True)
    calls = []
    def synthesize(*args, **kwargs):
        calls.append(kwargs)
        return b'audio'
    monkeypatch.setattr(runtime_tts, '_bounded_piper_synthesis', synthesize)
    cfg = SimpleNamespace(voice_speech_plan={'max_chars': 750}, tts_timeout_seconds=0.7)
    if cancellable:
        assert runtime_tts.synthesize_cancellable(cfg, 'hello', 'en', lambda: False) == b'audio'
    else:
        assert runtime_tts.synthesize(cfg, 'hello', 'en') == b'audio'
    assert calls[0]['timeout'] == 0.7
