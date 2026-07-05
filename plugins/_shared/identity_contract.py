from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import patch


def assert_headers_include_identity(
    headers: dict[str, str],
    *,
    gateway_id: str,
    agent_key: str = "",
    actor_id: str = "",
    auth_token: str = "",
) -> None:
    assert headers["X-EB-Gateway-ID"] == gateway_id
    if agent_key:
        assert headers["X-EB-Agent-Key"] == agent_key
    if actor_id:
        assert headers["X-EB-Actor-Id"] == actor_id
    if auth_token:
        assert headers["X-EB-Auth-Token"] == auth_token


def assert_headers_omit_empty(headers: dict[str, str]) -> None:
    empty_values = [key for key, value in headers.items() if value == ""]
    assert empty_values == []


def assert_service_url_priority(service_url: Callable[[], str]) -> None:
    env = {
        "EB_SERVICE_URL": "http://service.test/",
        "EB_RUNTIME_URL": "http://runtime.test/",
        "COGNEE_SERVICE_URL": "http://cognee.test/",
    }
    with patch.dict(os.environ, env, clear=True):
        assert service_url() == "http://service.test"
    with patch.dict(os.environ, {"EB_RUNTIME_URL": "http://runtime.test/", "COGNEE_SERVICE_URL": "http://cognee.test/"}, clear=True):
        assert service_url() == "http://runtime.test"
    with patch.dict(os.environ, {"COGNEE_SERVICE_URL": "http://cognee.test/"}, clear=True):
        assert service_url() == "http://cognee.test"


def assert_stable_uuid_contract(stable_uuid: Callable[[str], str]) -> None:
    valid = "550e8400-e29b-41d4-a716-446655440000"
    derived = stable_uuid("session:key")
    assert stable_uuid("") == "00000000-0000-0000-0000-000000000000"
    assert stable_uuid(valid) == valid
    assert stable_uuid("session:key") == derived
    uuid.UUID(derived)


def assert_request_skips_without_gateway(module: Any) -> None:
    calls: list[str] = []

    class FakeRequest:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            calls.append("request")

    def fake_urlopen(*_args: Any, **_kwargs: Any) -> None:
        calls.append("urlopen")
        raise AssertionError("request must be skipped without EB_GATEWAY_ID")

    with patch.dict(os.environ, {"EB_SERVICE_URL": "http://runtime.test"}, clear=True):
        with patch.object(module.urllib.request, "Request", FakeRequest):
            with patch.object(module.urllib.request, "urlopen", fake_urlopen):
                result = module._eb_request("/memory/store", {"fact": {"text": "x"}})

    assert result is None
    assert calls == []
