"""Operator-pinned local reranker (OpenVINO or sentence-transformers CrossEncoder)."""
from __future__ import annotations
import os
from pathlib import Path
from .openvino_backend import OpenVINOReranker


class LocalReranker:
    def __init__(self, path: str, manifest_path: str = ''):
        root = Path(path)
        if not root.is_dir():
            raise ValueError("Reranker model must already exist on disk")
        from ..model_manifest import model_identity
        self.model_name = model_identity(path, manifest_path)
        if (root / "openvino_model.xml").is_file():
            self.model = OpenVINOReranker(root)
        else:
            os.environ.setdefault("USE_TF", "0")
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(path, device="cpu", local_files_only=True)

    def score(self, query: str, bodies: list[str], *, max_length: int = 512) -> list[float]:
        if isinstance(self.model, OpenVINOReranker):
            return self.model.score(query, bodies, max_length=max_length)
        missing = object()
        previous = getattr(self.model, 'max_length', missing)
        try:
            try:
                self.model.max_length = max_length
            except (AttributeError, TypeError):
                pass
            return [float(x) for x in self.model.predict([(query, body) for body in bodies])]
        finally:
            if previous is not missing:
                self.model.max_length = previous
