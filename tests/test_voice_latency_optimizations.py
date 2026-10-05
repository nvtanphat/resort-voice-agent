from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.runtime.admission import AudioAdmission
from concierge_kiosk.voice.runtime import adapters
from concierge_kiosk.voice.runtime.tts_cache import load_cached_wav, store_cached_wav
from concierge_kiosk.voice.session.turns import VoiceTurns, _plan_speech_chunks


def _wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * 1600)
    return out.getvalue()


def test_final_whisper_uses_ui_language_and_avoids_second_decode(monkeypatch, tmp_path: Path):
    model_dir = tmp_path / 'whisper'
    model_dir.mkdir()
    audio = _wav()
    calls = []

    class Model:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(text=' 안녕하세요 ')], SimpleNamespace(
                language=kwargs['language'], language_probability=1.0)

    monkeypatch.setattr(adapters, '_whisper', lambda path: Model())
    cfg = Settings(whisper_model_path=str(model_dir))
    result = adapters.transcribe_detected(cfg, audio, 'vi')
    assert result.text == '안녕하세요'
    assert result.detected_language == 'ko'
    assert result.language_probability == 1.0
    # The decoder is bounded to the UI language; an obvious Hangul script can
    # still suggest a switch without a separate language-detection pass.
    assert calls[0]['language'] == 'vi'
    assert calls[0]['beam_size'] == 1


def test_preview_stt_remains_ui_language_bounded(monkeypatch, tmp_path: Path):
    model_dir = tmp_path / 'whisper'
    model_dir.mkdir()
    audio = _wav()
    calls = []

    class Model:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(text=' xin chào ')], SimpleNamespace(
                language='vi', language_probability=0.99)

    monkeypatch.setattr(adapters, '_whisper', lambda path: Model())
    cfg = Settings(whisper_model_path=str(model_dir))
    assert adapters.transcribe(cfg, audio, 'vi') == 'xin chào'
    assert calls[0]['language'] == 'vi'
    assert calls[0]['beam_size'] == 1


def test_audio_admission_keeps_stt_priority_but_allows_tts_slm_overlap():
    admission = AudioAdmission()
    assert admission.try_enter_slm('a')
    assert admission.try_enter_tts()  # no whole-system SLM/TTS mutex anymore
    admission.leave_tts()
    admission.leave_slm()

    admission.enter_stt('a')
    assert not admission.try_enter_slm('a')
    assert not admission.try_enter_tts()
    admission.leave_stt()


def test_voice_turn_can_prepare_one_chunk_ahead_without_advancing_playback():
    turns = VoiceTurns(ttl_seconds=60)
    session = 'guest'
    turn = turns.begin(session)
    assert turns.finish(session, turn)
    answer = 'First sentence is short. Second sentence is also short. Third sentence completes the answer.'
    assert turns.authorize_speech(session, turn, answer, 'en')
    plan = turns.speech_plan(session, turn)
    assert plan and len(plan['chunks']) >= 2
    first, second = plan['chunks'][:2]

    reserved1 = turns.reserve_chunk(session, first['id'])
    assert reserved1 is not None
    _, lease1, _, _ = reserved1
    assert turns.complete_chunk(session, first['id'], lease1)

    # Current chunk is synthesized but not played. The next one may synthesize now.
    reserved2 = turns.reserve_chunk(session, second['id'])
    assert reserved2 is not None
    _, lease2, _, _ = reserved2
    assert turns.complete_chunk(session, second['id'], lease2)

    # Playback ACK still must be ordered; second cannot advance first.
    assert not turns.mark_chunk_spoken(session, second['id'])
    assert turns.mark_chunk_spoken(session, first['id'])
    assert turns.mark_chunk_spoken(session, second['id'])


def test_fixed_voice_text_can_opt_into_tts_cache_without_evidence():
    turns = VoiceTurns(ttl_seconds=60)
    session = 'guest'
    turn = turns.begin(session)
    assert turns.finish(session, turn)
    assert turns.authorize_speech(session, turn, 'Hello! How can I help you today?', 'en', cacheable=True)
    assert turns.speech_cacheable(session, turn) is True


def test_evidence_free_voice_text_is_not_cacheable_by_default():
    turns = VoiceTurns(ttl_seconds=60)
    session = 'guest'
    turn = turns.begin(session)
    assert turns.finish(session, turn)
    assert turns.authorize_speech(session, turn, 'A fixed response.', 'en')
    assert turns.speech_cacheable(session, turn) is False


