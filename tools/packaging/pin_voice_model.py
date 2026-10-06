"""Pin an operator-approved LOCAL Vosk model; no model downloads."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from concierge_kiosk.core.model_manifest import voice_manifest, verify_voice_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model_directory')
    parser.add_argument('manifest')
    parser.add_argument('--write', action='store_true', help='Operator approves the current model bytes')
    args = parser.parse_args()
    destination = Path(args.manifest)
    if args.write:
        model_root = Path(args.model_directory).resolve()
        if destination.resolve().is_relative_to(model_root):
            parser.error('Store the lockfile OUTSIDE the model directory')
        result = voice_manifest(args.model_directory)
        if destination.is_symlink():
            parser.error('Refusing symlink lockfile')
        destination.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    ok = verify_voice_manifest(args.model_directory, args.manifest)
    print(json.dumps({'model_integrity': 'verified' if ok else 'failed'}, sort_keys=True))
    raise SystemExit(0 if ok else 1)

if __name__ == '__main__':
    main()
