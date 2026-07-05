# EB migration notes

## Progress
- 2026-06-11 Task 1 complete: stabilized EB adapter surface
- 2026-06-11 Task 2 complete: promoted eb to first-class runtime mode
- 2026-06-11 Task 3 complete: EB session bootstrap path is active
- 2026-06-11 Task 4 complete: prompt/trace staging uses EB mode
- 2026-06-11 Task 5 complete: sync now flushes staged data to EB
- 2026-06-11 Task 6 complete: skill docs rewritten for EB semantics

## `/memory/store` reproduction

```bash
curl -s -X POST http://localhost:8420/memory/store \
  -H "Content-Type: application/json" \
  -H "X-EB-Gateway-ID: gw-enterprise-prod" \
  -d '{"fact":{"text":"hello world","category":"general","scope":"session","memory_class":"episodic","confidence":1.0}}'
```

Observed response:

```json
{"code":"internal_error","message":"All connection attempts failed","field":null,"details":null}
```

### Evidence gathered

- `GET /health/` succeeds
- `POST /memory/search` succeeds
- `POST /sessions/start` succeeds
- `POST /sessions/end` succeeds
- `POST /memory/ingest-messages` succeeds
- `GET /memory/status` returns:

```json
{
  "status": "ok",
  "backend": "elephantbroker",
  "provider": "neo4j+qdrant",
  "facts_count": 0,
  "neo4j_connected": true,
  "qdrant_connected": true,
  "embedding_available": false,
  "llm_available": true
}
```

### Suspected root cause

`/memory/store` appears to execute a direct fact-write path that depends on downstream embedding/vector connectivity. This matches `/memory/status` reporting `embedding_available: false` while search/session endpoints remain available.

Code-path evidence:

- `elephantbroker/api/routes/memory.py` routes `/memory/store` into `MemoryStoreFacade.store(...)`
- `elephantbroker/runtime/memory/facade.py` calls `self._embeddings.embed_text(fact.text)` before vector dedup search and store flow
- `elephantbroker/runtime/adapters/cognee/embeddings.py` uses an HTTP embedding endpoint via `httpx.AsyncClient.post(f"{self._endpoint}/embeddings", ...)`
- the error string `All connection attempts failed` is consistent with an upstream HTTP connectivity failure in that embedding path

### Plugin-side mitigation status

The plugin now uses graceful fallback for explicit fact remember behavior:

- first try `/memory/store`
- on failure, log `store_failed_fallback_to_ingest`
- fall back to `/memory/ingest-messages`

Observed fallback result:

```python
{'mode': 'ingest_fallback', 'result': {'status': 'buffered', 'message': 'Full mode — extraction via context engine'}}
```
