from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent


def load_local_module(name: str) -> ModuleType:
    module_name = f"elephantbroker_hermes_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    module_path = PLUGIN_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load local module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_compat_module = load_local_module("compat")
atomic_json_write = _compat_module.atomic_json_write
get_hermes_home = _compat_module.get_hermes_home

logger = logging.getLogger(__name__)


DEFAULT_CONFIG: dict[str, str] = {
    "service_url": "http://localhost:8420",
    "gateway_id": "",
    "agent_key": "",
    "profile_name": "coding",
}


def load_config() -> dict[str, str]:
    config = dict(DEFAULT_CONFIG)
    config_path = get_hermes_home() / "elephantbroker.json"

    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({str(k): str(v) for k, v in file_cfg.items() if v is not None and v != ""})
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("ElephantBroker config load failed: %s", e)

    env_config = {
        "service_url": os.environ.get("EB_SERVICE_URL") or os.environ.get("EB_RUNTIME_URL") or os.environ.get("COGNEE_SERVICE_URL"),
        "gateway_id": os.environ.get("EB_GATEWAY_ID"),
        "agent_key": os.environ.get("EB_AGENT_KEY"),
        "profile_name": os.environ.get("EB_PROFILE_NAME"),
    }
    config.update({k: v for k, v in env_config.items() if v is not None and v != ""})
    return config


def save_config(values: dict[str, Any], hermes_home: str) -> None:
    config_path = Path(hermes_home) / "elephantbroker.json"
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("ElephantBroker config read failed before save: %s", e)
    existing.update(values)
    atomic_json_write(config_path, existing, mode=0o600)


def config_schema() -> list[dict[str, Any]]:
    return [
        {"key": "service_url", "description": "ElephantBroker service URL", "default": "http://localhost:8420", "env_var": "EB_SERVICE_URL or EB_RUNTIME_URL"},
        {"key": "gateway_id", "description": "ElephantBroker Gateway ID", "default": "", "env_var": "EB_GATEWAY_ID"},
        {"key": "agent_key", "description": "ElephantBroker Agent Key", "secret": True, "env_var": "EB_AGENT_KEY"},
        {"key": "profile_name", "description": "Context engine profile name", "default": "coding"},
    ]
