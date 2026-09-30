import json
import threading
import time

import pytest
import requests
import requests.adapters
from fastapi.testclient import TestClient

from app.observability import LokiLogger
from main import create_app


@pytest.fixture(autouse=True)
def network_guard(monkeypatch):
    """No test may send real HTTP traffic (e.g. to Loki), whatever the environment says.

    LOKI_URL / APP_NAME are cleared so results do not depend on a developer's shell,
    and the ``requests`` transport is replaced so any attempted send is recorded and
    fails the test at teardown. A test that provokes attempts on purpose clears the list.
    """
    monkeypatch.delenv("LOKI_URL", raising=False)
    monkeypatch.delenv("APP_NAME", raising=False)
    attempts: list[str] = []

    def blocked_send(self, request, **kwargs):
        attempts.append(request.url)
        raise AssertionError(f"real HTTP request attempted in tests: {request.url}")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", blocked_send)
    yield attempts
    assert attempts == [], f"tests attempted real HTTP requests: {attempts}"


@pytest.fixture
def wait_until():
    def _wait_until(condition, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.01)
        return condition()

    return _wait_until


class RecordingLogger:
    """Stand-in for LokiLogger: records events, never touches a network."""

    def __init__(self):
        self.events = []
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self, timeout=None):
        self.stopped += 1

    def log(self, message, level="INFO", method=None):
        self.events.append({"message": message, "level": level, "method": method})
        return True

    def request_events(self):
        """Parsed request events (the startup event has no status_code)."""
        parsed = [{**e, "fields": json.loads(e["message"])} for e in self.events]
        return [e for e in parsed if "status_code" in e["fields"]]


class FakeResponse:
    def raise_for_status(self):
        pass


class FakeSession:
    """Stand-in for requests.Session: records posts; can fail or hang like Loki."""

    def __init__(self):
        self.calls = []
        self.error = None
        self.hanging = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        self.entered.set()
        if self.hanging:
            self.release.wait(30)
        if self.error is not None:
            raise self.error
        return FakeResponse()


@pytest.fixture
def recording_logger():
    return RecordingLogger()


@pytest.fixture
def fake_session():
    session = FakeSession()
    yield session
    session.release.set()


@pytest.fixture
def make_logger(fake_session):
    created = []

    def _make(**kwargs):
        logger = LokiLogger(
            "http://loki.invalid/loki/api/v1/push",
            "test-app",
            session=fake_session,
            **kwargs,
        )
        created.append(logger)
        return logger

    yield _make
    fake_session.release.set()
    for logger in created:
        logger.stop(timeout=2)


@pytest.fixture
def client(recording_logger):
    with TestClient(create_app(logger=recording_logger)) as test_client:
        yield test_client
