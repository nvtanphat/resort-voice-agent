"""Shared RAG types, language normalization, embedding cache and scoped evidence."""
from __future__ import annotations
import hashlib
import json
import logging
import math
import os
import re
import unicodedata
from urllib.parse import urlsplit
from urllib.request import Request
from datetime import date
from collections import OrderedDict
from threading import RLock
from pathlib import Path
from typing import Protocol
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.core.domain_profile import nlu_policy, rag_policy, supported_languages
from concierge_kiosk.core.terminology import normalize_terminology
from concierge_kiosk.core.domain_vocab import all_category_terms
from concierge_kiosk.runtime.local_http import local_embedding_open
from .tokenization import segment_terms
from .safety_patterns import PROMPT_INJECTION_PATTERN

LANGUAGES = set(supported_languages())
_RAG_POLICY = rag_policy()
_DOMAIN_POLICY = _RAG_POLICY.document_domains
DOMAINS = set(_DOMAIN_POLICY['markers'])
_DOMAIN_MARKERS = {
    domain: tuple(dict.fromkeys((*markers, *all_category_terms(domain))))
    for domain, markers in _DOMAIN_POLICY['markers'].items()
}
_DEFAULT_DOMAIN = _DOMAIN_POLICY['default']
_POLICY_QUALIFIER = re.compile(nlu_policy().qualifier_patterns['grounding'], re.I)
_OLLAMA_MANIFEST_FORMAT = 'concierge-ollama-embedding'
_OLLAMA_DIGEST = re.compile(r'^[0-9a-f]{64}$')


def document_domain(meta: dict) -> str:
    """Prefer the editorial domain; legacy manuals get a deterministic label."""
    if 'domain' in meta:
        domain = meta['domain']
        if domain in DOMAINS:
            return domain
        normalized = domain.replace('-', '_').strip()
        if normalized in DOMAINS:
            return normalized
        return _DEFAULT_DOMAIN
    label = f"{meta.get('document_id', '')} {meta.get('title', '')}".casefold()
    return next((domain for domain, hints in _DOMAIN_MARKERS.items()
                 if any(marker in label for marker in hints)), _DEFAULT_DOMAIN)


def unsafe_knowledge_text(text: str) -> bool:
    """Conservative screening of instruction-style payloads, including legacy data.

    This is defense in depth, not a guarantee against arbitrary prompt injection.
    """
    return bool(PROMPT_INJECTION_PATTERN.search(text))


def semantic_parent_id(property_id: str, language: str, source_id: str,
                       revision: str, section_id: str, section_ordinal: int) -> str:
    """Stable section parent identity independent of translated/display headings."""
    key = json.dumps([property_id, language, source_id, revision, section_id, section_ordinal],
                     ensure_ascii=False)
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]

STOP = set(_RAG_POLICY.token_stopwords)
LOGGER = logging.getLogger(__name__)


def fold_accents(value: str) -> str:
    """Fold Latin diacritics for *fallback* matching without touching CJK/Hangul.

    Vietnamese diacritics carry lexical meaning (for example ``bàn`` vs ``bán``),
    so the primary retrieval channel must keep them.  This helper is only used
    when we intentionally need a no-diacritic fallback representation.
    """
    value = unicodedata.normalize("NFKC", value).casefold().replace("đ", "d")
    return "".join(
        c if ("\uac00" <= c <= "\ud7a3" or "\u4e00" <= c <= "\u9fff")
        else "".join(d for d in unicodedata.normalize("NFKD", c)
                     if not unicodedata.combining(d))
        for c in value
    )


def searchable(value: str, language: str | None = None) -> str:
    """Return the normalized surface form used by policy and phrase checks."""
    return normalize_terminology(unicodedata.normalize("NFKC", value).casefold(), language)


def search_index_text(value: str, language: str | None = None) -> str:
    """Store exact text first and an accent-folded fallback second.

    FTS itself preserves diacritics.  Accented Vietnamese queries therefore use
    the exact channel, while unaccented ASR/keyboard input can still match the
    appended folded form.
    """
    normalized = searchable(value, language).strip()
    terms = segment_terms(normalized, language)
    primary = normalized + (" " + " ".join(terms) if terms else "")
    folded = fold_accents(value).strip()
    return primary if not folded or folded == primary else f"{primary} {folded}"


