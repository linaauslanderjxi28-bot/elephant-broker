"""Tests for CLI commands — server (elephantbroker) and admin (ebrun)."""
from click.testing import CliRunner


class TestServerCLI:
    """Tests for elephantbroker server CLI (server.py)."""

    def test_serve_command_exists(self):
        from elephantbroker.server import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["serve", "--help"])
        assert result.exit_code == 0
        assert "Start the ElephantBroker" in result.output

    def test_health_check_command_exists(self):
        from elephantbroker.server import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["health-check", "--help"])
        assert result.exit_code == 0

    def test_migrate_command_exists(self):
        from elephantbroker.server import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["migrate"])
        assert result.exit_code == 0
        assert "No migrations needed" in result.output

    def test_serve_default_port(self):
        from elephantbroker.server import serve
        for param in serve.params:
            if param.name == "port":
                assert param.default == 8420

    def test_health_check_default_port(self):
        from elephantbroker.server import health_check
        for param in health_check.params:
            if param.name == "port":
                assert param.default == 8420

    def test_serve_has_config_option(self):
        from elephantbroker.server import serve
        param_names = [p.name for p in serve.params]
        assert "config" in param_names


class TestEbrunCLI:
    """Tests for ebrun admin CLI (cli.py)."""

    def test_bootstrap_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["bootstrap", "--help"])
        assert result.exit_code == 0
        assert "org-name" in result.output
        assert "team-name" in result.output
        assert "admin-name" in result.output

    def test_org_create_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["org", "create", "--help"])
        assert result.exit_code == 0
        assert "--name" in result.output

    def test_org_list_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["org", "list", "--help"])
        assert result.exit_code == 0

    def test_team_create_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["team", "create", "--help"])
        assert result.exit_code == 0
        assert "--org-id" in result.output

    def test_team_add_member_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["team", "add-member", "--help"])
        assert result.exit_code == 0

    def test_actor_create_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["actor", "create", "--help"])
        assert result.exit_code == 0
        assert "--display-name" in result.output
        assert "--authority-level" in result.output

    def test_actor_merge_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["actor", "merge", "--help"])
        assert result.exit_code == 0

    def test_profile_list_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "list", "--help"])
        assert result.exit_code == 0

    def test_profile_resolve_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "resolve", "--help"])
        assert result.exit_code == 0

    def test_authority_list_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["authority", "list", "--help"])
        assert result.exit_code == 0

    def test_authority_set_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["authority", "set", "--help"])
        assert result.exit_code == 0
        assert "--min-level" in result.output

    def test_goal_create_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["goal", "create", "--help"])
        assert result.exit_code == 0
        assert "--scope" in result.output

    def test_goal_list_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["goal", "list", "--help"])
        assert result.exit_code == 0

    def test_config_set_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "set", "--help"])
        assert result.exit_code == 0

    def test_config_show_command_exists(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0

    def test_actor_id_flag_accepted(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["--actor-id", "test-uuid", "authority", "list", "--help"])
        assert result.exit_code == 0


class TestIndexesCLI:
    """Tests for ebrun indexes commands (Fix 5 — opt-in fact indexes)."""

    SAMPLE_LISTING = {"indexes": [
        {"name": "eb_fact_gateway_id", "property": "gateway_id",
         "description": "Tenant scoping", "exists": True,
         "state": "ONLINE", "population_percent": 100.0},
        {"name": "eb_fact_created_at", "property": "created_at",
         "description": "Recency ordering", "exists": True,
         "state": "POPULATING", "population_percent": 42.0},
        {"name": "eb_fact_confidence", "property": "confidence",
         "description": "Confidence filter", "exists": False,
         "state": None, "population_percent": None},
    ]}

    def _patch_api(self, monkeypatch, response=None):
        """Replace cli._api with a recorder; returns the call log."""
        # Pin the runtime URL so assertions don't depend on the developer's
        # ~/.elephantbroker/config.json or ambient EB_RUNTIME_URL.
        monkeypatch.setenv("EB_RUNTIME_URL", "http://localhost:8420")
        calls = []

        def fake_api(method, url, actor_id, body=None, api_key=None):
            calls.append((method, url))
            return response if response is not None else {}

        monkeypatch.setattr("elephantbroker.cli._api", fake_api)
        return calls

    def test_indexes_group_help_mentions_global_scope(self):
        from elephantbroker.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "--help"])
        assert result.exit_code == 0
        # Multi-tenant caveat must be surfaced in the CLI help.
        assert "database-global" in result.output

    def test_indexes_list_renders_table(self, monkeypatch):
        from elephantbroker.cli import cli
        calls = self._patch_api(monkeypatch, self.SAMPLE_LISTING)
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "list"])
        assert result.exit_code == 0
        assert calls == [("GET", "http://localhost:8420/admin/indexes")]
        assert "eb_fact_gateway_id" in result.output
        assert "ONLINE" in result.output
        assert "POPULATING 42%" in result.output
        assert "absent" in result.output  # exists=False renders as absent

    def test_indexes_status_known_name(self, monkeypatch):
        from elephantbroker.cli import cli
        self._patch_api(monkeypatch, self.SAMPLE_LISTING)
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "status", "eb_fact_created_at"])
        assert result.exit_code == 0
        assert "eb_fact_created_at" in result.output
        assert "POPULATING" in result.output

    def test_indexes_status_unknown_name_errors(self, monkeypatch):
        from elephantbroker.cli import cli
        self._patch_api(monkeypatch, self.SAMPLE_LISTING)
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "status", "eb_fact_bogus"])
        assert result.exit_code == 1
        assert "Unknown fact index: eb_fact_bogus" in result.output

    def test_indexes_enable_single(self, monkeypatch):
        from elephantbroker.cli import cli
        calls = self._patch_api(
            monkeypatch, {"index": "eb_fact_scope", "status": "created"})
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "enable", "eb_fact_scope"])
        assert result.exit_code == 0
        assert calls == [("POST", "http://localhost:8420/admin/indexes/eb_fact_scope")]
        assert "created" in result.output

    def test_indexes_enable_all_reads_catalog_then_posts_each(self, monkeypatch):
        from elephantbroker.cli import cli
        monkeypatch.setenv("EB_RUNTIME_URL", "http://localhost:8420")
        calls = []

        def fake_api(method, url, actor_id, body=None, api_key=None):
            calls.append((method, url))
            if method == "GET":
                return self.SAMPLE_LISTING
            return {"index": url.rsplit("/", 1)[-1], "status": "created"}

        monkeypatch.setattr("elephantbroker.cli._api", fake_api)
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "enable", "--all"])
        assert result.exit_code == 0
        # Catalog is re-read from the runtime, never hardcoded in the CLI.
        assert calls[0] == ("GET", "http://localhost:8420/admin/indexes")
        assert calls[1:] == [
            ("POST", "http://localhost:8420/admin/indexes/eb_fact_gateway_id"),
            ("POST", "http://localhost:8420/admin/indexes/eb_fact_created_at"),
            ("POST", "http://localhost:8420/admin/indexes/eb_fact_confidence"),
        ]
        assert "eb_fact_gateway_id: created" in result.output

    def test_indexes_enable_requires_exactly_one_target(self, monkeypatch):
        from elephantbroker.cli import cli
        calls = self._patch_api(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "enable"])
        assert result.exit_code == 1
        assert "exactly one" in result.output
        result = runner.invoke(cli, ["indexes", "enable", "eb_fact_scope", "--all"])
        assert result.exit_code == 1
        assert "exactly one" in result.output
        assert calls == []  # no API call may fire on invalid invocation

    def test_indexes_disable_single(self, monkeypatch):
        from elephantbroker.cli import cli
        calls = self._patch_api(
            monkeypatch, {"index": "eb_fact_scope", "status": "dropped"})
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "disable", "eb_fact_scope"])
        assert result.exit_code == 0
        assert calls == [("DELETE", "http://localhost:8420/admin/indexes/eb_fact_scope")]
        assert "dropped" in result.output

    def test_indexes_disable_all(self, monkeypatch):
        from elephantbroker.cli import cli
        monkeypatch.setenv("EB_RUNTIME_URL", "http://localhost:8420")
        calls = []

        def fake_api(method, url, actor_id, body=None, api_key=None):
            calls.append((method, url))
            if method == "GET":
                return self.SAMPLE_LISTING
            return {"index": url.rsplit("/", 1)[-1], "status": "dropped"}

        monkeypatch.setattr("elephantbroker.cli._api", fake_api)
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "disable", "--all"])
        assert result.exit_code == 0
        assert calls[0] == ("GET", "http://localhost:8420/admin/indexes")
        assert [c for c in calls[1:] if c[0] == "DELETE"] == calls[1:]
        assert len(calls[1:]) == 3

    def test_indexes_rebuild(self, monkeypatch):
        from elephantbroker.cli import cli
        calls = self._patch_api(
            monkeypatch, {"index": "eb_fact_memory_class", "status": "rebuilt"})
        runner = CliRunner()
        result = runner.invoke(cli, ["indexes", "rebuild", "eb_fact_memory_class"])
        assert result.exit_code == 0
        assert calls == [
            ("POST", "http://localhost:8420/admin/indexes/eb_fact_memory_class/rebuild")]
        assert "rebuilt" in result.output


