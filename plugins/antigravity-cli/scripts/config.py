"""Shared configuration for the ElephantBroker Antigravity CLI plugin.

Loads settings from (in priority order):
  1. Environment variables (runtime overrides)
  2. Config file (~/.elephantbroker/config.json)
  3. Defaults

Config file is created on first SessionStart if it doesn't exist.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

_CONFIG_DIR = Path.home() / ".elephantbroker"
_STATE_DIR = _CONFIG_DIR
_CONFIG_FILE = _CONFIG_DIR / "config.json"
_HOOK_LOG = _STATE_DIR / "hook.log"

# Legacy path for migration
_LEGACY_CONFIG_DIR = Path.home() / ".cognee-plugin"
_LEGACY_CONFIG_FILE = _LEGACY_CONFIG_DIR / "config.json"

_DEFAULTS = {
    "dataset": "eb_sessions",
    "agent_name": "antigravity-agent",
    "session_strategy": "per-directory",  # per-directory | git-branch | static
    "session_prefix": "eb",
    "top_k": 3,
    # EB service
    "service_url": "",
}


def _config_log(event: str, detail: dict | None = None) -> None:
    try:
        from datetime import datetime, timezone

        _HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "event": event,
        }
        if detail:
            line["detail"] = detail
        with _HOOK_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
    except Exception:
        pass


# Env var overrides (env var name -> config key)
_ENV_MAP = {
    # EB-native env vars (preferred)
    "EB_DATASET": "dataset",
    "EB_AGENT_NAME": "agent_name",
    "EB_SESSION_STRATEGY": "session_strategy",
    "EB_SESSION_PREFIX": "session_prefix",
    "EB_SERVICE_URL": "service_url",
    # Legacy compat (lower priority, checked after EB_* vars)
    "COGNEE_CLAUDE_DATASET": "dataset",
    "COGNEE_CODEX_DATASET": "dataset",
    "COGNEE_AGENT_NAME": "agent_name",
    "COGNEE_PLUGIN_DATASET": "dataset",
    "COGNEE_SESSION_STRATEGY": "session_strategy",
    "COGNEE_SESSION_PREFIX": "session_prefix",
    "COGNEE_SERVICE_URL": "service_url",
    # Legacy compat
    "COGNEE_SESSION_ID": "_static_session_id",
}


def _migrate_legacy_config() -> dict | None:
    """Migrate config from legacy ~/.cognee-plugin/ if new dir doesn't exist."""
    if _CONFIG_FILE.exists():
        return None
    if not _LEGACY_CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(_LEGACY_CONFIG_FILE.read_text(encoding="utf-8"))
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _config_log("migrated_legacy_config", {"from": str(_LEGACY_CONFIG_FILE)})
        return data
    except Exception as exc:
        _config_log("legacy_migration_failed", {"error": str(exc)[:200]})
        return None


def load_config() -> dict:
    """Load merged config: defaults -> file -> env vars."""
    config = dict(_DEFAULTS)

    # Try migration from legacy path
    _migrate_legacy_config()

    # Layer 2: config file
    if _CONFIG_FILE.exists():
        try:
            file_cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            config.update({k: v for k, v in file_cfg.items() if v is not None and v != ""})
        except Exception as exc:
            _config_log(
                "config_file_load_failed", {"path": str(_CONFIG_FILE), "error": str(exc)[:200]}
            )

    # Layer 3: env vars (highest priority)
    for env_key, config_key in _ENV_MAP.items():
        val = os.environ.get(env_key, "")
        if val:
            config[config_key] = val

    return config


def save_config(config: dict) -> None:
    """Write config to disk. Creates directory if needed."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    transient_keys = {"service_url"}
    to_save = {
        k: v
        for k, v in config.items()
        if k not in transient_keys and not k.startswith("_") and v and v != _DEFAULTS.get(k)
    }
    _CONFIG_FILE.write_text(json.dumps(to_save, indent=2), encoding="utf-8")


def get_session_id(config: dict, cwd: Optional[str] = None) -> str:
    """Compute session ID based on the configured strategy.

    Strategies:
      - per-directory: prefix + hash of cwd -> stable per-project
      - git-branch: prefix + hash of cwd + branch -> stable per-branch
      - static: uses static session ID or fallback
    """
    # Legacy: explicit static session ID
    static_id = config.get("_static_session_id", "")
    if static_id:
        return static_id

    strategy = config.get("session_strategy", "per-directory")
    prefix = config.get("session_prefix", "eb")

    if cwd is None:
        cwd = os.environ.get("ANTIGRAVITY_CWD", os.environ.get("CLAUDE_CWD", os.getcwd()))

    if strategy == "static":
        return f"{prefix}_session"

    # Per-directory: hash the cwd for a stable, short ID
    dir_hash = hashlib.sha256(cwd.encode()).hexdigest()[:12]
    dir_name = Path(cwd).name

    if strategy == "git-branch":
        branch = _get_git_branch(cwd)
        if branch:
            return f"{prefix}_{dir_name}_{branch}_{dir_hash}"

    return f"{prefix}_{dir_name}_{dir_hash}"


def get_dataset(config: dict) -> str:
    """Get the dataset name from config."""
    return config.get("dataset", "eb_sessions")


def _get_git_branch(cwd: str) -> str:
    """Get current git branch, or empty string if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            # Sanitize for use in session IDs
            return branch.replace("/", "-").replace(" ", "-")[:40]
    except Exception as exc:
        _config_log("git_branch_lookup_failed", {"cwd": cwd, "error": str(exc)[:200]})
    return ""
