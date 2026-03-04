from fastapi.testclient import TestClient

from sts_monitor.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_and_run_investigation() -> None:
    created = client.post("/investigations", json={"topic": "Major incident"})
    assert created.status_code == 200
    investigation_id = created.json()["id"]

    run = client.post(f"/investigations/{investigation_id}/run")
    assert run.status_code == 200
    payload = run.json()
    assert payload["investigation_id"] == investigation_id
    assert "confidence" in payload