def tokens(query: str, *, language: str | None = None, limit: int | None = 16,
           stem: bool = False) -> list[str]:
    items = []
    for word in segment_terms(query, language):
        if len(word) <= 1 or fold_accents(word) in STOP:
            continue
        if stem and word.isascii() and len(word) > 4 and word.endswith('s') and not word.endswith('ss'):
            word = word[:-1]
        items.append(word)
    return items[:limit] if limit is not None else items


def fts_expression(query: str, language: str | None = None) -> str:
    # Only quoted, escaped words in FTS expression; never raw user query as syntax.
    return " OR ".join('"' + w.replace('"', '""') + '"'
                       for w in tokens(query, language=language))


class Embedder(Protocol):
    model_name: str

    def encode(self, text: str) -> list[float]: ...
    def encode_query(self, text: str) -> list[float]: ...
    def encode_passage(self, text: str) -> list[float]: ...


class _HashedNgramModel:
    """Tiny deterministic offline dense embedder.

    This is deliberately not presented as a learned semantic model. It provides a
    reproducible CPU-only dense path for typo/substring robustness and keeps the
    production interface identical to a locally pinned SentenceTransformer. A
    learned multilingual model can replace it without changing retrieval code.
    """
    FORMAT = "concierge-hash-embedding"

    def __init__(self, config: dict) -> None:
        if config.get("format") != self.FORMAT:
            raise ValueError("Unsupported built-in embedding format")
        self.dimension = int(config.get("dimension", 384))
        self.min_n = int(config.get("min_n", 2))
        self.max_n = int(config.get("max_n", 5))
        if not (64 <= self.dimension <= 4096 and 1 <= self.min_n <= self.max_n <= 8):
            raise ValueError("Invalid built-in embedding configuration")

    @staticmethod
    def _normalized(text: str) -> str:
        value = unicodedata.normalize("NFKC", text).casefold()
        value = re.sub(r"\s+", " ", value).strip()
        return f" {value} "

    def encode(self, text: str) -> list[float]:
        value = self._normalized(text)
        vector = [0.0] * self.dimension
        features: list[tuple[str, float]] = []
        # Whole tokens receive a little more weight; character n-grams make the
        # vector robust to accents, inflection and minor typing differences in
        # Vietnamese/Korean/Chinese/English without any network/model download.
        for token in re.findall(r"[\w\u4e00-\u9fff\uac00-\ud7a3]+", value, re.UNICODE):
            if len(token) >= 2:
                features.append(("w:" + token, 2.0))
        for n in range(self.min_n, self.max_n + 1):
            for i in range(max(0, len(value) - n + 1)):
                gram = value[i:i+n]
                if gram.strip():
                    features.append((f"c{n}:" + gram, 1.0))
        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8, person=b"ck-embed").digest()
            number = int.from_bytes(digest, "big", signed=False)
            bucket = number % self.dimension
            sign = -1.0 if (number >> 63) else 1.0
            vector[bucket] += sign * weight
        norm = math.sqrt(sum(item * item for item in vector))
        if norm == 0:
            raise ValueError("Cannot embed empty text")
        return [item / norm for item in vector]


class _OnnxE5Model:
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


def validate_ollama_manifest(manifest_path: str, model: str) -> dict[str, object]:
    """Validate the operator-captured identity of a loopback Ollama model."""
    path = Path(manifest_path)
    if (not manifest_path or not path.is_file() or path.is_symlink()
            or path.stat().st_size > 64_000):
        raise ValueError('Ollama embedding manifest file required')
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid Ollama embedding manifest') from exc
    if (not isinstance(payload, dict)
            or set(payload) != {'format', 'model', 'digest', 'dimension'}
            or payload.get('format') != _OLLAMA_MANIFEST_FORMAT
            or not isinstance(payload.get('model'), str)
            or payload['model'] not in {model, f'{model}:latest'}
            or not isinstance(payload.get('digest'), str)
            or not _OLLAMA_DIGEST.fullmatch(payload['digest'])
            or type(payload.get('dimension')) is not int
            or not 1 <= payload['dimension'] <= 4096):
        raise ValueError('Invalid Ollama embedding manifest')
    return payload


