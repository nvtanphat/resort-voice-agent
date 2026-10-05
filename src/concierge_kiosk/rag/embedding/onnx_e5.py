"""CPU-only ONNX E5 encoder."""
from __future__ import annotations
from pathlib import Path


class OnnxE5Model:
    """CPU-only ONNX E5 encoder using the model's tokenizer.json.

    The ONNX graph returns token-level hidden states. E5 requires attention-mask
    mean pooling followed by L2 normalization. Query/passage prefixes are applied
    by LocalEmbedder from the pinned profile, not hidden in this backend.
    """

    def __init__(self, root: Path, max_seq_length: int, model_file: str = "model.onnx") -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install the 'embeddings-onnx' optional dependencies to use ONNX embeddings") from exc
        model_path = root / model_file
        tokenizer_path = root / "tokenizer.json"
        if not model_path.is_file() or not tokenizer_path.is_file():
            raise ValueError("ONNX embedding assets require model.onnx (or configured model_file) and tokenizer.json")
        self._np = np
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=max_seq_length)
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.output_names = [item.name for item in self.session.get_outputs()]
        if not {"input_ids", "attention_mask"}.issubset(self.input_names):
            raise ValueError("Unsupported ONNX embedding input contract")

    def encode(self, text: str) -> list[float]:
        np = self._np
        encoded = self.tokenizer.encode(text)
        input_ids = np.asarray([encoded.ids], dtype=np.int64)
        attention_mask = np.asarray([encoded.attention_mask], dtype=np.int64)
        inputs = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self.input_names:
            inputs["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)
        outputs = self.session.run(None, inputs)
        index = self.output_names.index("last_hidden_state") if "last_hidden_state" in self.output_names else 0
        hidden = np.asarray(outputs[index], dtype=np.float32)
        if hidden.ndim != 3 or hidden.shape[0] != 1 or hidden.shape[1] != attention_mask.shape[1]:
            raise ValueError("Unsupported ONNX embedding output contract")
        mask = attention_mask[..., None].astype(np.float32)
        denom = np.maximum(mask.sum(axis=1), 1.0)
        pooled = (hidden * mask).sum(axis=1) / denom
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        if not np.isfinite(norm).all() or float(norm[0, 0]) == 0.0:
            raise ValueError("Invalid ONNX embedding output")
        return (pooled / norm)[0].astype(np.float64).tolist()
