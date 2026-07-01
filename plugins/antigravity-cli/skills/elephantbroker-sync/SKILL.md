---
name: elephantbroker-sync
description: Flush staged memory to the ElephantBroker backend and finalize session lifecycle state. This runs automatically at session end.
---

# ElephantBroker Memory Sync

Flush staged memory into the ElephantBroker backend and finalize session state.

## Instructions

Run the sync script:

```bash
python3 ${PLUGIN_DIR}/scripts/sync-session-to-graph.py
```

This flushes staged prompt, answer, and trace memory through ElephantBroker-native ingest paths.

## When to use

- you want to force an early flush without waiting for session end
- you want recently staged memory to become available to later recall sooner
- you are testing the plugin's EB-mode memory loop manually
- the session is about to end and you want an explicit final flush