class OllamaEmbedder:
    """Loopback Ollama adapter for a pinned BGE-M3 model.

    Ollama owns the model lifecycle; this adapter only accepts a loopback URL,
    validates the response shape, and exposes the same query/passage contract as
    the offline local embedder. It never contacts a remote service.
    """
    is_learned = True
    backend = 'ollama'

    def __init__(self, model: str = 'bge-m3', base_url: str = 'http://127.0.0.1:11434',
                 timeout: float = 15.0, manifest_path: str = '') -> None:
        if not model or len(model) > 128 or any(char in model for char in '\r\n'):
            raise ValueError('Invalid Ollama embedding model')
        parsed = urlsplit(base_url.rstrip('/'))
        if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1', '::1'} \
                or parsed.username or parsed.password or parsed.query or parsed.fragment \
                or parsed.path not in {'', '/'}:
            raise ValueError('Ollama embeddings must use a loopback base URL')
        self.model = model
        self.base_url = base_url.rstrip('/')
        self.timeout = max(0.05, min(float(timeout), 30.0))
        self.manifest = validate_ollama_manifest(manifest_path, model) if manifest_path else None
        self.model_name = f'ollama:{model}'

    def _encode(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Invalid embedding input')
        payload = json.dumps({'model': self.model, 'input': text.strip()}, ensure_ascii=False).encode('utf-8')
        request = Request(self.base_url + '/api/embed', data=payload,
                          headers={'Content-Type': 'application/json'}, method='POST')
        with local_embedding_open(request, timeout=self.timeout) as response:
            raw = response.read(16 * 1024 * 1024)
        obj = json.loads(raw.decode('utf-8'))
        vectors = obj.get('embeddings') if isinstance(obj, dict) else None
        # Ollama releases have returned both JSON float arrays and a compact
        # whitespace-delimited vector string for /api/embed. Accept only the
        # latter's finite numeric form; never evaluate or coerce arbitrary text.
        if isinstance(vectors, list) and len(vectors) == 1 and isinstance(vectors[0], str):
            try:
                vectors[0] = [float(value) for value in vectors[0].split()]
            except ValueError as exc:
                raise ValueError('Invalid Ollama embedding response') from exc
        if (not isinstance(vectors, list) or len(vectors) != 1
                or not _valid_vector(vectors[0])
                or (self.manifest is not None
                    and len(vectors[0]) != self.manifest['dimension'])):
            raise ValueError('Invalid Ollama embedding response')
        return [float(value) for value in vectors[0]]

    def encode_query(self, text: str) -> list[float]:
        return self._encode(text)

    def encode_passage(self, text: str) -> list[float]:
        return self._encode(text)

    def encode(self, text: str) -> list[float]:
        return self._encode(text)


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
        from .model_manifest import model_identity
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
            self.model = _HashedNgramModel(config)
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
            self.model = _OnnxE5Model(root, max_seq_length, model_file=model_file)
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


class _OpenVINOReranker:
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


class LocalReranker:
    def __init__(self, path: str, manifest_path: str = ''):
        root = Path(path)
        if not root.is_dir():
            raise ValueError("Reranker model must already exist on disk")
        from .model_manifest import model_identity
        self.model_name = model_identity(path, manifest_path)
        if (root / "openvino_model.xml").is_file():
            self.model = _OpenVINOReranker(root)
        else:
            os.environ.setdefault("USE_TF", "0")
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(path, device="cpu", local_files_only=True)

    def score(self, query: str, bodies: list[str], *, max_length: int = 512) -> list[float]:
        if isinstance(self.model, _OpenVINOReranker):
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


def _valid_vector(value: object) -> bool:
    return (isinstance(value, (list, tuple)) and 0 < len(value) <= 4096
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) for v in value)
            and any(v != 0 for v in value))


# Bounded per-model query cache. Never cache search results or authorization:
# access, publication dates and current revisions must be checked every request.
_EMBED_CACHE: OrderedDict[tuple[int, str, str], tuple[Embedder, tuple[float, ...]]] = OrderedDict()
_EMBED_CACHE_LOCK = RLock()
_EMBED_CACHE_CAPACITY = 128

