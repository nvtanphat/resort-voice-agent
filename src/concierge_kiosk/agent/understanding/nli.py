"""Optional independent LOCAL NLI verdict for non-critical RAG paraphrases.

No weights or cloud calls are bundled. Fail closed for unknown label mappings,
long/truncated inputs, unavailable model packages and runtime failures. An NLI
score is an uncalibrated signal, not a guarantee of factual correctness.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def local_model_fingerprint(model_path: str) -> tuple | None:
    """Fingerprint explicitly local weights so a revoked/replaced model fails closed.

    This checks presence/integrity of file *identity*, NOT provenance, model
    quality or file contents. The directory and relevant files cannot be links.
    """
    root = Path(model_path) if model_path else None
    if root is None or not root.is_dir() or root.is_symlink():
        return None
    config = root / 'config.json'
    weights = [root / name for name in ('model.safetensors', 'pytorch_model.bin')
               if (root / name).is_file()]
    weights += sorted(root.glob('model-*-of-*.safetensors'))
    if (not config.is_file() or config.is_symlink() or not weights or
            any(not item.is_file() or item.is_symlink() for item in weights)):
        return None
    try:
        return tuple((item.name, item.stat().st_size, item.stat().st_mtime_ns)
                     for item in (config, *weights))
    except OSError:
        return None


@lru_cache(maxsize=1)
def _load_local_nli(model_path: str, fingerprint: tuple):
    root = Path(model_path)
    if not fingerprint or local_model_fingerprint(model_path) != fingerprint:
        raise ValueError('NLI files changed before model load')
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    model = AutoModelForSequenceClassification.from_pretrained(  # nosec B615  # local_files_only=True; no hub download
        str(root), local_files_only=True, trust_remote_code=False)
    tokenizer = AutoTokenizer.from_pretrained(  # nosec B615  # local_files_only=True; no hub download
        str(root), local_files_only=True, trust_remote_code=False)
    labels = {str(label).casefold(): int(index) for index, label in model.config.id2label.items()}
    entailed = [index for label, index in labels.items() if 'entail' in label]
    if len(entailed) != 1 or len(labels) < 2:
        raise ValueError('NLI model must publish an identifiable entailment label')
    model.eval().to('cpu')
    return tokenizer, model, entailed[0]


def verify_local_nli(model_path: str, quote: str, claim: str, *, min_confidence: float = 0.85,
                     manifest_path: str = "", require_manifest: bool = False) -> bool:
    """Return true only for an independently scored, non-truncated premise/claim."""
    if (not model_path or not quote or not claim or
            not 0.5 <= min_confidence <= 1.0):
        return False
    try:
        from concierge_kiosk.agent.models.model_manifest import check_model_manifest
        if require_manifest and not manifest_path:
            return False
        if manifest_path and not check_model_manifest(model_path, manifest_path):
            return False
        import torch
        fingerprint = local_model_fingerprint(model_path)
        if fingerprint is None:
            return False
        tokenizer, model, entailment_id = _load_local_nli(model_path, fingerprint)
        batch = tokenizer(quote, claim, truncation=False, return_tensors='pt')
        # Truncation could remove policy exceptions and reverse the meaning.
        if batch['input_ids'].shape[-1] > min(384, getattr(model.config, 'max_position_embeddings', 384)):
            return False
        with torch.inference_mode():
            scores = torch.softmax(model(**batch).logits[0].float(), dim=-1)
        return bool((scores[entailment_id] >= min_confidence).item() and
                    (scores.argmax() == entailment_id).item())
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, IndexError):
        return False
