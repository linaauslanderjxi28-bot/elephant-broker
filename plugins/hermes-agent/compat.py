from __future__ import annotations

import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _load_attr(module_name: str, attr_name: str) -> Any:
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


try:
    MemoryProvider = _load_attr("agent.memory_provider", "MemoryProvider")
except (ImportError, AttributeError):
    class MemoryProvider:
        pass


try:
    get_hermes_home = _load_attr("hermes_constants", "get_hermes_home")
except (ImportError, AttributeError):
    def get_hermes_home() -> Path:
        return Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()


try:
    atomic_json_write = _load_attr("utils", "atomic_json_write")
except (ImportError, AttributeError):
    def atomic_json_write(path, data, mode=0o600):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=str(target.parent), delete=False, encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            temp_name = handle.name
        os.chmod(temp_name, mode)
        os.replace(temp_name, str(target))
