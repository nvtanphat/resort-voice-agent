"""OpenVINO CPU cross-encoder backend."""
from __future__ import annotations
import os
from pathlib import Path


class OpenVINOReranker:
    """CPU reranker for a locally provisioned OpenVINO BGE cross-encoder."""

    def __init__(self, root: Path) -> None:
        # Some installations include a partial TensorFlow package.  Transformers
        # only needs its PyTorch path here; selecting it before import prevents a
        # broken optional TensorFlow install from making the reranker unusable.
        os.environ.setdefault("USE_TF", "0")
        from openvino import Core
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            str(root), local_files_only=True, use_fast=True,
        )  # nosec B615: only the already-pinned local model directory is read
        core = Core()
        model = core.read_model(str(root / "openvino_model.xml"))
        self.compiled = core.compile_model(model, "CPU")
        self.input_names = {
            str(port.get_any_name()) for port in self.compiled.inputs
        }
        self.output = self.compiled.outputs[0]

    def score(self, query: str, bodies: list[str], *, max_length: int = 512) -> list[float]:
        if not bodies:
            return []
        encoded = self.tokenizer(
            [query] * len(bodies), bodies,
            padding=True, truncation=True, max_length=max_length,
            return_tensors="np",
        )
        inputs = {
            name: encoded[name]
            for name in self.input_names
            if name in encoded
        }
        if not inputs or "input_ids" not in inputs or "attention_mask" not in inputs:
            raise ValueError("OpenVINO reranker input contract is incomplete")
        result = self.compiled(inputs)
        values = result[self.output] if self.output in result else next(iter(result.values()))
        import numpy as np
        scores = np.asarray(values, dtype=np.float32).reshape(len(bodies), -1)
        if scores.shape[1] < 1 or not np.isfinite(scores[:, 0]).all():
            raise ValueError("Invalid OpenVINO reranker output")
        return [float(value) for value in scores[:, 0]]
