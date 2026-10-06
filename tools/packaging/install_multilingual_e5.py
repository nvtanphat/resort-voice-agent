"""Install a pinned multilingual E5 embedding model into the local model tree.

This command is intentionally explicit and operator-run. Runtime code never
contacts Hugging Face. After download, every local file is copied into an
ordinary directory, a Concierge embedding profile is written, and the entire
artifact tree is pinned by the existing SHA-256 RAG model manifest.

Examples:
  python -m tools.packaging.install_multilingual_e5
  python -m tools.packaging.install_multilingual_e5 --backend onnx-int8-avx512
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from concierge_kiosk.core.model_manifest import rag_model_manifest

REPO_ID = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
PROFILE_FORMAT = "concierge-embedding-profile"
UPSTREAM_SHA256 = {
    "sentence-transformers": {"model.safetensors": "1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477"},
    "onnx-e5": {"model_qint8_avx512_vnni.onnx": "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

ST_PATTERNS = [
    "1_Pooling/*",
    "config.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
]
ONNX_PATTERNS = [
    "onnx/config.json",
    "onnx/model_qint8_avx512_vnni.onnx",
    "onnx/sentencepiece.bpe.model",
    "onnx/special_tokens_map.json",
    "onnx/tokenizer.json",
    "onnx/tokenizer_config.json",
]


def _copy_tree(source: Path, target: Path, *, flatten_onnx: bool) -> None:
    if target.exists():
        if any(target.iterdir()):
            raise ValueError(f"Target must be absent or empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    base = source / "onnx" if flatten_onnx else source
    if not base.is_dir():
        raise RuntimeError("Downloaded model snapshot is incomplete")
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            # Never preserve hub/cache symlinks in a pinned deployment artifact.
            continue
        if path.is_file():
            relative = path.relative_to(base)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)


def install(target: Path, manifest: Path, backend: str) -> dict[str, object]:
    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install huggingface-hub (or .[embeddings]) for the one-time model download") from exc

    patterns = ST_PATTERNS if backend == "sentence-transformers" else ONNX_PATTERNS
    with tempfile.TemporaryDirectory(prefix="concierge-e5-download-") as directory:
        snapshot = Path(snapshot_download(
            repo_id=REPO_ID,
            revision=REVISION,
            allow_patterns=patterns,
            local_dir=directory,
        ))
        _copy_tree(snapshot, target, flatten_onnx=backend != "sentence-transformers")

    if backend == "sentence-transformers":
        runtime_backend = "sentence-transformers"
        model_file = None
        source_model = target / "model.safetensors"
        expected = UPSTREAM_SHA256[runtime_backend]["model.safetensors"]
        if not source_model.is_file() or _sha256(source_model) != expected:
            raise RuntimeError("Downloaded E5 safetensors bytes do not match the pinned upstream SHA-256")
    else:
        runtime_backend = "onnx-e5"
        source_model = target / "model_qint8_avx512_vnni.onnx"
        expected = UPSTREAM_SHA256[runtime_backend]["model_qint8_avx512_vnni.onnx"]
        if not source_model.is_file() or _sha256(source_model) != expected:
            raise RuntimeError("Downloaded E5 ONNX bytes do not match the pinned upstream SHA-256")
        model_file = "model.onnx"
        source_model.rename(target / model_file)

    profile = {
        "format": PROFILE_FORMAT,
        "backend": runtime_backend,
        "source_repo": REPO_ID,
        "source_revision": REVISION,
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
        "normalize_embeddings": True,
        "max_seq_length": 512,
        "embedding_dimension": 384,
    }
    if model_file:
        profile["model_file"] = model_file
    (target / "concierge_embedding.json").write_text(
        json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    pinned = rag_model_manifest(str(target))
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(pinned, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {
        "repo_id": REPO_ID,
        "revision": REVISION,
        "backend": runtime_backend,
        "model_path": str(target),
        "manifest_path": str(manifest),
        "file_count": len(pinned["files"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("models/embeddings/multilingual-e5-small"),
    )
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument(
        "--backend",
        choices=("sentence-transformers", "onnx-int8-avx512"),
        default="sentence-transformers",
        help="Use the portable PyTorch/SentenceTransformer artifact by default; ONNX INT8 is smaller but AVX512/VNNI-specific.",
    )
    args = parser.parse_args()
    manifest = args.manifest or args.target.with_name(args.target.name + ".manifest.json")
    result = install(args.target, manifest, args.backend)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
