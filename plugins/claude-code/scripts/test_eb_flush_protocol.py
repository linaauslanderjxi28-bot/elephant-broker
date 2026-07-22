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
from types import ModuleType
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).parent


@contextmanager
def loaded_plugins(temp_dir: str):
    previous = {name: sys.modules.get(name) for name in ("_plugin_common", "_plugin_eb")}
    try:
        with patch.dict(
            os.environ,
            {
                "CLAUDE_PLUGIN_DATA": temp_dir,
                "EB_SERVICE_URL": "http://127.0.0.1:8420",
                "EB_GATEWAY_ID": "test-gateway",
            },
            clear=True,
        ):
            common = _load_module("_plugin_common.py", "_plugin_common")
            sys.modules["_plugin_common"] = common
            eb = _load_module("_plugin_eb.py", "_plugin_eb")
            sys.modules["_plugin_eb"] = eb
            yield common, eb
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _load_module(filename: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _stage(common: ModuleType, question: str) -> None:
    common.remember_entry_via_http(
        "dataset-1",
        "session-1",
        {"type": "qa", "question": question, "answer": f"A-{question}"},
    )


class TestEmptyCacheSkipsNetwork(unittest.TestCase):
    """Regression: empty cache must return EMPTY without any /memory/status call."""

    def test_empty_cache_makes_zero_eb_request_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir) as (_common, eb):
            _eb_request_calls: list[str] = []
            original_eb_request = eb._eb_request

            def tracking_eb_request(path: str, *args, **kwargs):
                _eb_request_calls.append(path)
                return original_eb_request(path, *args, **kwargs)

            urlopen_calls: list[str] = []
            original_urlopen = eb.urllib.request.urlopen

            def tracking_urlopen(req, *args, **kwargs):
                urlopen_calls.append(str(req.full_url if hasattr(req, 'full_url') else req))
                return original_urlopen(req, *args, **kwargs)

            with (
                patch.object(eb.urllib.request, "urlopen", side_effect=tracking_urlopen),
                patch.object(eb, "_eb_request", side_effect=tracking_eb_request),
            ):
                result = eb.eb_persist_session("dataset-1", "session-1")

            self.assertEqual(result.status.value, "empty")
            self.assertTrue(result.terminal_success)
            # Health check uses urlopen directly — that's the only network call allowed
            self.assertEqual(
                urlopen_calls,
                [],
                f"expected no network calls for empty cache, got: {urlopen_calls}",
            )
            # _eb_request must be zero for empty cache — no /memory/status, no /memory/ingest-turn
            self.assertEqual(
                len(_eb_request_calls), 0,
                f"expected 0 _eb_request calls for empty cache, got {len(_eb_request_calls)}: {_eb_request_calls}",
            )

    def test_staged_cache_skips_memory_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir) as (common, eb):
            _stage(common, "Q1")
            _eb_request_calls: list[str] = []

            def tracking_eb_request(path: str, *args, **kwargs):
                _eb_request_calls.append(path)
                # Return valid response for any path — we only care about WHICH paths are called
                if path == "/memory/ingest-turn":
                    return {"facts_extracted": 1}
                return {"embedding_available": True}

            with (
                patch.object(eb.urllib.request, "urlopen"),
                patch.object(eb, "_eb_request", side_effect=tracking_eb_request),
            ):
                result = eb.eb_persist_session("dataset-1", "session-1")

            self.assertEqual(result.status.value, "flushed")
            # Must NOT contain /memory/status
            self.assertNotIn(
                "/memory/status", _eb_request_calls,
                f"_eb_request should never call /memory/status, got: {_eb_request_calls}",
            )
            # Must call /memory/ingest-turn to flush
            self.assertIn("/memory/ingest-turn", _eb_request_calls)


