from unittest.mock import MagicMock, patch

import pytest
import requests

from app.observability import DEFAULT_APP_NAME, DEFAULT_LOKI_URL, send_log_to_loki


def test_send_log_to_loki_payload_format():
    with patch("app.observability.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        send_log_to_loki(
            "GET /health 200 1.5ms",
            level="INFO",
            method="GET",
            endpoint="/health",
            status_code=200,
            duration_ms=1.5,
        )

    mock_post.assert_called_once()
    url = mock_post.call_args.args[0]
    payload = mock_post.call_args.kwargs["json"]

    assert url == DEFAULT_LOKI_URL
    assert len(payload["streams"]) == 1
    stream = payload["streams"][0]
    assert stream["stream"]["app"] == DEFAULT_APP_NAME
    assert stream["stream"]["level"] == "INFO"
    assert stream["stream"]["method"] == "GET"
    assert stream["stream"]["endpoint"] == "/health"
    assert stream["stream"]["status_code"] == "200"
    values = stream["values"][0]
    assert isinstance(values[0], str)
    assert values[0].isdigit()
    assert values[1] == "GET /health 200 1.5ms"


def test_send_log_to_loki_does_not_raise_on_failure():
    with patch(
        "app.observability.requests.post",
        side_effect=requests.ConnectionError("Loki unavailable"),
    ):
        send_log_to_loki("test message", level="ERROR")


def test_send_log_to_loki_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("LOKI_URL", "http://loki:3100/loki/api/v1/push")
    monkeypatch.setenv("APP_NAME", "custom-app")

    with patch("app.observability.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        send_log_to_loki("hello")

    url = mock_post.call_args.args[0]
    stream = mock_post.call_args.kwargs["json"]["streams"][0]["stream"]
    assert url == "http://loki:3100/loki/api/v1/push"
    assert stream["app"] == "custom-app"
