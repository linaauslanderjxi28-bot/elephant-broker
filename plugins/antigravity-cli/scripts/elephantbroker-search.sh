#!/usr/bin/env bash
# Search ElephantBroker-backed memory (session or broader durable context).
#
# Usage:
#   elephantbroker-search.sh <query> [top_k] [--session | --graph] [--deep]
#
# Default behavior uses a faster, lighter manual recall mode.
# --deep: run the broader, slower search path when maximum recall is worth the latency.
#
# --session: prefer session-scoped recall
# --graph:   prefer durable/non-session recall
# No flag:   try session-oriented recall first, then broader recall
#
# Runtime behavior:
#   - eb mode: uses ElephantBroker /memory/search
#   - http mode: uses Cognee HTTP endpoints
#   - local_sdk compatibility mode: falls back to the locally configured runtime assumptions
#
# Configuration:
#   Resolves session ID and dataset from runtime state when possible.

set -euo pipefail

PLUGIN_DIR="${HOME}/.cognee-plugin"
runtime_json="$(python3 - <<'PY' "${PLUGIN_DIR}" 2>/dev/null || true
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

plugin_dir = pathlib.Path(sys.argv[1])
import os
service_url = (os.environ.get("COGNEE_SERVICE_URL") or os.environ.get("COGNEE_LOCAL_API_URL") or "http://localhost:8011").strip()
api_key = (os.environ.get("COGNEE_API_KEY") or "").strip()
agent_name = (os.environ.get("COGNEE_AGENT_NAME") or "").strip()
is_eb = False
if (os.environ.get("EB_MODE") or "").strip().lower() in ("1", "true", "yes"):
    is_eb = True
elif ":8420" in service_url:
    is_eb = True
if agent_name:
    if agent_name.endswith("@cognee.agent"):
        agent_name = agent_name[: -len("@cognee.agent")]
    if not agent_name.endswith("_claude"):
        agent_name = f"{agent_name}_claude"

if not api_key and service_url and agent_name and not is_eb:
    cache_path = plugin_dir / "agent_keys.json"
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            entries = cache.get("entries", {}) if isinstance(cache, dict) else {}
            if isinstance(entries, dict):
                normalized_url = service_url.rstrip("/")
                key = f"{normalized_url}::{agent_name}"
                chosen = entries.get(key)
                if isinstance(chosen, dict):
                    api_key = str(chosen.get("api_key") or "").strip()
                else:
                    for entry in entries.values():
                        if not isinstance(entry, dict):
                            continue
                        name = str(entry.get("agent_name") or "").strip()
                        url = str(entry.get("service_url") or "").strip().rstrip("/")
                        if name == agent_name and url == normalized_url:
                            api_key = str(entry.get("api_key") or "").strip()
                            break
        except Exception:
            pass

session_id = ""
dataset = ""
if service_url and (api_key or is_eb):
    try:
        query = ""
        session_key = (os.environ.get("COGNEE_SESSION_KEY") or "").strip()
        if session_key and not is_eb:
            query = "?agent_session_name=" + urllib.parse.quote(session_key, safe="")
        if not is_eb:
            req = urllib.request.Request(
                service_url.rstrip("/") + "/api/v1/agents/connections/me" + query,
                headers={"X-Api-Key": api_key},
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
            if isinstance(payload, dict):
                agent = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
                if isinstance(agent, dict):
                    session_id = str(agent.get("session_id") or "").strip()
                    datasets = agent.get("datasets") if isinstance(agent.get("datasets"), list) else []
                    for item in datasets:
                        if isinstance(item, dict):
                            name = str(item.get("name") or "").strip()
                            if name:
                                dataset = name
                                break
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        pass

print(json.dumps({"session_id": session_id, "dataset": dataset, "is_eb": is_eb}))
PY
)"

DATASET="$(python3 - <<'PY' "${runtime_json}" 2>/dev/null || true
import json, sys
try:
    print((json.loads(sys.argv[1] or "{}").get("dataset") or "").strip())
