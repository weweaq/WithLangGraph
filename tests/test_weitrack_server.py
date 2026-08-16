from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gacore.weitrack.server import create_app
from gacore.weitrack.storage import Storage


@pytest.fixture
def client(tmp_path):
    storage = Storage(tmp_path / "wei_track.db")
    app = create_app(storage)
    c = TestClient(app)
    yield c, storage
    storage.close()


def test_health(client):
    c, _ = client
    assert c.get("/health").json() == {"status": "ok"}


def test_ingest_inserts(client):
    c, storage = client
    payload = {
        "device_id": "dev1", "batch_id": "b1", "client_ts": 1000,
        "events": [{"type": "usage", "ts": 1000, "data": {"pkg": "com.x", "foreground_ms": 5}}],
    }
    r = c.post("/ingest", json=payload)
    assert r.status_code == 200
    assert r.json()["inserted"] == 1
    assert storage.event_count() == 1


def test_ingest_idempotent(client):
    c, storage = client
    payload = {
        "device_id": "dev1", "batch_id": "b1", "client_ts": 1000,
        "events": [{"type": "usage", "ts": 1000, "data": {"pkg": "com.x"}}],
    }
    c.post("/ingest", json=payload)
    r2 = c.post("/ingest", json=payload)
    assert r2.status_code == 200
    assert r2.json()["deduplicated"] is True
    assert storage.event_count() == 1


def test_ingest_invalid_422(client):
    c, _ = client
    r = c.post("/ingest", json={"device_id": "d", "batch_id": "b", "client_ts": 1, "events": [{"type": "nope", "ts": 1, "data": {}}]})
    assert r.status_code == 422
