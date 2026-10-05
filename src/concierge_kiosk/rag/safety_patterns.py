"""Shared conservative patterns for screening untrusted knowledge text."""
from __future__ import annotations

import re


PROMPT_INJECTION_PATTERN = re.compile(
    r"ignore\s+(?:all\s+|the\s+)?(?:previous|system|developer)\s+(?:instructions|prompts)|"
    r"\b(?:system\s+prompt|developer\s+message|reveal\s+(?:your\s+)?(?:secret|password|api\s+key)|"
    r"send\s+(?:the\s+)?(?:secret|password|api\s+key)\s+to|exfiltrat(?:e|ion))\b|"
    r"bỏ qua\s+(?:mọi\s+|tất cả\s+)?(?:chỉ dẫn|hướng dẫn)\s+(?:trước|hệ thống)|"
    r"忽略(?:之前|系统)指令|이전\s+(?:지시|명령)을\s+무시",
    re.IGNORECASE,
)


__all__ = ["PROMPT_INJECTION_PATTERN"]
