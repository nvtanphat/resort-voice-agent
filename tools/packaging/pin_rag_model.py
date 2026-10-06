"""Create/verify an offline integrity manifest for embedding or reranking assets."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from concierge_kiosk.core.model_manifest import rag_model_manifest, verify_rag_model_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description='Pin a local RAG model directory')
    parser.add_argument('model_dir')
    parser.add_argument('manifest_file')
    args = parser.parse_args()
    destination = Path(args.manifest_file)
    if destination.exists():
        raise FileExistsError('Refusing to overwrite an existing model manifest')
    payload = rag_model_manifest(args.model_dir)
    destination.write_text(json.dumps(payload, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    if not verify_rag_model_manifest(args.model_dir, str(destination)):
        destination.unlink(missing_ok=True)
        raise RuntimeError('Generated RAG model manifest did not verify')
    print('Pinned local RAG model:', destination)


if __name__ == '__main__':
    main()
