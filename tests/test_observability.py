import json
import threading
import time

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import (
    DEFAULT_APP_NAME,
    DEFAULT_LOKI_URL,
    UNMATCHED_ROUTE,
    WORKER_NAME,
    LokiLogger,
    RequestLoggingMiddleware,
    level_for_status,
)
from main import create_app

ALLOWED_LABELS = {"app", "level", "method"}


def workers():
    return [t for t in threading.enumerate() if t.name == WORKER_NAME and t.is_alive()]


def sent_streams(session):
    return [call["json"]["streams"][0] for call in session.calls]


# --- LokiLogger: payload, labels, configuration ---------------------------------


def test_payload_format_and_labels(make_logger, fake_session, wait_until):
    logger = make_logger()
    logger.start()
    assert logger.log('{"route": "/health"}', level="info", method="get")
    assert wait_until(lambda: len(fake_session.calls) == 1)

    call = fake_session.calls[0]
    assert call["url"] == "http://loki.invalid/loki/api/v1/push"
    stream = call["json"]["streams"][0]
    assert stream["stream"] == {"app": "test-app", "level": "INFO", "method": "GET"}
    timestamp, message = stream["values"][0]
    assert timestamp.isdigit()
    assert message == '{"route": "/health"}'


def test_send_uses_explicit_connect_and_read_timeout(make_logger, fake_session, wait_until):
    logger = make_logger()
    logger.start()
    logger.log("hello")
    assert wait_until(lambda: len(fake_session.calls) == 1)
    assert fake_session.calls[0]["timeout"] == (1, 2)


def test_unbounded_label_values_are_normalised(make_logger, fake_session, wait_until):
    logger = make_logger()
    logger.start()
    logger.log("x", level="weird-level", method="/cards/12345")
    assert wait_until(lambda: len(fake_session.calls) == 1)
    assert sent_streams(fake_session)[0]["stream"] == {
        "app": "test-app",
        "level": "INFO",
        "method": "OTHER",
    }


def test_from_env_defaults_and_overrides(monkeypatch):
    default = LokiLogger.from_env()
    assert default._url == DEFAULT_LOKI_URL
    assert default._app_name == DEFAULT_APP_NAME

    monkeypatch.setenv("LOKI_URL", "http://loki:3100/loki/api/v1/push")
    monkeypatch.setenv("APP_NAME", "custom-app")
    custom = LokiLogger.from_env()
    assert custom._url == "http://loki:3100/loki/api/v1/push"
    assert custom._app_name == "custom-app"


@pytest.mark.parametrize(
    ("status", "level"),
    [(200, "INFO"), (204, "INFO"), (307, "INFO"), (404, "WARNING"), (422, "WARNING"), (500, "ERROR"), (503, "ERROR")],
)
def test_level_for_status(status, level):
    assert level_for_status(status) == level


# --- Queue behaviour ---------------------------------------------------------------


def test_full_queue_drops_events_without_raising(make_logger):
    logger = make_logger(max_queue=2)  # worker not started: nothing drains the queue
    results = [logger.log(f"event {i}") for i in range(5)]
    assert results == [True, True, False, False, False]
    assert logger.dropped == 3


def test_full_queue_never_breaks_a_request(make_logger):
    logger = make_logger(max_queue=1)
    assert logger.log("fills the queue")

    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware, logger=logger)
    app.add_api_route("/ping", lambda: {"ok": True})

    with TestClient(app) as client:
        assert client.get("/ping").json() == {"ok": True}
    assert logger.dropped == 1


def test_logger_that_raises_never_breaks_a_request():
    class ExplodingLogger:
        def log(self, *args, **kwargs):
            raise RuntimeError("logging is broken")

    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware, logger=ExplodingLogger())
    app.add_api_route("/ping", lambda: {"ok": True})

    with TestClient(app) as client:
        assert client.get("/ping").status_code == 200


# --- Worker lifecycle ----------------------------------------------------------------


def test_start_is_idempotent_and_stop_ends_the_worker(make_logger):
    logger = make_logger()
    logger.start()
    logger.start()
    assert len(workers()) == 1

    logger.stop()
    assert workers() == []


