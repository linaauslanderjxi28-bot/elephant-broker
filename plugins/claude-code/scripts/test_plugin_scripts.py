from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).parent
HOOKS_FILE = SCRIPTS_DIR.parent / "hooks" / "hooks.json"


def load_sync_module():
    path = SCRIPTS_DIR / "sync-session-to-graph.py"
    spec = importlib.util.spec_from_file_location("claude_sync_session_to_graph", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sync-session-to-graph.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_script_module(filename: str, module_name: str):
    path = SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def isolated_plugin_modules():
    previous = {name: sys.modules.get(name) for name in ("_plugin_common", "_plugin_eb")}
    try:
        sys.modules.pop("_plugin_common", None)
        sys.modules.pop("_plugin_eb", None)
        yield
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def hook_script_names() -> list[str]:
    hooks = json.loads(HOOKS_FILE.read_text(encoding="utf-8"))["hooks"]
    names: set[str] = set()
    for event_groups in hooks.values():
        for group in event_groups:
            for hook in group.get("hooks", []):
                for arg in hook.get("args", []):
                    if arg.endswith(".py"):
                        names.add(Path(arg).name)
    return sorted(names)


class TestHookScriptImports(unittest.TestCase):
    def test_all_hook_referenced_production_scripts_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["CLAUDE_PLUGIN_DATA"] = temp_dir
            for script_name in hook_script_names():
                with self.subTest(script=script_name):
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            (
                                "import importlib.util; "
                                f"p={str(SCRIPTS_DIR / script_name)!r}; "
                                "s=importlib.util.spec_from_file_location('hook_script', p); "
                                "m=importlib.util.module_from_spec(s); "
                                "s.loader.exec_module(m)"
                            ),
                        ],
                        check=False,
                        capture_output=True,
                        env=env,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)


class TestElephantBrokerStagingLifecycle(unittest.TestCase):
    def test_staged_qa_and_trace_flush_once_and_record_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"CLAUDE_PLUGIN_DATA": temp_dir, "EB_SERVICE_URL": "http://127.0.0.1:8420"},
        ), isolated_plugin_modules():
            common = load_script_module("_plugin_common.py", "_plugin_common")
            sys.modules["_plugin_common"] = common
            eb = load_script_module("_plugin_eb.py", "_plugin_eb")
            sys.modules["_plugin_eb"] = eb

            common.remember_entry_via_http(
                "dataset-1",
                "session-1",
                {"type": "qa", "question": "Question", "answer": "Answer"},
            )
            common.remember_entry_via_http(
                "dataset-1",
                "session-1",
                {
                    "type": "trace",
                    "origin_function": "Read",
                    "status": "success",
                    "method_return_value": "tool output",
                },
            )

            cache = json.loads((Path(temp_dir) / "http_bridge_cache.json").read_text(encoding="utf-8"))
            self.assertEqual(
                cache["eb:dataset-1:session-1"],
                {
                    "qa": [{"answer": "Answer", "question": "Question"}],
                    "trace": ["[Read] success\noutput: tool output"],
                },
            )

            ingest_calls: list[list[dict[str, str]]] = []
            with (
                patch.object(eb.urllib.request, "urlopen"),
                patch.object(eb, "_eb_request", return_value={"embedding_available": True}),
                patch.object(
                    eb,
                    "eb_ingest_turn",
                    side_effect=lambda _key, messages, **_kwargs: ingest_calls.append(messages)
                    or {"facts_extracted": 2},
                ),
            ):
                first = eb.eb_persist_session("dataset-1", "session-1")
                second = eb.eb_persist_session("dataset-1", "session-1")

            self.assertEqual(first.status.value, "flushed")
            self.assertIn(second.status.value, {"empty", "unchanged"})
            self.assertEqual(len(ingest_calls), 1)
            self.assertEqual(
                ingest_calls[0],
                [
                    {"role": "user", "content": "Question"},
                    {"role": "assistant", "content": "Answer"},
                    {"role": "tool", "content": "[Read] success\noutput: tool output"},
                ],
            )
            cache_after = json.loads(
                (Path(temp_dir) / "http_bridge_cache.json").read_text(encoding="utf-8")
            )
            self.assertEqual(cache_after["eb:dataset-1:session-1"], {"qa": [], "trace": []})


class TestIdleWatcher(unittest.TestCase):
    def test_improve_once_uses_eb_flush_without_local_fallback(self) -> None:
        with isolated_plugin_modules():
            common = load_script_module("_plugin_common.py", "_plugin_common")
            module = load_script_module("idle-watcher.py", "claude_idle_watcher")

            terminal = type("Result", (), {"status": type("Status", (), {"value": "empty"})(), "terminal_success": True})()

            with (
                patch.object(common, "persist_session_cache_to_graph_via_http", return_value=terminal) as persist,
                patch.object(common, "http_api_ready", return_value=True),
                patch.object(module, "_log"),
            ):
                result = asyncio.run(
                    module._improve_once(
                        "session-1",
                        "dataset-1",
                        {"session_key": "session-1", "user_id": "user-1"},
                    )
                )

            self.assertTrue(result)
            persist.assert_called_once_with("dataset-1", "session-1")


class TestSyncSessionToGraph(unittest.TestCase):
    def test_sync_module_imports_in_eb_only_runtime(self) -> None:
        module = load_sync_module()

        self.assertTrue(callable(module.main))

    def test_sync_runs_eb_flush_and_unregisters(self) -> None:
        module = load_sync_module()
        calls: list[str] = []

        resolved = ("session-1", "dataset-1", "", "agent-session-1", True, True, "session-1")
        terminal = type("Result", (), {"status": type("Status", (), {"value": "flushed"})(), "terminal_success": True})()
        with (
            patch.object(module, "_load_resolved", return_value=resolved),
            patch.object(module, "hook_log"),
            patch.object(
                module,
                "persist_session_cache_to_graph_via_http",
                side_effect=lambda _dataset, _session: calls.append("flush") or terminal,
            ),
            patch.object(
                module,
                "unregister_agent_via_http",
                side_effect=lambda **_kwargs: calls.append("unregister") or (True, 0),
            ),
        ):
            asyncio.run(module._sync(stop_watcher=False, unregister_on_finish=True))

        self.assertEqual(calls, ["flush", "unregister"])


class _SearchHandler(BaseHTTPRequestHandler):
    headers_seen: dict[str, str] = {}

    def do_POST(self) -> None:
        type(self).headers_seen = dict(self.headers.items())
        content_length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(content_length)
        response = json.dumps([]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *_args: object) -> None:
        return


class TestElephantBrokerSearch(unittest.TestCase):
    def test_eb_auth_token_sends_both_supported_headers(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SearchHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                env = os.environ.copy()
                env.update(
                    {
                        "CLAUDE_PLUGIN_DATA": temp_dir,
                        "EB_SERVICE_URL": f"http://127.0.0.1:{server.server_port}",
                        "EB_AUTH_TOKEN": "test-token",
                        "EB_MODE": "true",
                    }
                )
                completed = subprocess.run(
                    [str(SCRIPTS_DIR / "elephantbroker-search.sh"), "header probe", "1"],
                    check=False,
                    capture_output=True,
                    env=env,
                    text=True,
                    timeout=10,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        headers_seen = {key.lower(): value for key, value in _SearchHandler.headers_seen.items()}
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(headers_seen.get("x-eb-auth-token"), "test-token")
        self.assertEqual(headers_seen.get("authorization"), "Bearer test-token")


if __name__ == "__main__":
    unittest.main()
