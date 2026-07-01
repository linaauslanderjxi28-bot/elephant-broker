#!/usr/bin/env python3
"""Build a memory anchor before context-window compaction.

Runs on the PreCompact hook. Pulls a compact summary from session memory
and emits a JSON payload with ``hookSpecificOutput.additionalContext``
so the compactor preserves a memory anchor.

All calls go through the EB backend (``POST /memory/search``).
"""

import json
import os
import re
import sys

# Add scripts dir to path for helper imports
sys.path.insert(0, os.path.dirname(__file__))
from _plugin_common import hook_log, load_resolved, recall_via_http

_MIN_WORD_LEN = 3
_SESSION_TOP_K = 5
_TRACE_TOP_K = 8
_GRAPH_TOP_K = 3


def _load_resolved_fields() -> tuple[str, str]:
    """Return (session_id, dataset) from resolved cache or config."""
    resolved = load_resolved()
    session_id = resolved.get("session_id", "")
    dataset = resolved.get("dataset", "")
    if not session_id or not dataset:
        from config import get_dataset, get_session_id, load_config
        config = load_config()
        session_id = session_id or get_session_id(config)
        dataset = dataset or get_dataset(config)
    return session_id, dataset


def _extract_query_words(entries: list, max_words: int = 20) -> str:
    """Pull keyword-dense query from recent entries for graph-context search."""
    words: list[str] = []
    for entry in entries[-3:]:
        if not isinstance(entry, dict):
            continue
        blob = " ".join(
            str(entry.get(f, ""))
            for f in ("text", "origin_function", "source")
        )
        for w in re.findall(r"\b\w+\b", blob.lower()):
            if len(w) >= _MIN_WORD_LEN:
                words.append(w)
                if len(words) >= max_words:
                    return " ".join(words)
    return " ".join(words)


def _format_session_section(entries: list) -> str:
    lines = ["### Session Memory (recent turns)"]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or entry.get("answer") or "").strip()
        if not text:
            continue
        short = text[:300]
        if len(text) > 300:
            short += "..."
        lines.append(f"- {short}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_trace_section(entries: list) -> str:
    lines = ["### Agent Trace (tool calls & feedback)"]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        origin = entry.get("origin_function", "?")
        text = str(entry.get("text") or "").strip()
        if text:
            lines.append(f"- {origin}: {text[:200]}")
        else:
            lines.append(f"- {origin}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_graph_entries(entries: list) -> str:
    lines = ["### Knowledge Graph Snapshot"]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("text") or entry.get("content") or entry.get("answer") or "")
        short = content[:400] + "..." if len(content) > 400 else content
        if short.strip():
            lines.append(short)
    return "\n".join(lines) if len(lines) > 1 else ""


def _run():
    session_id, dataset = _load_resolved_fields()
    if not session_id:
        hook_log("precompact_no_session_id")
        return

    # First pull session+trace so we can derive a query from them.
    seed_results = recall_via_http(
        "", session_id=session_id, top_k=_TRACE_TOP_K, scope=["session", "trace"], timeout=12.0
    )
    session_entries = [
        r for r in seed_results if isinstance(r, dict) and r.get("source") in ("session", None)
    ]
    trace_entries = [r for r in seed_results if isinstance(r, dict) and r.get("source") == "trace"]

    session_entries = session_entries[-_SESSION_TOP_K:]
    trace_entries = trace_entries[-_TRACE_TOP_K:]

    query = _extract_query_words(session_entries + trace_entries)

    graph_entries: list = []
    if query:
        ctx = recall_via_http(query, session_id=session_id, top_k=_GRAPH_TOP_K, scope=["graph"], timeout=12.0)
        graph_entries = [r for r in ctx if isinstance(r, dict)] if ctx else []

    sections = []
    if session_entries:
        s = _format_session_section(session_entries)
        if s:
            sections.append(s)
    if trace_entries:
        s = _format_trace_section(trace_entries)
        if s:
            sections.append(s)
    if graph_entries:
        s = _format_graph_entries(graph_entries)
        if s:
            sections.append(s)

    if not sections:
        hook_log("precompact_empty")
        return

    header = (
        "## ElephantBroker Memory Anchor\n"
        "Preserved context from session, agent trace, and knowledge graph:\n"
    )
    anchor = header + "\n\n".join(sections)

    hook_log(
        "precompact_anchor",
        {
            "session_entries": len(session_entries),
            "trace_entries": len(trace_entries),
            "graph": len(graph_entries),
        },
    )

    # Output JSON with additionalContext for the compactor to preserve.
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": anchor,
        },
        "suppressOutput": False,
    }
    # Also print the markdown block on stdout as a human-readable fallback.
    print(json.dumps(output))


def main():
    # Read stdin (PreCompact payload); we don't use the body, just the trigger.
    sys.stdin.read()

    try:
        _run()
    except Exception as exc:
        hook_log("precompact_run_exception", {"error": str(exc)[:200]})


if __name__ == "__main__":
    main()