class TestFlushProtocol(unittest.TestCase):
    def test_success_acknowledges_submitted_prefix_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir) as (common, eb):
            _stage(common, "Q1")
            calls: list[list[dict[str, str]]] = []
            ingest_entered = threading.Event()

            def ingest(_key, messages, **_kwargs):
                calls.append(messages)
                if len(calls) == 1:
                    ingest_entered.set()
                return {"facts_extracted": 1}

            append_thread = threading.Thread(
                target=lambda: (ingest_entered.wait(timeout=2), _stage(common, "Q2")),
            )

            with (
                patch.object(eb.urllib.request, "urlopen"),
                patch.object(eb, "_eb_request", return_value={"embedding_available": True}),
                patch.object(eb, "eb_ingest_turn", side_effect=ingest),
            ):
                append_thread.start()
                first = eb.eb_persist_session("dataset-1", "session-1")
                append_thread.join(timeout=2)
                second = eb.eb_persist_session("dataset-1", "session-1")

            self.assertEqual(first.status.value, "flushed")
            self.assertEqual(second.status.value, "flushed")
            self.assertEqual(
                calls,
                [
                    [
                        {"role": "user", "content": "Q1"},
                        {"role": "assistant", "content": "A-Q1"},
                    ],
                    [
                        {"role": "user", "content": "Q2"},
                        {"role": "assistant", "content": "A-Q2"},
                    ],
                ],
            )

    def test_concurrent_appends_preserve_both_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir) as (common, _eb):
            barrier = threading.Barrier(2)

            def append(question: str) -> None:
                barrier.wait(timeout=2)
                _stage(common, question)

            threads = [
                threading.Thread(target=append, args=("Q1",)),
                threading.Thread(target=append, args=("Q2",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

            cache = json.loads((Path(temp_dir) / "http_bridge_cache.json").read_text())
            questions = {
                entry["question"] for entry in cache["eb:dataset-1:session-1"]["qa"]
            }
            self.assertEqual(questions, {"Q1", "Q2"})

    def test_ingest_failure_is_retryable_and_preserves_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir) as (common, eb):
            _stage(common, "Q1")
            with (
                patch.object(eb.urllib.request, "urlopen"),
                patch.object(eb, "_eb_request", return_value={"embedding_available": True}),
                patch.object(eb, "eb_ingest_turn", side_effect=OSError("network down")),
            ):
                result = eb.eb_persist_session("dataset-1", "session-1")

            cache = json.loads((Path(temp_dir) / "http_bridge_cache.json").read_text())
            self.assertEqual(result.status.value, "ingest_failed")
            self.assertTrue(result.retryable)
            self.assertEqual(cache["eb:dataset-1:session-1"]["qa"][0]["question"], "Q1")

    def test_empty_and_unchanged_are_non_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir) as (common, eb):
            with (
                patch.object(eb.urllib.request, "urlopen"),
                patch.object(eb, "_eb_request", return_value={"embedding_available": True}),
            ):
                empty = eb.eb_persist_session("dataset-1", "session-1")
            _stage(common, "Q1")
            with (
                patch.object(eb.urllib.request, "urlopen"),
                patch.object(eb, "_eb_request", return_value={"embedding_available": True}),
                patch.object(eb, "eb_ingest_turn", return_value={"facts_extracted": 1}),
            ):
                eb.eb_persist_session("dataset-1", "session-1")
                unchanged = eb.eb_persist_session("dataset-1", "session-1")

            self.assertEqual(empty.status.value, "empty")
            self.assertTrue(empty.terminal_success)
            self.assertIn(unchanged.status.value, {"empty", "unchanged"})
            self.assertTrue(unchanged.terminal_success)

    def test_concurrent_flushes_submit_one_batch_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir) as (common, eb):
            _stage(common, "Q1")
            entered = threading.Event()
            release = threading.Event()
            calls = 0

            def ingest(_key, _messages, **_kwargs):
                nonlocal calls
                calls += 1
                entered.set()
                release.wait(timeout=2)
                return {"facts_extracted": 1}

            results = []
            with (
                patch.object(eb.urllib.request, "urlopen"),
                patch.object(eb, "_eb_request", return_value={"embedding_available": True}),
                patch.object(eb, "eb_ingest_turn", side_effect=ingest),
            ):
                first = threading.Thread(
                    target=lambda: results.append(eb.eb_persist_session("dataset-1", "session-1"))
                )
                second = threading.Thread(
                    target=lambda: results.append(eb.eb_persist_session("dataset-1", "session-1"))
                )
                first.start()
                self.assertTrue(entered.wait(timeout=2))
                second.start()
                second.join(timeout=2)
                release.set()
                first.join(timeout=2)

            self.assertEqual(calls, 1)
            self.assertEqual({result.status.value for result in results}, {"flushed", "lock_busy"})


