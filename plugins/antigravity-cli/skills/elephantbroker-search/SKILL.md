---
name: elephantbroker-search
description: Search ElephantBroker-backed memory. Session context is still injected automatically on every prompt via hooks; use this skill when you want explicit recall beyond the automatic context.
---

# ElephantBroker Memory Search

Search memory stored through the ElephantBroker-backed plugin.

## Automatic session search

Session context is searched automatically on every user prompt via the `PreInvocation` hook. You do not need to run this skill to access current-session context.

## What this does

This skill searches ElephantBroker-backed memory and is best for:

- recalling recent session knowledge
- surfacing prior tool and trace context
- searching durable memory previously extracted by EB ingest

## Instructions

Use the plugin search helper:

```bash
${PLUGIN_DIR}/scripts/elephantbroker-search.sh "$ARGUMENTS"
```

Default behavior uses a faster, lighter manual recall path.
Add `--deep` when broader recall is worth the extra latency:

```bash
${PLUGIN_DIR}/scripts/elephantbroker-search.sh "$ARGUMENTS" --deep
```

If the backend stalls, the helper reports a clean timeout message instead of dumping a Python traceback.

## Search modes

- **Default mode** — optimized for routine manual recall and quicker turnaround; if the backend stalls, the helper fails with a short human-readable message instead of a traceback
- **Deep mode (`--deep`)** — broader, slower recall for debugging, cross-session investigation, or exhaustive searches; the helper warns up front that it may take longer

This helper uses `POST /memory/search` on the ElephantBroker backend.

## Understanding results

Results may be grouped as:

- `session` — recent turn-oriented memory
- `trace` — tool / action context
- `graph_context` — a higher-level knowledge snapshot assembled by the plugin for context injection

## When to use

- you want more recall than the automatic prompt hook injected
- you want to search recent work from this session
- you want to probe whether EB has already extracted a fact or preference
- you want to inspect prior tool-related context