def test_worker_can_be_restarted_after_stop(make_logger, fake_session, wait_until):
    logger = make_logger()
    logger.start()
    logger.stop()
    logger.start()
    assert len(workers()) == 1

    logger.log("after restart")
    assert wait_until(lambda: len(fake_session.calls) == 1)


def test_app_lifespan_starts_and_stops_exactly_one_worker(make_logger):
    logger = make_logger()
    app = create_app(logger=logger)

    for _ in range(2):
        with TestClient(app) as client:
            client.get("/health")
            assert len(workers()) == 1
        assert workers() == []


def test_stop_returns_promptly_even_if_loki_hangs(make_logger, fake_session, wait_until):
    fake_session.hanging = True
    logger = make_logger()
    logger.start()
    logger.log("gets stuck in flight")
    assert fake_session.entered.wait(5)

    started = time.monotonic()
    logger.stop(timeout=0.3)
    assert time.monotonic() - started < 3

    fake_session.release.set()
    assert wait_until(lambda: workers() == [])  # the abandoned worker exits once unstuck


def test_restart_after_timed_out_stop_never_runs_two_workers(make_logger, fake_session, wait_until):
    fake_session.hanging = True
    logger = make_logger()
    logger.start()
    logger.log("gets stuck in flight")
    assert fake_session.entered.wait(5)
    (original,) = workers()

    started = time.monotonic()
    logger.stop(timeout=0.3)
    assert time.monotonic() - started < 3
    assert original.is_alive()  # stop() timed out; the worker is still inside the send

    logger.start()  # must not spawn a replacement next to the still-live worker
    logger.start()  # ...however often it is repeated
    assert workers() == [original]

    fake_session.hanging = False
    fake_session.release.set()
    original.join(5)
    assert not original.is_alive()  # the original worker really terminated

    # start() was requested meanwhile, so exactly one replacement took over.
    assert wait_until(lambda: len(workers()) == 1)
    (replacement,) = workers()
    assert replacement is not original
    logger.start()
    assert workers() == [replacement]

    logger.log("after restart")
    assert wait_until(lambda: len(fake_session.calls) == 2)  # the new worker delivers

    logger.stop()
    assert workers() == []


def track_session_use(session):
    """Wrap ``session.post`` to record which threads use it, and whether any overlap.

    ``live_workers`` is the number of live worker threads at the moment each call
    begins, so a duplicate worker would show up as a value above 1.
    """
    record = {"threads": [], "in_flight": 0, "max_in_flight": 0, "live_workers": []}
    guard = threading.Lock()
    real_post = session.post

    def post(*args, **kwargs):
        with guard:
            record["threads"].append(threading.current_thread())
            record["live_workers"].append(len(workers()))
            record["in_flight"] += 1
            record["max_in_flight"] = max(record["max_in_flight"], record["in_flight"])
        try:
            return real_post(*args, **kwargs)
        finally:
            with guard:
                record["in_flight"] -= 1

    session.post = post
    return record


def hang_worker_then_stop(logger, fake_session):
    """Leave one worker alive inside a hanging send after a bounded ``stop``."""
    fake_session.hanging = True
    logger.start()
    logger.log("gets stuck in flight")
    assert fake_session.entered.wait(5)
    (original,) = workers()

    started = time.monotonic()
    logger.stop(timeout=0.3)
    assert time.monotonic() - started < 3
    assert original.is_alive()
    return original


def test_overlapping_restart_hands_off_to_exactly_one_replacement(make_logger, fake_session, wait_until):
    use = track_session_use(fake_session)
    logger = make_logger()
    original = hang_worker_then_stop(logger, fake_session)

    logger.start()  # the new lifespan begins while the old worker is still stopping
    assert workers() == [original]  # no concurrent replacement

    fake_session.hanging = False
    fake_session.release.set()
    # The old worker exits and hands over: a single replacement appears, then the old one is gone.
    assert wait_until(lambda: len(workers()) == 1 and workers()[0] is not original)
    (replacement,) = workers()
    assert not original.is_alive()

    logger.start()  # still idempotent
    assert workers() == [replacement]

    logger.log("queued for the replacement")
    assert wait_until(lambda: len(fake_session.calls) == 2)
    assert use["threads"] == [original, replacement]  # the replacement consumed the new event
    assert workers() == [replacement]

    logger.stop()
    assert workers() == []
    assert use["max_in_flight"] == 1  # the session was never used concurrently
    assert use["live_workers"] == [1, 1]  # no call began while another worker was alive


