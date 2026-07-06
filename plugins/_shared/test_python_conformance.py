from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from .identity_contract import (
    assert_headers_include_identity,
    assert_headers_omit_empty,
    assert_request_skips_without_gateway,
    assert_service_url_priority,
    assert_stable_uuid_contract,
)

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contract_document_exists() -> None:
    contract = (ROOT / "CONTRACT.md").read_text(encoding="utf-8")
    assert "X-EB-Gateway-ID" in contract
    assert "Fail-Closed Writes" in contract
    assert "Service URL Resolution" in contract


def test_claude_code_identity_contract() -> None:
    module = load_module("contract_claude_plugin_eb", ROOT / "claude-code/scripts/_plugin_eb.py")
    with patch.dict(
        os.environ,
        {
            "EB_GATEWAY_ID": "gw-test",
            "EB_AGENT_KEY": "agent-test",
            "EB_ACTOR_ID": "actor-test",
            "EB_AUTH_TOKEN": " token-test ",
        },
        clear=True,
    ):
        headers = module._default_headers()
    assert_headers_include_identity(
        headers,
        gateway_id="gw-test",
        agent_key="agent-test",
        actor_id="actor-test",
        auth_token="token-test",
    )
    assert_headers_omit_empty(headers)
    assert_service_url_priority(module._service_url)
    assert_stable_uuid_contract(module._stable_uuid)
    assert_request_skips_without_gateway(module)


def test_antigravity_identity_contract() -> None:
    module = load_module("contract_antigravity_plugin_eb", ROOT / "antigravity-cli/scripts/_plugin_eb.py")
    with patch.dict(
        os.environ,
        {
            "EB_GATEWAY_ID": "gw-test",
            "EB_AGENT_KEY": "agent-test",
            "EB_ACTOR_ID": "actor-test",
            "EB_AUTH_TOKEN": " token-test ",
        },
        clear=True,
    ):
        headers = module._default_headers()
    assert_headers_include_identity(
        headers,
        gateway_id="gw-test",
        agent_key="agent-test",
        actor_id="actor-test",
        auth_token="token-test",
    )
    assert_headers_omit_empty(headers)
    assert_service_url_priority(module._service_url)
    assert_stable_uuid_contract(module._stable_uuid)
    assert_request_skips_without_gateway(module)


def test_hermes_identity_contract() -> None:
    module = load_module("contract_hermes_client", ROOT / "hermes-agent/client.py")
    client = module.ElephantBrokerClient("http://runtime.test/", "gw-test", "agent-test")
    with patch.dict(os.environ, {"EB_ACTOR_ID": "actor-test", "EB_AUTH_TOKEN": " token-test "}, clear=True):
        headers = client.default_headers()
    assert client.service_url == "http://runtime.test"
    assert_headers_include_identity(
        headers,
        gateway_id="gw-test",
        agent_key="agent-test",
        actor_id="actor-test",
        auth_token="token-test",
    )
    assert_headers_omit_empty(headers)