class TestSyncRetryProtocol(unittest.TestCase):
    def test_sync_failure_does_not_unregister(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir) as (_common, eb):
            sync = _load_module("sync-session-to-graph.py", "claude_sync_failure")
            resolved = ("session-1", "dataset-1", "", "agent-session-1", True, True, "session-1")
            failure = eb.PersistResult(eb.PersistStatus.INGEST_FAILED)
            with (
                patch.object(sync, "_load_resolved", return_value=resolved),
                patch.object(sync, "persist_session_cache_to_graph_via_http", return_value=failure),
                patch.object(sync, "unregister_agent_via_http") as unregister,
                patch.object(sync, "hook_log"),
            ):
                result = asyncio.run(sync._sync(stop_watcher=False, unregister_on_finish=True))

            self.assertEqual(result.status.value, "ingest_failed")
            unregister.assert_not_called()

    def test_detached_main_retries_failure_and_releases_final_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir) as (_common, eb):
            sync = _load_module("sync-session-to-graph.py", "claude_sync_retry")
            failure = eb.PersistResult(eb.PersistStatus.HEALTH_FAILED)
            with (
                patch.dict(
                    os.environ,
                    {
                        "CLAUDE_PLUGIN_DATA": temp_dir,
                        "COGNEE_SESSION_KEY": "session-1",
                        "COGNEE_SYNC_RETRIES": "2",
                        "COGNEE_SYNC_RETRY_DELAY": "0",
                    },
                    clear=True,
                ),
                patch.object(sys, "argv", ["sync-session-to-graph.py", "--detached-final"]),
                patch.object(sync, "_sync", return_value=failure) as run_sync,
                patch.object(sync, "hook_log"),
            ):
                sync.main()
                claim_again = sync._claim_final_sync_once()

            self.assertEqual(run_sync.call_count, 2)
            self.assertTrue(claim_again)

    def test_detached_main_releases_final_claim_when_sync_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir):
            sync = _load_module("sync-session-to-graph.py", "claude_sync_exception")
            with (
                patch.dict(
                    os.environ,
                    {
                        "CLAUDE_PLUGIN_DATA": temp_dir,
                        "COGNEE_SESSION_KEY": "session-1",
                        "COGNEE_SYNC_RETRIES": "2",
                        "COGNEE_SYNC_RETRY_DELAY": "0",
                    },
                    clear=True,
                ),
                patch.object(sys, "argv", ["sync-session-to-graph.py", "--detached-final"]),
                patch.object(sync, "_sync", side_effect=RuntimeError("boom")) as run_sync,
                patch.object(sync, "hook_log"),
            ):
                sync.main()
                claim_again = sync._claim_final_sync_once()

            self.assertEqual(run_sync.call_count, 2)
            self.assertTrue(claim_again)


class _SearchHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:
        type(self).calls += 1
        size = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(size)
        payload = b"[]"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args) -> None:
        return


class TestSearchUrlPriority(unittest.TestCase):
    def test_search_uses_eb_runtime_url_when_it_is_only_url(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SearchHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _SearchHandler.calls = 0
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                env = {
                    "HOME": os.environ.get("HOME", "/tmp"),
                    "PATH": os.environ.get("PATH", ""),
                    "CLAUDE_PLUGIN_DATA": temp_dir,
                    "EB_RUNTIME_URL": f"http://127.0.0.1:{server.server_port}",
                    "EB_GATEWAY_ID": "test-gateway",
                }
                completed = subprocess.run(
                    [str(SCRIPTS_DIR / "elephantbroker-search.sh"), "runtime url", "1"],
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

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(_SearchHandler.calls, 1)


class TestModuleIsolation(unittest.TestCase):
    def test_plugin_loader_restores_sys_modules(self) -> None:
        sentinel_common = ModuleType("_plugin_common")
        sentinel_eb = ModuleType("_plugin_eb")
        with patch.dict(
            sys.modules,
            {"_plugin_common": sentinel_common, "_plugin_eb": sentinel_eb},
        ):
            with tempfile.TemporaryDirectory() as temp_dir, loaded_plugins(temp_dir):
                self.assertIsNot(sys.modules["_plugin_common"], sentinel_common)
            self.assertIs(sys.modules["_plugin_common"], sentinel_common)
            self.assertIs(sys.modules["_plugin_eb"], sentinel_eb)


if __name__ == "__main__":
    unittest.main()
