"""Download the pinned INT4 multilingual BGE reranker and write its manifest.

The binary model is intentionally kept out of source control.  Provision it
once on the target machine, then set CONCIERGE_RERANK_MODEL_PATH and
CONCIERGE_RERANK_MANIFEST_PATH to the generated files.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from huggingface_hub import snapshot_download

from concierge_kiosk.rag.model_manifest import rag_model_manifest


REPOSITORY = "EmbeddedLLM/bge-reranker-v2-m3-int4-ov"
REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
FILES = [
    "config.json",
    "openvino_model.bin",
    "openvino_model.xml",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-dir",
        default="models/reranker/bge-reranker-v2-m3-int4-ov",
    )
    parser.add_argument(
        "--manifest",
        default="models/reranker/bge-reranker-v2-m3-int4-ov.manifest.json",
    )
    args = parser.parse_args()
    model_dir = Path(args.model_dir)
    manifest_path = Path(args.manifest)
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite {manifest_path}")
    snapshot_download(
        REPOSITORY,
        revision=REVISION,
        local_dir=str(model_dir),
        allow_patterns=FILES,
    )
    # Hugging Face stores download metadata under local_dir/.cache. It is not
    # part of the model and must not enter the integrity manifest.
    shutil.rmtree(model_dir / ".cache", ignore_errors=True)
    (model_dir / ".gitignore").unlink(missing_ok=True)
    payload = rag_model_manifest(str(model_dir))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Provisioned {REPOSITORY}@{REVISION} -> {model_dir}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