# Decode each approved stored vector at most once per content fingerprint.
# Only vector data is cached. Property/date/classification/revision access is
# ALWAYS checked by the SQL query before the cache can be consulted.
_DOCUMENT_VECTOR_CACHE: OrderedDict[str, tuple[float, ...]] = OrderedDict()
_DOCUMENT_VECTOR_LOCK = RLock()
_DOCUMENT_VECTOR_CAPACITY = 1024


def decoded_embedding(raw: str) -> tuple[float, ...]:
    fingerprint = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    with _DOCUMENT_VECTOR_LOCK:
        cached = _DOCUMENT_VECTOR_CACHE.get(fingerprint)
        if cached is not None:
            _DOCUMENT_VECTOR_CACHE.move_to_end(fingerprint)
            return cached
    vector = json.loads(raw)
    if not _valid_vector(vector):
        raise ValueError('Invalid stored embedding')
    decoded = tuple(float(number) for number in vector)
    with _DOCUMENT_VECTOR_LOCK:
        _DOCUMENT_VECTOR_CACHE[fingerprint] = decoded
        _DOCUMENT_VECTOR_CACHE.move_to_end(fingerprint)
        while len(_DOCUMENT_VECTOR_CACHE) > _DOCUMENT_VECTOR_CAPACITY:
            _DOCUMENT_VECTOR_CACHE.popitem(last=False)
    return decoded


def query_embedding(embedder: Embedder, query: str) -> list[float]:
    key = (id(embedder), embedder.model_name, query)
    with _EMBED_CACHE_LOCK:
        cached = _EMBED_CACHE.get(key)
        if cached is not None and cached[0] is embedder:
            _EMBED_CACHE.move_to_end(key)
            return list(cached[1])
    encoder = getattr(embedder, "encode_query", None) or embedder.encode
    vector = encoder(query)
    if not _valid_vector(vector):
        raise ValueError('Invalid query embedding')
    with _EMBED_CACHE_LOCK:
        _EMBED_CACHE[key] = (embedder, tuple(vector))
        _EMBED_CACHE.move_to_end(key)
        while len(_EMBED_CACHE) > _EMBED_CACHE_CAPACITY:
            _EMBED_CACHE.popitem(last=False)
    return list(vector)


def evidence_passage(body: str, query: str, *, language: str | None = None,
                     max_chars: int | None = None, require_term_overlap: bool = True) -> str:
    """Return a relevant local passage, never arbitrary first-N-character tails.

    User-supplied numbers are ranking hints, not a hard candidate filter.  A guest
    may mention party size, child age or ``3pm`` while the verified policy uses no
    such number (or formats the time as ``15:00``).  Numeric truth is enforced at
    claim generation/verification rather than by throwing away otherwise relevant
    evidence here.
    """
    if max_chars is None:
        max_chars = int((_RAG_POLICY.grounding_budgets.get("evidence_budget_chars") or {})["fact"])
    terms = set(tokens(query, language=language, limit=None, stem=True))
    numbers = set(re.findall(r'\d+(?:[.,:/-]\d+)*', query))
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    if not paragraphs:
        return body if len(body) <= max_chars else ''
    spans = []
    for paragraph in paragraphs:
        qualified = bool(_POLICY_QUALIFIER.search(paragraph))
        if len(paragraph) <= max_chars:
            spans.append(paragraph)
            # A compact paragraph can still contain many independent facts.
            # Also rank complete sentence-sized evidence so a focused guest
            # question does not inherit unrelated claims from the same chunk.
            # Never split policy/exception text, where the qualifier must remain
            # attached to the rule it constrains.
            if not qualified:
                sentences = re.split(r'(?<=[.!?。！？])(?:\s+|(?=\S))', paragraph)
                if len(sentences) > 1:
                    spans.extend(sentence.strip() for sentence in sentences
                                 if sentence.strip() and len(sentence.strip()) <= max_chars)
        else:
            # Do not separate a rule from its exception just to satisfy a
            # character budget. A different complete paragraph may still fit.
            if qualified:
                continue
            # Never slice an unfinished policy sentence: an exception/negation
            # could be in its removed tail. Abstain if no complete span fits.
            sentences = re.split(r'(?<=[.!?。！？])(?:\s+|(?=\S))', paragraph)
            for sentence in sentences:
                sentence = sentence.strip()
                if sentence and len(sentence) <= max_chars:
                    spans.append(sentence)
    if not spans:
        return ''
    # Never manufacture a citation passage from an unrelated first/shortest
    # span. At least one substantive query term must occur in the selected span.
    if terms and require_term_overlap:
        folded_terms = {fold_accents(term) for term in terms}
        spans = [span for span in spans
                if (terms.intersection(tokens(span, language=language, limit=None, stem=True))
                     or folded_terms.intersection(
                         fold_accents(term) for term in tokens(span, language=language, limit=None, stem=True)))]
        if not spans:
            return ''
    def rank(span: str) -> tuple[int, int, int]:
        span_terms = set(tokens(span, language=language, limit=None, stem=True))
        found = set(re.findall(r'\d+(?:[.,:/-]\d+)*', span))
        return (int(numbers.issubset(found)), len(terms & span_terms), -len(span))
    selected = max(spans, key=rank)
    return selected


