"""Operator CLI: generate and/or verify a local SHA-256 model lockfile.

Requires operator review: this is integrity pinning, not authenticity or quality.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from concierge_kiosk.agent.models.model_manifest import model_manifest, verify_model_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('model_dir')
    parser.add_argument('manifest_file')
    parser.add_argument('--write', action='store_true', help='Explicitly generate/overwrite pin file')
    args = parser.parse_args()
    path = Path(args.manifest_file)
    if args.write:
        if path.is_symlink():
            raise SystemExit('Refusing manifest symlink')
        path.write_text(json.dumps(model_manifest(args.model_dir), indent=2) + '\n', encoding='utf-8')
    good = verify_model_manifest(args.model_dir, args.manifest_file)
    print(json.dumps({'integrity_verified': good, 'quality_verified': False}))
    if not good:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
