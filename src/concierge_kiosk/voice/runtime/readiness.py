"""Truthful readiness for the *actual* local Vosk decoder.

A Python package, nonempty directory or configured language is not proof that
Kaldi can open the model. The first capability query loads the operator-selected
model into the same bounded cache used by actual speech turns. No downloads.
"""
from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

from concierge_kiosk.voice.session.incremental import _model
from concierge_kiosk.core.model_manifest import check_voice_manifest, voice_manifest_identity


def incremental_voice_ready(*, enabled: bool, model_path: str,
                            manifest_path: str = '', require_manifest: bool = False) -> bool:
    if not enabled or not model_path:
        return False
    root = Path(model_path)
    try:
        if find_spec('vosk') is None or not root.is_dir() or root.is_symlink() or (require_manifest and not manifest_path):
            return False
        identity = None
        if manifest_path:
            if not check_voice_manifest(model_path, manifest_path):
                return False
            identity = voice_manifest_identity(model_path, manifest_path)
            if identity is None:
                return False
        # A successful Kaldi model load, not just an import check. The same
        # cached object is reused by the single admitted STT turn.
        return _model(str(root.resolve()), identity) is not None
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        return False
