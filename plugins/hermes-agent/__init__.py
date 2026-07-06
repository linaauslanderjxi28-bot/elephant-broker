from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


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

_config = load_local_module("config")
_provider = load_local_module("provider")
_schemas = load_local_module("schemas")
_utils = load_local_module("utils")

_load_config = _config.load_config
ElephantBrokerMemoryProvider = _provider.ElephantBrokerMemoryProvider
SEARCH_GLOBAL_SCHEMA = _schemas.SEARCH_GLOBAL_SCHEMA
SEARCH_SCHEMA = _schemas.SEARCH_SCHEMA
STORE_SCHEMA = _schemas.STORE_SCHEMA
_stable_uuid = _utils.stable_uuid


def register(ctx) -> None:
    ctx.register_memory_provider(ElephantBrokerMemoryProvider())
