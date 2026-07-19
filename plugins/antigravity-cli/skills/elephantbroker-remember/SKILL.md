---
name: elephantbroker-remember
description: Store information through the ElephantBroker-backed plugin. Supports short fact storage and longer ingest-style context capture.
---

# ElephantBroker Memory Storage

Store information through the ElephantBroker-backed plugin.

## Modes

### Fact mode
Use for short preferences, constraints, and explicit facts.

Examples:
- "remember that I prefer bash"
- "remember this project uses EB-native memory semantics"
- "remember that retries should stay under 3"

### Document mode
Use for longer notes, project context, and text blocks that should be ingested and extracted.

Examples:
- a paragraph of project notes
- a long context block from a conversation
- a chunk of documentation you want the system to absorb

Interactive hook writes are **not** automatic. The PostInvocation hook persists only when the user's prompt explicitly asks to remember something; it redacts common credential patterns and stores a bounded user/assistant pair through `/memory/ingest-turn`. Raw tool outputs, terminal output, file contents, and web pages are never captured by the hook.

## When to use

- user says "remember this"
- user states a stable preference or constraint
- user provides project-level guidance worth retaining
- user wants a longer context block stored for later recall