class TestConfigLoading:
    """Tests for YAML config loading."""

    def test_from_yaml_loads_file(self, tmp_path):
        from elephantbroker.schemas.config import ElephantBrokerConfig
        yaml_content = "gateway:\n  gateway_id: test-gw\ninfra:\n  log_level: debug\n"
        config_file = tmp_path / "test.yaml"
        config_file.write_text(yaml_content)
        config = ElephantBrokerConfig.from_yaml(str(config_file))
        assert config.gateway.gateway_id == "test-gw"
        assert config.infra.log_level == "debug"

    def test_from_yaml_env_overrides(self, tmp_path, monkeypatch):
        from elephantbroker.schemas.config import ElephantBrokerConfig
        yaml_content = "gateway:\n  gateway_id: yaml-gw\n"
        config_file = tmp_path / "test.yaml"
        config_file.write_text(yaml_content)
        monkeypatch.setenv("EB_GATEWAY_ID", "env-gw")
        config = ElephantBrokerConfig.from_yaml(str(config_file))
        assert config.gateway.gateway_id == "env-gw"

    def test_from_yaml_invalid_raises(self, tmp_path):
        from elephantbroker.schemas.config import ElephantBrokerConfig
        config_file = tmp_path / "bad.yaml"
        config_file.write_text("invalid: [yaml: {broken")
        import pytest
        with pytest.raises(Exception):
            ElephantBrokerConfig.from_yaml(str(config_file))

    def test_from_yaml_missing_file_raises(self):
        from elephantbroker.schemas.config import ElephantBrokerConfig
        import pytest
        with pytest.raises(FileNotFoundError):
            ElephantBrokerConfig.from_yaml("/nonexistent/path.yaml")

    def test_load_no_path_uses_packaged_default(self):
        from elephantbroker.schemas.config import ElephantBrokerConfig
        # F2/F3 — D5 OPERATOR LOCKED: load() with no path falls back to the
        # packaged default.yaml resource and applies env overrides on top.
        config = ElephantBrokerConfig.load()
        assert config.gateway.gateway_id is not None

    def test_default_yaml_loads(self):
        import os
        from elephantbroker.schemas.config import ElephantBrokerConfig
        yaml_path = os.path.join(os.path.dirname(__file__), "..", "..", "elephantbroker", "config", "default.yaml")
        if os.path.exists(yaml_path):
            config = ElephantBrokerConfig.from_yaml(yaml_path)
            # default.yaml ships with an empty gateway_id sentinel — operators
            # MUST set EB_GATEWAY_ID before booting (Bucket A — A3 startup guard).
            assert config.gateway.gateway_id == ""
