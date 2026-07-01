"""ebrun CLI auth helpers — API-key storage and header resolution (Phase 11).

The API key is stored in ``~/.ebrun/config.toml`` (separate from the Phase 8
``~/.elephantbroker/config.json`` that holds actor-id / runtime-url), matching
the Phase 11 dashboard-auth spec.

Reading uses stdlib :mod:`tomllib` (Python 3.11+). Writing uses a minimal
TOML serializer so no third-party TOML *writer* dependency is required (the
runner may not have run ``uv sync``).

Resolution precedence for the active API key:
    ``--api-key`` flag  >  ``EB_API_KEY`` env  >  stored ``~/.ebrun/config.toml``.

When an API key is available it is sent as the ``X-EB-API-Key`` header; when
absent, ebrun falls back to the Phase 8 ``X-EB-Actor-Id`` header (local trust
boundary only).
"""
from __future__ import annotations

import os


def ebrun_config_path() -> str:
    """Absolute path to the ebrun TOML config file."""
    return os.path.expanduser("~/.ebrun/config.toml")


def load_ebrun_config() -> dict:
    """Load ``~/.ebrun/config.toml`` as a dict (empty dict if missing/invalid)."""
    path = ebrun_config_path()
    if not os.path.exists(path):
        return {}
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def save_ebrun_config(data: dict) -> None:
    """Serialize ``data`` to ``~/.ebrun/config.toml`` (flat key/value TOML).

    The config file holds a secret (the API key), so it is created with
    ``0o600`` permissions inside a ``0o700`` directory, and existing files are
    tightened to ``0o600`` before writing.
    """
    path = ebrun_config_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    lines: list[str] = []
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, bool):
            lines.append(f"{key} = {str(value).lower()}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key} = {value}")
        else:
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    # Tighten perms on an existing file before rewriting its contents.
    if os.path.exists(path):
        os.chmod(path, 0o600)
    content = "\n".join(lines) + ("\n" if lines else "")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)


def set_api_key(key: str) -> None:
    """Persist an API key to ``~/.ebrun/config.toml``."""
    cfg = load_ebrun_config()
    cfg["api_key"] = key
    save_ebrun_config(cfg)


def unset_api_key() -> bool:
    """Remove the stored API key. Returns True if a key was removed."""
    cfg = load_ebrun_config()
    if "api_key" in cfg:
        del cfg["api_key"]
        save_ebrun_config(cfg)
        return True
    return False


def get_stored_api_key() -> str | None:
    """Return the API key stored in ``~/.ebrun/config.toml`` (or None)."""
    cfg = load_ebrun_config()
    val = cfg.get("api_key")
    return val or None


def mask_api_key(key: str | None) -> str:
    """Mask an API key for display, e.g. ``eb_key_****a1b2``.

    Preserves an ``eb_key_`` prefix (if present) and the last four characters;
    everything else is replaced with ``****``.
    """
    if not key:
        return ""
    prefix = "eb_key_" if key.startswith("eb_key_") else ""
    tail = key[-4:] if len(key) >= 4 else key
    return f"{prefix}****{tail}"


def resolve_api_key(flag_value: str | None) -> str | None:
    """Resolve the active API key: ``--api-key`` flag > ``EB_API_KEY`` env > stored config."""
    if flag_value:
        return flag_value
    env_val = os.environ.get("EB_API_KEY")
    if env_val:
        return env_val
    return get_stored_api_key()
