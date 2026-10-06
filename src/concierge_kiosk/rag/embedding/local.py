"""Operator-pinned local embedder (built-in hash, sentence-transformers, ONNX E5, Ollama)."""
from __future__ import annotations
import json
from pathlib import Path
from .hashed import HashedNgramModel
from .ollama import OllamaEmbedder
from .onnx_e5 import OnnxE5Model


class LocalEmbedder:
    PROFILE_FORMAT = "concierge-embedding-profile"

    def __new__(cls, path: str, manifest_path: str = ''):
        # Keep the existing constructor used by bootstrap/ingestion while
        # allowing an operator-pinned loopback Ollama model.  The returned
        # object is a real OllamaEmbedder, so callers retain the normal
        # ``encode_query``/``encode_passage`` contract.
        if cls is LocalEmbedder and path.startswith('ollama://'):
            model = path.removeprefix('ollama://').strip('/')
            return OllamaEmbedder(model=model, manifest_path=manifest_path)
        return super().__new__(cls)

    def __init__(self, path: str, manifest_path: str = '') -> None:
        if path.startswith('ollama://'):
            return
        root = Path(path)
        if not root.is_dir():
            raise ValueError("Embedding model must already exist on disk")
        from concierge_kiosk.core.model_manifest import model_identity
        self.model_name = model_identity(path, manifest_path)
        self.query_prefix = ""
        self.passage_prefix = ""
        self.is_learned = False
        self.backend = "builtin-hash"
        config_path = root / "embedder.json"
        if config_path.is_file():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid built-in embedding configuration") from exc
            self.model = HashedNgramModel(config)
            self._builtin = True
            return

        profile_path = root / "concierge_embedding.json"
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Learned embedding model requires concierge_embedding.json") from exc
        backend = profile.get("backend")
        if (not isinstance(profile, dict) or profile.get("format") != self.PROFILE_FORMAT
                or backend not in {"sentence-transformers", "onnx-e5"}
                or profile.get("normalize_embeddings") is not True
                or not isinstance(profile.get("query_prefix", ""), str)
                or not isinstance(profile.get("passage_prefix", ""), str)):
            raise ValueError("Invalid learned embedding profile")
        max_seq_length = profile.get("max_seq_length", 512)
        if not isinstance(max_seq_length, int) or not 32 <= max_seq_length <= 8192:
            raise ValueError("Invalid learned embedding max_seq_length")
        if backend == "sentence-transformers":
            try:
                from sentence_transformers import SentenceTransformer
            except ModuleNotFoundError as exc:
                raise RuntimeError("Install the 'embeddings' optional dependencies to use a learned model") from exc
            self.model = SentenceTransformer(path, device="cpu", local_files_only=True)
            self.model.max_seq_length = max_seq_length
        else:
            model_file = profile.get("model_file", "model.onnx")
            if not isinstance(model_file, str) or not model_file or "/" in model_file or "\\" in model_file:
                raise ValueError("Invalid ONNX model_file")
            self.model = OnnxE5Model(root, max_seq_length, model_file=model_file)
        self.query_prefix = profile.get("query_prefix", "")
        self.passage_prefix = profile.get("passage_prefix", "")
        self.is_learned = True
        self.backend = backend
        self._builtin = False

    def _encode(self, text: str, prefix: str) -> list[float]:
        payload = prefix + text.strip()
        if not payload.strip():
            raise ValueError("Cannot embed empty text")
        if self._builtin or self.backend == "onnx-e5":
            return self.model.encode(payload)
        value = self.model.encode(payload, normalize_embeddings=True)
        return value.tolist() if hasattr(value, "tolist") else [float(item) for item in value]

    def encode_query(self, text: str) -> list[float]:
        return self._encode(text, self.query_prefix)

    def encode_passage(self, text: str) -> list[float]:
        return self._encode(text, self.passage_prefix)

    def encode(self, text: str) -> list[float]:
        # Backward-compatible neutral encoding. Retrieval and ingestion use the
        # explicit query/passage methods so asymmetric models such as E5 are correct.
        return self.encode_passage(text)
