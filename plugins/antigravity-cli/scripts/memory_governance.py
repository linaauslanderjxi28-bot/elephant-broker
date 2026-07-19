"""Minimal-write and safe-injection policy for the Antigravity EB plugin.

Hooks are allowed to persist only an explicit user memory request. Tool outputs,
raw web pages, and ordinary conversation turns are never durable hook inputs.
"""
from __future__ import annotations

import re
from typing import Any

MAX_MEMORY_TEXT = 2400
_EXPLICIT_MEMORY_RE = re.compile(
    r"(?:\bremember(?:\s+this)?\b|\bsave\s+(?:this|it)\s+(?:to|in)\s+memory\b|"
    r"(?:请|帮我)?(?:记住|记下|保存到记忆|存入记忆|写入记忆))",
    re.IGNORECASE,
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?key|secret|token|password|passwd|authorization)\s*[:=]\s*([^\s,;]{6,})"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{8,}"),
    re.compile(r"\b(?:ghp_|gho_|sk-|AIza)[A-Za-z0-9_-]{12,}\b"),
)


def is_explicit_memory_request(text: Any) -> bool:
    return bool(_EXPLICIT_MEMORY_RE.search(str(text or "")))


def redact_sensitive_text(text: Any, *, limit: int = MAX_MEMORY_TEXT) -> str:
    value = str(text or "")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    value = " ".join(value.split())
    return value[:limit]


def safe_memory_messages(prompt: Any, answer: Any) -> list[dict[str, str]]:
    """Return a bounded, redacted explicit-memory turn or an empty list."""
    prompt_text = redact_sensitive_text(prompt)
    if not is_explicit_memory_request(prompt_text):
        return []
    answer_text = redact_sensitive_text(answer)
    messages = [{"role": "user", "content": prompt_text}]
    if answer_text:
        messages.append({"role": "assistant", "content": answer_text})
    return messages
