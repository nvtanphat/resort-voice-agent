"""Low-overhead admission control for CPU-bound edge audio work.

STT gets admission ahead of NEW TTS jobs. Running Piper subprocesses cannot be
preempted safely here; browser barge-in cancels playback and cooperatively
signals same-session SLM work, while surviving native jobs keep their permits.
This policy only coordinates work in the single supported API worker.
"""
from __future__ import annotations
from threading import Event, Lock


class AudioAdmission:
    def __init__(self):
        self._lock = Lock()
        self._stt = 0
        self._tts = 0
        self._slm = 0
        self._slm_session: str | None = None
        self._slm_cancel = Event()

    def enter_stt(self, session: str | None = None) -> None:
        with self._lock:
            self._stt += 1
            # A new utterance may cancel its own earlier SLM, but a different
            # guest must not invalidate another session's verified response.
            # The global STT admission still defers NEW model jobs on this CPU.
            if self._slm and self._slm_session == session:
                self._slm_cancel.set()

    def cancel_slm(self, session: str) -> bool:
        """Cooperatively cancel only this guest's active model inference."""
        with self._lock:
            if self._slm and self._slm_session == session:
                self._slm_cancel.set()
                return True
            return False

    def leave_stt(self) -> None:
        with self._lock:
            if self._stt < 1:
                raise RuntimeError('Unbalanced STT admission')
            self._stt -= 1

    def try_enter_tts(self) -> bool:
        """Admit one TTS job; STT keeps priority, but TTS may overlap SLM.

        Persistent/cached TTS is short-lived and no longer needs to serialize the
        whole local-model lane. This removes an avoidable cross-session wait while
        preserving the one-TTS and one-SLM bounds.
        """
        with self._lock:
            if self._stt or self._tts:
                return False
            self._tts = 1
            return True

    def leave_tts(self) -> None:
        with self._lock:
            if not self._tts:
                raise RuntimeError('Unbalanced TTS admission')
            self._tts = 0

    def try_enter_slm(self, session: str | None = None) -> bool:
        """Allow one SLM job. STT preempts new work; TTS may overlap it.

        The previous global STT/TTS/SLM mutex forced unrelated sessions through
        a serial queue. STT remains highest priority, while bounded TTS and one
        SLM may run concurrently.
        """
        with self._lock:
            if self._stt or self._slm:
                return False
            self._slm_cancel.clear()
            self._slm_session = session
            self._slm = 1
            return True

    def slm_cancelled(self, session: str | None = None) -> bool:
        with self._lock:
            return self._slm_session != session or self._slm_cancel.is_set()

    def leave_slm(self) -> None:
        with self._lock:
            if not self._slm:
                raise RuntimeError('Unbalanced SLM admission')
            self._slm = 0
            self._slm_session = None

    def detailed_snapshot(self) -> dict[str, int]:
        with self._lock:
            return {'stt_active': self._stt, 'tts_active': self._tts,
                    'slm_active': self._slm}

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {'stt_active': self._stt, 'tts_active': self._tts}