def test_speech_planner_makes_first_chunk_small_when_sentence_boundaries_exist():
    chunks = _plan_speech_chunks(
        'This first sentence is deliberately concise. '
        'This second sentence adds another useful fact without waiting for the entire answer. '
        'This third sentence completes the explanation.', 'en')
    assert len(chunks) >= 2
    assert len(chunks[0].text) <= 120


def test_source_backed_wav_cache_is_model_fingerprinted(tmp_path: Path):
    models = tmp_path / 'voices'
    models.mkdir()
    (models / 'en.onnx').write_bytes(b'model')
    (models / 'en.onnx.json').write_text('{}')
    cfg = Settings(db_path=tmp_path / 'data' / 'concierge.sqlite3', piper_models_dir=str(models))
    data = _wav()
    assert store_cached_wav(cfg, 'Pool opens at 07:00.', 'en', data)
    assert load_cached_wav(cfg, 'Pool opens at 07:00.', 'en') == data
    assert load_cached_wav(cfg, 'Different sentence.', 'en') is None


def test_tts_cache_key_includes_voice_policy(monkeypatch, tmp_path: Path):
    from concierge_kiosk.voice.runtime import tts_cache

    models = tmp_path / 'voices'
    models.mkdir()
    (models / 'vi.onnx').write_bytes(b'model')
    (models / 'vi.onnx.json').write_text('{}')
    cfg = Settings(db_path=tmp_path / 'data' / 'concierge.sqlite3', piper_models_dir=str(models))
    monkeypatch.setattr(tts_cache, 'voice_policy', lambda: {'version': 1})
    first = tts_cache.cache_key(cfg, 'Wi-Fi', 'vi')
    monkeypatch.setattr(tts_cache, 'voice_policy', lambda: {'version': 2})
    second = tts_cache.cache_key(cfg, 'Wi-Fi', 'vi')
    assert first and second and first != second


def test_edge_and_production_do_not_carry_removed_persistent_piper_flag():
    root = Path(__file__).resolve().parents[1]
    for name in ('edge', 'production'):
        profile = json.loads((root / 'config' / 'runtime-profiles' / f'{name}.json').read_text(encoding='utf-8'))
        assert 'piper_persistent' not in profile['models']['voice']


def test_incremental_vosk_is_preview_only_and_final_whisper_can_switch_language():
    import asyncio
    from concierge_kiosk.api.voice.streaming import _finish_incremental

    class WS:
        def __init__(self):
            self.messages = []
        async def send_json(self, payload):
            self.messages.append(payload)

    class Incremental:
        def finish(self):
            return 'wrong-language preview'

    class Events:
        def emit(self, *args):
            pass

    ws = WS()
    turns = VoiceTurns(ttl_seconds=60)
    session = 'guest'
    turn = turns.begin(session)

    def detected(cfg, audio, language):
        return adapters.DetectedTranscript('안녕하세요', 'ko', 0.95)

    ok = asyncio.run(_finish_incremental(
        websocket=ws, cfg=Settings(), incremental=Incremental(),
        raw_pcm=b'\x00\x00' * 1600, language='en', transcribe_detected_fn=detected,
        voice_turns=turns, turn_events=Events(), session=session, turn_id=turn,
        incremental_bytes=3200))
    assert ok is True
    final = ws.messages[-1]
    assert final['text'] == '안녕하세요'
    assert final['detected_language'] == 'ko'
    assert final['suggest_language_switch'] is True
    assert final['decoder'] == 'faster_whisper_final_with_vosk_preview'


def test_speech_rendering_drops_markdown_and_speaks_clock_ranges():
    assert adapters.speech_rendering('- **Schedule**: 06:00–18:30 (lifeguard hours)', 'en') == (
        'Schedule: 6 AM to 6:30 PM (lifeguard hours)')
    assert adapters.speech_rendering('- **Lịch hoạt động**: 06:00–18:30', 'vi') == (
        'Lịch hoạt động: 6 giờ đến 18 giờ 30')
    assert adapters.speech_rendering('- **일정**: 06:00–18:30', 'ko') == '일정: 6시부터 18시 30분까지'
    assert adapters.speech_rendering('- **活动时间**: 06:00–18:30', 'zh') == '活动时间: 6点到18点30分'
    # Configured PBX extension identifiers are spoken digit by digit; unrelated
    # numeric expressions remain untouched.
    assert adapters.speech_rendering('Extension 3420, ratio 3:2.', 'en') == (
        'Extension three four two zero, ratio 3:2.')


