import os
import sys
import time
from typing import Any

import requests

DEFAULT_LOKI_URL = "http://localhost:3100/loki/api/v1/push"
DEFAULT_APP_NAME = "ai-learning-flashcards-api"

_SAFE_LABEL_KEYS = frozenset(
    {"endpoint", "method", "status_code", "duration_ms", "path", "service"}
)


def _sanitize_label_value(value: Any) -> str:
    return str(value)[:256]


def send_log_to_loki(message: str, level: str = "INFO", **labels: Any) -> None:
    loki_url = os.environ.get("LOKI_URL", DEFAULT_LOKI_URL)
    app_name = os.environ.get("APP_NAME", DEFAULT_APP_NAME)

    stream_labels: dict[str, str] = {
        "app": app_name,
        "level": level.upper(),
        "service": app_name,
    }

    for key, value in labels.items():
        if key in _SAFE_LABEL_KEYS:
            stream_labels[key] = _sanitize_label_value(value)

    timestamp_ns = str(int(time.time() * 1_000_000_000))

    payload = {
        "streams": [
            {
                "stream": stream_labels,
                "values": [[timestamp_ns, message]],
            }
        ]
    }

    try:
        response = requests.post(loki_url, json=payload, timeout=2)
        response.raise_for_status()
    except Exception as exc:
        print(f"WARNING: Failed to send log to Loki: {exc}", file=sys.stdout)
