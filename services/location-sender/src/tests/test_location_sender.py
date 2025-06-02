import json
from unittest.mock import patch

import pytest
from location_sender import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_health_check_connected(client):
    with patch("location_sender.connection") as mock_conn:
        mock_conn.is_open = True
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json["status"] == "ok"
        assert response.json["rabbitmq"] == "connected"


def test_health_check_disconnected(client):
    with patch("location_sender.connection", None):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json["status"] == "ok"
        assert response.json["rabbitmq"] == "disconnected"


def test_gps_post_valid_data(client):
    with (
        patch("location_sender.connect_to_rabbitmq", return_value=True),
        patch("location_sender.channel") as mock_channel,
    ):
        payload = {
            "vehicle_id": "V123",
            "gps": {"lat": 52.52, "lon": 13.405},
            "timestamp": "2025-06-02T12:00:00Z",
        }
        response = client.post(
            "/gps", data=json.dumps(payload), content_type="application/json"
        )
        assert response.status_code == 200
        assert response.json["status"] == "sent"
        assert mock_channel.basic_publish.called


def test_gps_post_invalid_data(client):
    response = client.post("/gps", json={"wrong_key": "value"})
    assert response.status_code == 400
    assert "error" in response.json


def test_gps_post_connection_failure(client):
    with patch("location_sender.connect_to_rabbitmq", return_value=False):
        payload = {"vehicle_id": "V123", "gps": {"lat": 52.52, "lon": 13.405}}
        response = client.post("/gps", json=payload)
        assert response.status_code == 503
        assert response.json["error"] == "Message queue unavailable"