def test_vietnamese_speech_rendering_normalizes_phone_symbols_units_and_email():
    rendered = adapters.speech_rendering(
        'Điện thoại: +84 911 301 020; 258 m², 1 km, 50%, Wi-Fi & '
        '7303/7304; fb@furamavietnam.com', 'vi')
    assert rendered == (
        'Điện thoại cộng tám bốn chín một một ba không một không hai không; '
        '258 mét vuông, 1 ki-lô-mét, 50 phần trăm, Quai-phai và '
        'bảy ba không ba gạch chéo bảy ba không bốn; '
        'fb a còng furamavietnam chấm com')


def test_money_is_spoken_in_the_guest_language_without_losing_value():
    assert adapters.speech_rendering('Giá 1,500,000 VND', 'vi') == 'Giá 1 triệu 500 nghìn đồng'
    assert adapters.speech_rendering('Price 250,000 VND', 'en') == 'Price 250 thousand dong'
    assert adapters.speech_rendering('1,000,500 VND', 'en') == '1 million 500 dong'
    assert adapters.speech_rendering('12.50 USD', 'en') == '12 point 5 0 US dollars'
    assert adapters.speech_rendering('价格 1,500,000 VND', 'zh') == '价格 150万 越南盾'
    assert adapters.speech_rendering('가격 1,234,567 VND', 'ko') == '가격 123만 4567 동'
    # Years and plain numbers are untouched.
    assert adapters.speech_rendering('Open 2025 to 2026', 'en') == 'Open 2025 to 2026'


def _stt_with_segments(monkeypatch, tmp_path: Path, segments):
    model_dir = tmp_path / 'whisper'
    model_dir.mkdir(exist_ok=True)

    class Model:
        def detect_language(self, audio):
            return 'en', 0.99, [('en', 0.99)]

        def transcribe(self, path, **kwargs):
            return segments, SimpleNamespace(language='en', language_probability=0.99)

    monkeypatch.setattr(adapters, '_whisper', lambda path: Model())
    cfg = Settings(whisper_model_path=str(model_dir), stt_hallucination_phrases=('thank you',))
    return adapters.transcribe_detected(cfg, _wav(), 'en')


def test_stt_drops_stock_outro_only_when_decoder_doubts_speech(monkeypatch, tmp_path: Path):
    ghost = SimpleNamespace(text=' Thank you. ', no_speech_prob=0.8, avg_logprob=-1.4)
    assert _stt_with_segments(monkeypatch, tmp_path, [ghost]).text == ''
    # A guest who really says "thank you" (confident speech) keeps the transcript.
    real = SimpleNamespace(text=' Thank you. ', no_speech_prob=0.02, avg_logprob=-0.2)
    assert _stt_with_segments(monkeypatch, tmp_path, [real]).text == 'Thank you.'


def test_stt_confidence_reflects_decoding_quality_not_just_speech_presence(monkeypatch, tmp_path: Path):
    shaky = SimpleNamespace(text=' maybe room three ten ', no_speech_prob=0.01, avg_logprob=-1.2)
    result = _stt_with_segments(monkeypatch, tmp_path, [shaky])
    assert result.confidence is not None and result.confidence < 0.45
    solid = SimpleNamespace(text=' what time is breakfast ', no_speech_prob=0.01, avg_logprob=-0.15)
    assert _stt_with_segments(monkeypatch, tmp_path, [solid]).confidence > 0.8


def test_short_utterance_with_unsure_detection_uses_the_ui_language(monkeypatch, tmp_path: Path):
    model_dir = tmp_path / 'whisper'
    model_dir.mkdir()
    seen = {}

    class Model:
        def detect_language(self, audio):
            return 'en', 0.62, [('en', 0.62), ('vi', 0.30)]  # "alo" heard as English "Oh"

        def transcribe(self, path, **kwargs):
            seen.update(kwargs)
            return [SimpleNamespace(text=' Alo ', no_speech_prob=0.05, avg_logprob=-0.3)], SimpleNamespace(
                language=kwargs['language'], language_probability=1.0)

    monkeypatch.setattr(adapters, '_whisper', lambda path: Model())
    cfg = Settings(whisper_model_path=str(model_dir))
    result = adapters.transcribe_detected(cfg, _wav(), 'vi')
    assert seen['language'] == 'vi' and result.text == 'Alo'


def test_stt_discards_decoder_repetition_loops(monkeypatch, tmp_path: Path):
    loop = SimpleNamespace(text=' Tất cả tất cả tất cả tất cả tất cả tất cả ', no_speech_prob=0.05,
                           avg_logprob=-0.4)
    assert _stt_with_segments(monkeypatch, tmp_path, [loop]).text == ''
    assert adapters._is_repetition_loop('What time does the pool open') is False
