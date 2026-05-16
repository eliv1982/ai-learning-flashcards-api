from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@patch("main.send_log_to_loki")
def test_health(mock_send_log, client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert mock_send_log.called


@patch("main.send_log_to_loki")
def test_cards_list_not_empty(mock_send_log, client):
    response = client.get("/cards")
    assert response.status_code == 200
    assert len(response.json()) > 0
