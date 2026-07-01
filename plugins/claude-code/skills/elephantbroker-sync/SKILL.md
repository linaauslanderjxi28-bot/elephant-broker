---
name: elephantbroker-sync
description: Flush staged memory to the active backend and finalize session lifecycle state. This runs automatically at session end.
---

# ElephantBroker Memory Sync

Flush staged memory into the configured backend and finalize session state.

## Instructions

Run the sync script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sync-session-to-graph.py
```

In `eb` mode this means flushing staged prompt, answer, and trace memory through ElephantBroker-native paths. In compatibility modes it uses the active Cognee HTTP or local SDK path.

## When to use

- you want to force an early flush without waiting for session end
- you want recently staged memory to become available to later recall sooner
- you are testing the plugin's EB-mode memory loop manually
- the session is about to end and you want an explicit final flush
