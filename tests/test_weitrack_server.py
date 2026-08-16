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


def test_ingest_new_event_types(client):
    """采集扩展的新事件类型（snapshot/notification）可正常上报落库。"""
    c, storage = client
    payload = {
        "device_id": "dev1", "batch_id": "b2", "client_ts": 1000,
        "events": [
            {"type": "snapshot", "ts": 1000, "data": {"fg_pkg": "com.x", "battery": 73}},
            {"type": "notification", "ts": 1001, "data": {"pkg": "com.x", "clicked": False}},
        ],
    }
    r = c.post("/ingest", json=payload)
    assert r.status_code == 200
    assert r.json()["inserted"] == 2
    assert storage.event_count() == 2


def test_ingest_invalid_422(client):
    c, _ = client
    # type 不再限制枚举，422 改为缺 events / 缺 ts 等结构性错误
    r = c.post("/ingest", json={"device_id": "d", "batch_id": "b", "client_ts": 1, "events": [{"type": "nope", "data": {}}]})
    assert r.status_code == 422
