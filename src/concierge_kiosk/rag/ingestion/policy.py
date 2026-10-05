"""Chunking policy identity (hashed into signed knowledge releases)."""
from __future__ import annotations
import hashlib
import json

CHUNK_POLICY = {
    "version": 2,
    "tokenizer": "regex-unicode",
    "max_tokens": 256,
    "max_chars": 900,
    "overlap_chars": 80,
    "parent_budget_chars": 2400,
}


def chunk_policy_hash(policy: dict | None = None) -> str:
    payload = json.dumps(policy or CHUNK_POLICY, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
