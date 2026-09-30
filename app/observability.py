import json
import os
import queue
import sys
import threading
import time
from typing import Any, Protocol

import requests
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_LOKI_URL = "http://localhost:3100/loki/api/v1/push"
DEFAULT_APP_NAME = "ai-learning-flashcards-api"

LOKI_TIMEOUT = (1.0, 2.0)  # (connect, read) seconds
MAX_QUEUE_SIZE = 1000
WORKER_NAME = "loki-log-worker"
UNMATCHED_ROUTE = "unmatched"

_POLL_SECONDS = 0.25
_WARNING_INTERVAL_SECONDS = 60.0
_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


class EventLogger(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def log(self, message: str, level: str = "INFO", method: str | None = None) -> bool: ...


def _bounded_method(method: str) -> str:
    method = method.upper()
    return method if method in _METHODS else "OTHER"


def level_for_status(status_code: int) -> str:
    if status_code >= 500:
        return "ERROR"
    if status_code >= 400:
        return "WARNING"
    return "INFO"


class LokiLogger:
    """Best-effort Loki delivery that never runs on the request path.

    ``log`` only enqueues an event on a bounded queue and returns immediately;
    one daemon worker thread posts queued events to Loki. When Loki is slow or
    down the queue fills and further events are dropped, so the API is never
    blocked. Loki stream labels are limited to ``app``, ``level`` and ``method``.
    """

    def __init__(
        self,
        url: str = DEFAULT_LOKI_URL,
        app_name: str = DEFAULT_APP_NAME,
        *,
        max_queue: int = MAX_QUEUE_SIZE,
        timeout: tuple[float, float] = LOKI_TIMEOUT,
        session: Any = None,
    ) -> None:
        self._url = url
        self._app_name = app_name
        self._timeout = timeout
        self._session = session if session is not None else requests.Session()
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=max_queue)
        # Lifecycle state, all guarded by ``_lock``: ``_thread`` is the one tracked
        # worker (even while it is being stopped), ``_active`` is whether the logger
        # is currently requested to run, ``_stop_event`` belongs to ``_thread``.
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active = False
        self._stop_event = threading.Event()
        self._last_warning = float("-inf")
        # Best-effort counters, not synchronised.
        self.sent = 0
        self.failed = 0
        self.dropped = 0

    @classmethod
    def from_env(cls, **kwargs: Any) -> "LokiLogger":
        return cls(
            os.environ.get("LOKI_URL", DEFAULT_LOKI_URL),
            os.environ.get("APP_NAME", DEFAULT_APP_NAME),
            **kwargs,
        )

    def start(self) -> None:
        """Request the logger to run and start the worker unless one is alive.

        A worker that ``stop`` could not join in time stays tracked in
        ``_thread``. While it is alive this does not spawn a second worker (at
        most one worker ever consumes the queue and uses the session); it only
        records that the logger is wanted again, and that worker starts exactly
        one replacement as it exits.
        """
        with self._lock:
            self._active = True
            if self._thread is not None and self._thread.is_alive():
                return
            self._spawn_worker()

    def stop(self, timeout: float = 2.0) -> None:
        """Ask the worker to exit and wait at most ``timeout`` seconds.

        Queued events are not flushed. A worker stuck in a slow Loki request is
        left running (it is a daemon thread and exits once that request times
        out) but remains tracked, so ``start`` cannot spawn a second worker
        next to it. Clearing the requested state here also cancels a pending
        replacement: a worker that exits after ``stop`` hands nothing over.
        """
        with self._lock:
            self._active = False
            thread = self._thread
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout)

    def _spawn_worker(self) -> None:
        """Start a fresh worker and track it. The caller holds ``_lock``."""
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._run, args=(stop_event,), name=WORKER_NAME, daemon=True
        )
        thread.start()
        self._stop_event = stop_event
        self._thread = thread

    def _worker_exited(self) -> None:
        """Release the exiting worker's slot, starting a replacement if wanted.

        Runs on the exiting worker as its last action, under ``_lock`` so it is
        ordered against ``start`` and ``stop``: if ``stop`` ran last ``_active``
        is False and nothing starts; if ``start`` ran last exactly one
        replacement does. A worker that no longer owns ``_thread`` leaves it alone.
        """
        with self._lock:
            if self._thread is not threading.current_thread():
                return
            self._thread = None
            if self._active:
                try:
                    self._spawn_worker()
                except Exception:
                    pass  # best-effort: a later start() tries again

    def log(self, message: str, level: str = "INFO", method: str | None = None) -> bool:
        """Enqueue one event. Returns False if it was dropped. Never raises."""
        try:
            level = level.upper()
            stream = {"app": self._app_name, "level": level if level in _LEVELS else "INFO"}
            if method:
                stream["method"] = _bounded_method(method)
            payload = {
                "streams": [
                    {"stream": stream, "values": [[str(time.time_ns()), message]]}
                ]
            }
            self._queue.put_nowait(payload)
        except Exception:
            self.dropped += 1
            return False
        return True

    def _run(self, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                try:
                    payload = self._queue.get(timeout=_POLL_SECONDS)
                except queue.Empty:
                    continue
                try:
                    self._send(payload)
                except Exception:
                    pass
        finally:
            self._worker_exited()

    def _send(self, payload: dict) -> None:
        try:
            response = self._session.post(self._url, json=payload, timeout=self._timeout)
            response.raise_for_status()
        except Exception as exc:
            self.failed += 1
            self._warn(exc)
        else:
            self.sent += 1

    def _warn(self, exc: Exception) -> None:
        now = time.monotonic()
        if now - self._last_warning < _WARNING_INTERVAL_SECONDS:
            return
        self._last_warning = now
        print(
            f"WARNING: Failed to send log to Loki: {exc} "
            f"(failed={self.failed}, dropped={self.dropped})",
            file=sys.stdout,
        )


class RequestLoggingMiddleware:
    """Logs one event per HTTP request, including unhandled exceptions (as 500).

    Only method, route template, status code and duration are logged - never
    the raw path, query string, headers, cookies or body.
    """

    def __init__(self, app: ASGIApp, logger: EventLogger) -> None:
        self.app = app
        self._logger = logger

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code: int | None = None
        error_type: str | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            status_code = 500
            error_type = type(exc).__name__
            raise
        finally:
            if status_code is not None:
                self._emit(scope, status_code, start, error_type)

    def _emit(
        self, scope: Scope, status_code: int, start: float, error_type: str | None
    ) -> None:
        try:
            method = _bounded_method(scope.get("method", ""))
            fields: dict[str, Any] = {
                "method": method,
                "route": getattr(scope.get("route"), "path", None) or UNMATCHED_ROUTE,
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            }
            if error_type:
                fields["error_type"] = error_type
            self._logger.log(
                json.dumps(fields), level=level_for_status(status_code), method=method
            )
        except Exception:
            pass
