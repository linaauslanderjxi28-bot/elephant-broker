from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

WriteWork = Callable[[], None]


class WriteQueue:
    def __init__(self, thread_name: str = "eb-sync-turn") -> None:
        self._thread_name = thread_name
        self._queue: queue.Queue[WriteWork | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def enqueue(self, work: WriteWork) -> None:
        self._queue.put(work)
        self._ensure_worker()

    def flush(self, timeout: float = 5.0) -> bool:
        done = threading.Event()

        def mark_done() -> None:
            done.set()

        self.enqueue(mark_done)
        return done.wait(timeout)

    def shutdown(self, *, flush_timeout: float = 5.0, join_timeout: float = 3.0) -> bool:
        flushed = self.flush(flush_timeout)
        if self._thread and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=join_timeout)
        return flushed

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._drain, daemon=True, name=self._thread_name)
            self._thread.start()

    def _drain(self) -> None:
        while True:
            work = self._queue.get()
            try:
                if work is None:
                    return
                work()
            except Exception as e:
                logger.warning("ElephantBroker background write failed: %s", e)
            finally:
                self._queue.task_done()

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread
