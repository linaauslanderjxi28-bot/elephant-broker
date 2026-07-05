#!/usr/bin/env bash
set -euo pipefail

export EB_MODE=true
export COGNEE_SERVICE_URL="${COGNEE_SERVICE_URL:-http://localhost:8420}"
export EB_GATEWAY_ID="${EB_GATEWAY_ID:-gw-enterprise-prod}"

python3 - <<'PY'
import sys
sys.path.insert(0, 'scripts')
from _plugin_eb import eb_health, eb_memory_status, eb_session_start, eb_search, eb_session_end

assert eb_health() is True
status = eb_memory_status()
assert isinstance(status, dict)
assert status.get('backend') == 'elephantbroker'

start = eb_session_start('plan_smoke_session', session_id='plan_smoke_session')
assert isinstance(start, dict)
assert start.get('session_key') == 'plan_smoke_session'

results = eb_search('plan smoke', session_key='plan_smoke_session', max_results=3)
assert isinstance(results, list)

end = eb_session_end('plan_smoke_session', session_id='plan_smoke_session', reason='plan-smoke')
assert isinstance(end, dict)
assert end.get('session_key') == 'plan_smoke_session'
PY

python3 - <<'PY'
import sys
sys.path.insert(0, 'scripts')
from _plugin_common import resolve_runtime_mode, http_api_ready

runtime = resolve_runtime_mode()
assert runtime['mode'] == 'eb'
assert runtime['service_url'].startswith('http://localhost:8420')
assert http_api_ready() is True
PY
