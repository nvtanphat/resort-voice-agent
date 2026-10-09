"""Generate and verify the final Whisper/Piper production asset manifest.

Run only on the provisioned appliance after the final model directories and
Piper executable are installed. The manifest contains hashes, never model data.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.voice.runtime.assets import final_voice_manifest, verify_final_voice_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    cfg = load_settings()
    destination = args.manifest
    if destination.is_symlink():
        raise SystemExit('Refusing manifest symlink')
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = final_voice_manifest(cfg)
    destination.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    if not verify_final_voice_manifest(cfg, str(destination)):
        destination.unlink(missing_ok=True)
        raise SystemExit('Final voice asset manifest verification failed')
    print(destination)


if __name__ == '__main__':
    main()