except Exception:
    pass
PY
)"
SESSION_ID="$(python3 - <<'PY' "${runtime_json}" 2>/dev/null || true
import json, sys
try:
    print((json.loads(sys.argv[1] or "{}").get("session_id") or "").strip())
except Exception:
    pass
PY
)"
[ -z "$DATASET" ] && DATASET="${COGNEE_PLUGIN_DATASET:-claude_sessions}"
[ -z "$SESSION_ID" ] && SESSION_ID="${COGNEE_SESSION_ID:-claude_session}"

QUERY="${1:-}"
TOP_K="3"
MODE="auto"
DEPTH_MODE="fast"

shift_count=0
if [ $# -gt 0 ]; then
    shift_count=1
fi

for arg in "${@:2}"; do
    case "$arg" in
        --session) MODE="session" ;;
        --graph)   MODE="graph" ;;
        --deep)    DEPTH_MODE="deep" ;;
        --entity-type=*) ENTITY_TYPE="${arg#*=}" ;;
        --entity-type) ENTITY_TYPE="$2"; shift ;;
        '' ) ;;
        * )
            if [[ "$arg" =~ ^[0-9]+$ ]]; then
                TOP_K="$arg"
            fi
            ;;
    esac
done

if [ -z "$QUERY" ]; then
    echo "Error: no query provided" >&2
    exit 1
fi

if [ "$DEPTH_MODE" = "deep" ]; then
    echo "Running deep ElephantBroker search; this may take longer..." >&2
fi

_IS_EB=false
if [ "${EB_MODE:-}" = "true" ] || [ "${EB_MODE:-}" = "1" ] || [ "${EB_MODE:-}" = "yes" ]; then
    _IS_EB=true
elif echo "${COGNEE_SERVICE_URL:-}" | grep -q ":8420"; then
    _IS_EB=true
fi

python3 - <<'PY' "$MODE" "$QUERY" "$TOP_K" "$DATASET" "$SESSION_ID" "$_IS_EB" "$DEPTH_MODE" "${ENTITY_TYPE:-}"
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

mode, query, top_k, dataset, session_id, is_eb_str, depth_mode, entity_type = (sys.argv[1:8] + [""] * 8)[:8]
is_eb = sys.argv[6].lower() == "true"
depth_mode = sys.argv[7].lower()
request_timeout = 45 if depth_mode == "deep" else 35
service_url = (os.environ.get("COGNEE_SERVICE_URL") or os.environ.get("COGNEE_LOCAL_API_URL") or "http://localhost:8011").strip().rstrip("/")
api_key = (os.environ.get("COGNEE_API_KEY") or "").strip()
agent_name = (os.environ.get("COGNEE_AGENT_NAME") or "").strip()

_is_valid_uuid = bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', session_id, re.I)) if is_eb else True

if not api_key and service_url and agent_name and not is_eb:
    plugin_dir = pathlib.Path.home() / ".cognee-plugin"
    cache_path = plugin_dir / "agent_keys.json"
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            entries = cache.get("entries", {}) if isinstance(cache, dict) else {}
            if isinstance(entries, dict):
                normalized_url = service_url.rstrip("/")
                if agent_name.endswith("@cognee.agent"):
                    agent_name = agent_name[: -len("@cognee.agent")]
                if not agent_name.endswith("_claude"):
                    agent_name = f"{agent_name}_claude"
                key = f"{normalized_url}::{agent_name}"
                chosen = entries.get(key)
                if isinstance(chosen, dict):
                    api_key = str(chosen.get("api_key") or "").strip()
        except Exception:
            pass

if not service_url:
    raise SystemExit("ElephantBroker/Cognee service URL not configured")
if not is_eb and not api_key:
    raise SystemExit("Cognee HTTP mode requires COGNEE_API_KEY or a cached agent key")