def test_restart_cancelled_before_handoff_starts_no_replacement(make_logger, fake_session, wait_until):
    use = track_session_use(fake_session)
    logger = make_logger()
    original = hang_worker_then_stop(logger, fake_session)

    logger.start()  # a new lifespan is requested...
    assert workers() == [original]
    started = time.monotonic()
    logger.stop(timeout=0.3)  # ...and cancelled again before the old worker exits
    assert time.monotonic() - started < 3
    assert workers() == [original]

    fake_session.hanging = False
    fake_session.release.set()
    original.join(5)
    assert not original.is_alive()
    # A replacement would have been spawned before the original terminated.
    assert workers() == []

    # Nothing is left in a half-stopped state: a fresh start() works normally.
    logger.start()
    (fresh,) = workers()
    assert fresh is not original
    logger.log("after the cancelled restart")
    assert wait_until(lambda: len(fake_session.calls) == 2)
    assert use["threads"] == [original, fresh]

    logger.stop()
    assert workers() == []
    assert use["max_in_flight"] == 1


def test_repeated_lifespan_over_a_stopping_worker_gets_one_replacement(make_logger, fake_session, wait_until):
    logger = make_logger()
    real_stop = logger.stop
    # create_app calls stop() with no arguments; keep each lifespan's bounded shutdown short.
    logger.stop = lambda timeout=0.3: real_stop(timeout)
    app = create_app(logger=logger)
    fake_session.hanging = True

    with TestClient(app):
        assert fake_session.entered.wait(5)  # the startup event is stuck inside Loki
        (original,) = workers()
    assert original.is_alive()  # lifespan shutdown was bounded, not completed

    with TestClient(app) as client:  # the next lifespan overlaps the old worker's shutdown
        client.get("/health")
        assert workers() == [original]

        fake_session.hanging = False
        fake_session.release.set()
        # Startup event #1 (hung) + startup event #2 and the request, delivered by the replacement.
        assert wait_until(lambda: len(fake_session.calls) == 3)
        assert wait_until(lambda: len(workers()) == 1 and workers()[0] is not original)
    assert workers() == []


# --- Request logging semantics -------------------------------------------------------


def test_successful_request_is_info(recording_logger, client):
    client.get("/health")
    (event,) = recording_logger.request_events()
    assert event["level"] == "INFO"
    assert event["method"] == "GET"
    assert event["fields"]["status_code"] == 200
    assert event["fields"]["route"] == "/health"
    assert event["fields"]["duration_ms"] >= 0


def test_not_found_is_warning_with_route_template(recording_logger, client):
    client.get("/cards/12345")
    (event,) = recording_logger.request_events()
    assert event["level"] == "WARNING"
    assert event["fields"]["status_code"] == 404
    assert event["fields"]["route"] == "/cards/{card_id}"
    assert "12345" not in event["message"]


def test_validation_error_is_warning(recording_logger, client):
    client.get("/cards/abc")
    (event,) = recording_logger.request_events()
    assert event["level"] == "WARNING"
    assert event["fields"]["status_code"] == 422


def test_unmatched_path_uses_stable_route_and_hides_the_path(recording_logger, client):
    client.get("/wp-admin/setup.php?token=hunter2")
    (event,) = recording_logger.request_events()
    assert event["level"] == "WARNING"
    assert event["fields"]["route"] == UNMATCHED_ROUTE
    assert "wp-admin" not in event["message"]
    assert "hunter2" not in event["message"]


def test_request_secrets_are_never_logged(recording_logger, client):
    client.post(
        "/health?api_key=query-secret",
        headers={"Authorization": "Bearer header-secret", "Cookie": "session=cookie-secret"},
        json={"password": "body-secret"},
    )
    (event,) = recording_logger.request_events()
    for secret in ["query-secret", "header-secret", "cookie-secret", "body-secret"]:
        assert secret not in event["message"]


def boom():
    raise RuntimeError("kaboom")


