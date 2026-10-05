"""Pipecat-facing voice agent adapters.

The package keeps the kiosk's existing speech authorization boundary separate
from the optional Pipecat dependency. Importing the application therefore
continues to work in the legacy profile and in lightweight test environments.
"""

from .speech_gate import SpeechGate

__all__ = ["SpeechGate"]
