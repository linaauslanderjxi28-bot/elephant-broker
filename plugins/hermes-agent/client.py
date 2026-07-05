from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


class ElephantBrokerClient:
    def __init__(self, service_url: str, gateway_id: str, agent_key: str) -> None:
        self.service_url = service_url.rstrip("/")
        self.gateway_id = gateway_id
        self.agent_key = agent_key

    def default_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.gateway_id:
            headers["X-EB-Gateway-ID"] = self.gateway_id
        if self.agent_key:
            headers["X-EB-Agent-Key"] = self.agent_key
        actor_id = os.environ.get("EB_ACTOR_ID", "").strip()
        if actor_id:
            headers["X-EB-Actor-Id"] = actor_id
        auth_token = os.environ.get("EB_AUTH_TOKEN", "").strip()
        if auth_token:
            headers["X-EB-Auth-Token"] = auth_token
        return headers

    def request(self, path: str, payload: dict[str, Any] | None = None, *, method: str = "POST", timeout: float = 30.0) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.service_url}{path}",
            data=data,
            headers=self.default_headers(),
            method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return None
            return json.loads(body)
