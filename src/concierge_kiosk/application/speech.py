"""Speech boundary adapter so transport can be replaced without agent changes."""
from __future__ import annotations

from collections.abc import Callable

from ..api.voice.routes import register_voice_endpoints


class SpeechService:
    def __init__(self, register: Callable = register_voice_endpoints):
        self._register = register

    def register(self, app, cfg, *, pipecat_dependencies=None, **dependencies) -> None:
        self._register(app, cfg, **dependencies)
        if getattr(cfg, 'voice_transport', 'legacy') == 'pipecat':
            from ..voice.agent.transport import register_pipecat_route
            register_pipecat_route(app, cfg=cfg, **(pipecat_dependencies or {}))
