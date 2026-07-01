"""Tests for ebrun CLI auth — API-key config + header injection + auth commands.

Covers the Phase 11 SOW acceptance rows for Workstream 11.4:

    - ``ebrun config set/get/unset api-key`` (stored in ~/.ebrun/config.toml)
    - ``config get api-key`` shows a masked value, never plaintext
    - HTTP requests inject ``X-EB-API-Key`` when a key is configured
    - fallback to ``X-EB-Actor-Id`` when no key is configured
    - ``--api-key`` flag overrides the stored key
    - ``ebrun auth create-key`` / ``list-keys`` / ``revoke-key``
    - ``--bootstrap`` forces an unauthenticated create-key request

All HTTP is mocked at the ``httpx`` boundary (the runtime is never contacted);
config files are isolated to a per-test ``$HOME`` via ``tmp_path``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner


class _RequestRecorder:
    """Records the last httpx request made by ``_api()``.

    Installed over ``httpx.get/post/put/delete`` so tests can assert which
    auth header ebrun injected without a live runtime.
    """

    def __init__(self, response_json: dict | None = None, status_code: int = 200):
        self.calls: list[dict] = []
        self.response_json = response_json if response_json is not None else {"ok": True}
        self.status_code = status_code

    def _make(self, method: str):
        def _fn(url, headers=None, json=None, timeout=None):
            self.calls.append(
                {"method": method, "url": url, "headers": headers or {}, "json": json}
            )
            return SimpleNamespace(
                status_code=self.status_code,
                text="",
                json=lambda: self.response_json,
            )

        return _fn

    @property
    def last(self) -> dict:
        assert self.calls, "no HTTP request was recorded"
        return self.calls[-1]

    @property
    def last_headers(self) -> dict:
        return self.last["headers"]


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Redirect ~/.ebrun and ~/.elephantbroker into a temp HOME; clear env keys."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EB_API_KEY", raising=False)
    monkeypatch.delenv("EB_ACTOR_ID", raising=False)
    monkeypatch.delenv("EB_RUNTIME_URL", raising=False)
    # Reset the module global so state doesn't leak between invocations.
    import elephantbroker.cli as cli_module

    monkeypatch.setattr(cli_module, "_API_KEY", None, raising=False)
    yield


@pytest.fixture
def recorder(monkeypatch):
    """Install an httpx recorder and return it."""
    import httpx

    rec = _RequestRecorder()
    monkeypatch.setattr(httpx, "get", rec._make("GET"))
    monkeypatch.setattr(httpx, "post", rec._make("POST"))
    monkeypatch.setattr(httpx, "put", rec._make("PUT"))
    monkeypatch.setattr(httpx, "delete", rec._make("DELETE"))
    return rec


@pytest.fixture
def runner():
    return CliRunner()


def _cli():
    from elephantbroker.cli import cli

    return cli


# ---------------------------------------------------------------------------
# config set / get / unset api-key
# ---------------------------------------------------------------------------


class TestConfigApiKey:
    def test_config_set_api_key_stores_in_toml(self, runner, tmp_path):
        from elephantbroker import cli_auth

        result = runner.invoke(_cli(), ["config", "set", "api-key", "eb_key_abcd1234wxyz"])
        assert result.exit_code == 0
        # Stored in ~/.ebrun/config.toml (isolated HOME).
        assert cli_auth.ebrun_config_path() == str(tmp_path / ".ebrun" / "config.toml")
        assert cli_auth.get_stored_api_key() == "eb_key_abcd1234wxyz"
        # Output is masked, never the raw plaintext.
        assert "eb_key_abcd1234wxyz" not in result.output
        assert "****wxyz" in result.output

    def test_config_get_api_key_masks(self, runner):
        from elephantbroker import cli_auth

        cli_auth.set_api_key("eb_key_secretvalue9999")
        result = runner.invoke(_cli(), ["config", "get", "api-key"])
        assert result.exit_code == 0
        assert "eb_key_secretvalue9999" not in result.output
        assert "eb_key_****9999" in result.output

    def test_config_get_api_key_unset(self, runner):
        result = runner.invoke(_cli(), ["config", "get", "api-key"])
        assert result.exit_code == 0
        assert "not set" in result.output

    def test_config_unset_api_key_removes(self, runner):
        from elephantbroker import cli_auth

        cli_auth.set_api_key("eb_key_tobedeleted0001")
        result = runner.invoke(_cli(), ["config", "unset", "api-key"])
        assert result.exit_code == 0
        assert "Removed api-key" in result.output
        assert cli_auth.get_stored_api_key() is None

    def test_config_unset_api_key_when_absent(self, runner):
        result = runner.invoke(_cli(), ["config", "unset", "api-key"])
        assert result.exit_code == 0
        assert "was not set" in result.output

    def test_config_show_masks_api_key(self, runner):
        from elephantbroker import cli_auth

        cli_auth.set_api_key("eb_key_showmasked1234")
        result = runner.invoke(_cli(), ["config", "show"])
        assert result.exit_code == 0
        assert "eb_key_showmasked1234" not in result.output
        assert "****1234" in result.output


# ---------------------------------------------------------------------------
# Header injection & fallback
# ---------------------------------------------------------------------------


class TestHeaderInjection:
    def test_stored_api_key_injected_as_header(self, runner, recorder):
        from elephantbroker import cli_auth

        cli_auth.set_api_key("eb_key_headerkey0001")
        result = runner.invoke(_cli(), ["org", "list"])
        assert result.exit_code == 0
        assert recorder.last_headers.get("X-EB-API-Key") == "eb_key_headerkey0001"
        # When a key is present, the actor-id header must NOT be sent.
        assert "X-EB-Actor-Id" not in recorder.last_headers

    def test_actor_id_fallback_when_no_key(self, runner, recorder):
        result = runner.invoke(
            _cli(), ["--actor-id", "actor-uuid-123", "org", "list"]
        )
        assert result.exit_code == 0
        assert recorder.last_headers.get("X-EB-Actor-Id") == "actor-uuid-123"
        assert "X-EB-API-Key" not in recorder.last_headers

    def test_api_key_flag_overrides_stored(self, runner, recorder):
        from elephantbroker import cli_auth

        cli_auth.set_api_key("eb_key_stored0000")
        result = runner.invoke(
            _cli(), ["--api-key", "eb_key_flagoverride9", "org", "list"]
        )
        assert result.exit_code == 0
        assert recorder.last_headers.get("X-EB-API-Key") == "eb_key_flagoverride9"

    def test_api_key_env_overrides_stored(self, runner, recorder, monkeypatch):
        from elephantbroker import cli_auth

        cli_auth.set_api_key("eb_key_stored0000")
        monkeypatch.setenv("EB_API_KEY", "eb_key_fromenv1234")
        result = runner.invoke(_cli(), ["org", "list"])
        assert result.exit_code == 0
        assert recorder.last_headers.get("X-EB-API-Key") == "eb_key_fromenv1234"


# ---------------------------------------------------------------------------
# auth create-key / list-keys / revoke-key
# ---------------------------------------------------------------------------


class TestAuthCommands:
    def test_create_key_command_exists(self, runner):
        result = runner.invoke(_cli(), ["auth", "create-key", "--help"])
        assert result.exit_code == 0
        assert "--label" in result.output
        assert "--bootstrap" in result.output

    def test_create_key_posts_to_api(self, runner, recorder):
        recorder.response_json = {"key": "eb_key_newplaintext999", "key_id": "kid-1"}
        result = runner.invoke(
            _cli(),
            ["--actor-id", "admin-1", "auth", "create-key", "--label", "workstation"],
        )
        assert result.exit_code == 0
        call = recorder.last
        assert call["method"] == "POST"
        assert call["url"].endswith("/auth/api-keys")
        assert call["json"]["label"] == "workstation"
        # authenticated via configured actor-id
        assert call["headers"].get("X-EB-Actor-Id") == "admin-1"
        # plaintext echoed once so the operator can store it
        assert "eb_key_newplaintext999" in result.output

    def test_create_key_bootstrap_skips_auth(self, runner, recorder):
        from elephantbroker import cli_auth

        # Even with a stored key, --bootstrap forces an unauthenticated request.
        cli_auth.set_api_key("eb_key_stored0000")
        recorder.response_json = {"key": "eb_key_bootstrapkey1", "key_id": "kid-b"}
        result = runner.invoke(
            _cli(),
            ["auth", "create-key", "--label", "first-key", "--bootstrap"],
        )
        assert result.exit_code == 0
        headers = recorder.last_headers
        assert "X-EB-API-Key" not in headers
        assert "X-EB-Actor-Id" not in headers

    def test_create_key_with_authority_and_actor_binding(self, runner, recorder):
        recorder.response_json = {"key": "eb_key_bound", "key_id": "kid-2"}
        result = runner.invoke(
            _cli(),
            [
                "--actor-id", "admin-1",
                "auth", "create-key",
                "--label", "svc",
                "--authority-level", "70",
                "--actor-id", "bound-actor-9",
            ],
        )
        assert result.exit_code == 0
        body = recorder.last["json"]
        assert body["authority_level"] == 70
        assert body["actor_id"] == "bound-actor-9"

    def test_list_keys(self, runner, recorder):
        from elephantbroker import cli_auth

        cli_auth.set_api_key("eb_key_listcaller01")
        recorder.response_json = {"keys": [{"key_id": "kid-1", "label": "a"}]}
        result = runner.invoke(_cli(), ["auth", "list-keys"])
        assert result.exit_code == 0
        call = recorder.last
        assert call["method"] == "GET"
        assert call["url"].endswith("/auth/api-keys")
        assert call["headers"].get("X-EB-API-Key") == "eb_key_listcaller01"
        assert "kid-1" in result.output

    def test_revoke_key(self, runner, recorder):
        recorder.response_json = {"key_id": "kid-9", "status": "revoked"}
        result = runner.invoke(
            _cli(), ["--actor-id", "admin-1", "auth", "revoke-key", "kid-9"]
        )
        assert result.exit_code == 0
        call = recorder.last
        assert call["method"] == "DELETE"
        assert call["url"].endswith("/auth/api-keys/kid-9")
        assert "revoked" in result.output


# ---------------------------------------------------------------------------
# cli_auth helpers (masking edge cases)
# ---------------------------------------------------------------------------


class TestMasking:
    def test_mask_preserves_prefix_and_tail(self):
        from elephantbroker import cli_auth

        assert cli_auth.mask_api_key("eb_key_abcdefgh1234") == "eb_key_****1234"

    def test_mask_empty(self):
        from elephantbroker import cli_auth

        assert cli_auth.mask_api_key(None) == ""
        assert cli_auth.mask_api_key("") == ""

    def test_mask_non_prefixed(self):
        from elephantbroker import cli_auth

        assert cli_auth.mask_api_key("rawtoken9999") == "****9999"