def retrieve_parent_context(store: Store, *, property_id: str, language: str,
                            source_id: str, revision: str, effective_date: str,
                            parent_id: str = '', section_id: str = '',
                            heading: str = '', max_chars: int = 2400) -> str:
    """Expand only the same currently authorized section/revision/property.

    New rows are joined by opaque parent/section identity. Heading fallback exists
    only for migrated legacy rows with no section metadata.
    """
    if language not in LANGUAGES or not 100 <= max_chars <= 4000:
        raise ValueError('Invalid parent context request')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', effective_date):
        raise ValueError('Explicit effective date required')
    with store.connection() as con:
        if parent_id:
            # Reassemble only currently effective children. The parent body is
            # a structural cache and may contain an expired promotion beside a
            # permanent fact for the same entity.
            rows = con.execute(
                "SELECT k.body FROM knowledge k JOIN knowledge_parents p ON p.id=k.parent_id "
                "AND p.revision=k.revision WHERE k.parent_id=? AND k.property_id=? AND k.language=? "
                "AND k.source=? AND k.revision=? AND p.classification='public' AND p.active=1 "
                "AND k.classification='public' AND k.active=1 AND k.effective_from<=? "
                "AND (k.effective_to IS NULL OR k.effective_to>=?) ORDER BY k.section_ordinal,k.id",
                (parent_id, property_id, language, source_id, revision, effective_date, effective_date)
            ).fetchall()
        elif section_id:
            rows = con.execute(
                "SELECT k.body FROM knowledge k JOIN knowledge_parents p ON p.id=k.parent_id "
                "AND p.revision=k.revision WHERE k.property_id=? AND k.language=? AND k.source=? "
                "AND k.revision=? AND p.section_id=? AND p.classification='public' AND p.active=1 "
                "AND k.classification='public' AND k.active=1 AND k.effective_from<=? "
                "AND (k.effective_to IS NULL OR k.effective_to>=?) ORDER BY k.section_ordinal,k.id",
                (property_id, language, source_id, revision, section_id,
                 effective_date, effective_date)
            ).fetchall()
        else:
            rows = []
        if rows:
            combined = '\n\n'.join(row['body'] for row in rows)
            return combined[:max_chars] if not unsafe_knowledge_text(combined) else ''
        # Migrated pre-rows may have no section identity. Their fallback remains
        # property/language/revision/date scoped and cannot cross a live parent.
        if not heading:
            return ''
        rows = con.execute(
            "SELECT body FROM knowledge WHERE property_id=? AND language=? "
            "AND source=? AND revision=? AND heading=? AND parent_id='' AND section_id='' "
            "AND classification='public' AND active=1 AND effective_from<=? "
            "AND (effective_to IS NULL OR effective_to>=?) ORDER BY id LIMIT 16",
            (property_id, language, source_id, revision, heading,
             effective_date, effective_date)
        ).fetchall()
    combined = '\n\n'.join(row['body'] for row in rows)
    return combined[:max_chars] if not unsafe_knowledge_text(combined) else ''


def cosine(a: list[float], b: list[float]) -> float:
    if not _valid_vector(a) or not _valid_vector(b) or len(a) != len(b):
        return -1.0
    norm = math.sqrt(sum(v * v for v in a)) * math.sqrt(sum(v * v for v in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else -1.0