headers = {"Content-Type": "application/json"}
if is_eb:
    gateway_id = (os.environ.get("EB_GATEWAY_ID") or "").strip()
    agent_key = (os.environ.get("EB_AGENT_KEY") or "").strip()
    if gateway_id:
        headers["X-EB-Gateway-ID"] = gateway_id
    if agent_key:
        headers["X-EB-Agent-Key"] = agent_key
    auth_token = (os.environ.get("EB_AUTH_TOKEN") or "").strip()
    if auth_token:
        headers["X-EB-Auth-Token"] = auth_token
else:
    headers["X-Api-Key"] = api_key

def post_json(path, payload):
    req = urllib.request.Request(
        service_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=request_timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "null")

try:
    if is_eb:
        SEARCH_PATH = "/memory/search"

        def eb_search(req_query, req_scope=None, req_max_results=None, include_session_id=True, req_entity_type=None):
            payload = {
                "query": req_query,
                "max_results": int(req_max_results or top_k),
                "min_score": 0.0,
            }
            if req_scope == "session":
                payload["scope"] = "session"
            elif req_scope == "graph":
                payload["scope"] = "global"
            if req_entity_type:
                payload["entity_type"] = req_entity_type
            if include_session_id and _is_valid_uuid and session_id:
                payload["session_id"] = session_id
            return post_json(SEARCH_PATH, payload)

        if depth_mode == "deep":
            if mode == "graph":
                result = eb_search(query, "graph", req_max_results=top_k, req_entity_type=entity_type)
            elif mode == "session":
                result = eb_search(query, "session", req_max_results=top_k)
            else:
                session_result = eb_search(query, "session", req_max_results=top_k)
                if session_result and session_result != []:
                    result = session_result
                else:
                    result = eb_search(query, "graph", req_max_results=top_k, req_entity_type=entity_type)
        else:
            fast_top_k = min(int(top_k), 2)
            if mode == "session":
                result = eb_search(query, "session", req_max_results=fast_top_k, include_session_id=False)
            elif mode == "graph":
                result = eb_search(query, None, req_max_results=fast_top_k, include_session_id=False, req_entity_type=entity_type)
            else:
                result = eb_search(query, None, req_max_results=fast_top_k, include_session_id=False, req_entity_type=entity_type)
    else:
        if mode == "graph":
            result = post_json("/api/v1/recall", {
                "query": query,
                "dataset_name": dataset,
                "top_k": int(top_k),
            })
        elif mode == "session":
            result = post_json("/api/v1/search", {
                "query": query,
                "top_k": int(top_k),
                "sources": [{"type": "session", "session_id": session_id}],
            })
        else:
            session_result = post_json("/api/v1/search", {
                "query": query,
                "top_k": int(top_k),
                "sources": [{"type": "session", "session_id": session_id}],
            })
            if session_result and session_result != []:
                result = session_result
            else:
                result = post_json("/api/v1/recall", {
                    "query": query,
                    "dataset_name": dataset,
                    "top_k": int(top_k),
                })

    try:
        print(json.dumps(result, ensure_ascii=False))
    except BrokenPipeError:
        raise
except TimeoutError:
    if depth_mode == "deep":
        print(
            "ElephantBroker search timed out in deep mode.\n"
            "The backend did not finish the broader recall request in time.\n"
            "Try again later, or use the default mode for a lighter search.",
            file=sys.stderr,
        )
    else:
        print(
            "ElephantBroker search timed out in fast mode.\n"
            "The backend is responding too slowly for a quick recall request.\n"
            "Try again later, or rerun with --deep if you want to attempt a broader search.",
            file=sys.stderr,
        )
    raise SystemExit(1)
except urllib.error.HTTPError as exc:
    print(
        f"ElephantBroker search failed with HTTP {exc.code}.\n"
        "The backend returned an error while handling the search request.",
        file=sys.stderr,
    )
    raise SystemExit(1)
except urllib.error.URLError as exc:
    print(
        f"ElephantBroker search could not reach the backend: {exc.reason}",
        file=sys.stderr,
    )
    raise SystemExit(1)
except Exception as exc:
    print(
        f"ElephantBroker search failed unexpectedly: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