def test_unhandled_exception_logs_one_500_error_and_still_returns_500(recording_logger):
    app = create_app(logger=recording_logger)
    app.add_api_route("/boom", boom)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    (event,) = recording_logger.request_events()
    assert event["level"] == "ERROR"
    assert event["fields"]["status_code"] == 500
    assert event["fields"]["route"] == "/boom"
    assert event["fields"]["error_type"] == "RuntimeError"
    assert "kaboom" not in event["message"]


def test_unhandled_exception_is_not_swallowed(recording_logger):
    app = create_app(logger=recording_logger)
    app.add_api_route("/boom", boom)

    with TestClient(app, raise_server_exceptions=True) as client:
        with pytest.raises(RuntimeError, match="kaboom"):
            client.get("/boom")

    (event,) = recording_logger.request_events()
    assert event["fields"]["status_code"] == 500


def test_loki_labels_stay_within_the_stable_set(make_logger, fake_session, wait_until):
    logger = make_logger()
    with TestClient(create_app(logger=logger)) as client:
        client.get("/cards/12345")
        client.get("/cards/98765")
        client.get("/wp-admin/setup.php")
        client.request("FOO", "/health")
        client.get("/health")
        assert wait_until(lambda: len(fake_session.calls) == 6)  # startup + 5 requests

    for stream in sent_streams(fake_session):
        assert set(stream["stream"]) <= ALLOWED_LABELS
        label_values = " ".join(stream["stream"].values())
        for raw in ["12345", "98765", "wp-admin", "FOO"]:
            assert raw not in label_values
    payload_text = json.dumps([s["values"] for s in sent_streams(fake_session)])
    assert "12345" not in payload_text and "wp-admin" not in payload_text
    assert "/cards/{card_id}" in payload_text


# --- Non-blocking behaviour ----------------------------------------------------------


def test_health_stays_responsive_while_loki_hangs(make_logger, fake_session):
    fake_session.hanging = True  # accepts the request, never answers
    logger = make_logger(max_queue=5)
    # Failsafe: if sending ever becomes synchronous again, unstick it so the test
    # fails with an assertion after ~15s instead of hanging the whole suite.
    watchdog = threading.Timer(15, fake_session.release.set)
    watchdog.start()

    try:
        started = time.monotonic()
        with TestClient(create_app(logger=logger)) as client:  # startup logs too
            assert fake_session.entered.wait(5)  # worker is now stuck inside a Loki call
            for _ in range(50):
                assert client.get("/health").status_code == 200
            elapsed = time.monotonic() - started

            # A synchronous sender would have blocked on the very first log event.
            assert elapsed < 10
            assert logger.dropped > 0
            assert len(fake_session.calls) == 1
            fake_session.release.set()
    finally:
        watchdog.cancel()


def test_health_stays_responsive_while_loki_refuses_connections(make_logger, fake_session, wait_until):
    fake_session.error = requests.ConnectionError("connection refused")
    logger = make_logger()

    with TestClient(create_app(logger=logger)) as client:
        for _ in range(20):
            assert client.get("/health").status_code == 200
        assert wait_until(lambda: logger.failed >= 1)
    assert logger.sent == 0


def test_delivery_failure_warning_is_throttled(make_logger, fake_session, wait_until, capsys):
    fake_session.error = requests.ConnectionError("connection refused")
    logger = make_logger()
    logger.start()
    for i in range(10):
        logger.log(f"event {i}")
    assert wait_until(lambda: logger.failed == 10)

    warnings = [l for l in capsys.readouterr().out.splitlines() if l.startswith("WARNING")]
    assert len(warnings) == 1
    assert "Failed to send log to Loki" in warnings[0]


# --- Test isolation -----------------------------------------------------------------


def test_real_loki_url_in_environment_cannot_reach_the_network(monkeypatch, network_guard, wait_until):
    monkeypatch.setenv("LOKI_URL", "http://loki.example.com:3100/loki/api/v1/push")
    logger = LokiLogger.from_env()  # real requests.Session, pointed at a "real" Loki

    with TestClient(create_app(logger=logger)) as client:
        client.get("/health")
        # The guard replaced the requests transport, so the send was intercepted, not made.
        assert wait_until(lambda: len(network_guard) >= 1)

    assert network_guard[0].startswith("http://loki.example.com:3100/")
    network_guard.clear()  # expected here; any other test attempting it fails at teardown
