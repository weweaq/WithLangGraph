"""FastAPI 应用：POST /ingest 接收上报，GET /health 健康检查。"""
from __future__ import annotations

import time

from fastapi import FastAPI

from gacore.weitrack.schemas import IngestRequest
from gacore.weitrack.storage import Storage


def create_app(storage: Storage) -> FastAPI:
    app = FastAPI(title="weiTrack ingest")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/ingest")
    def ingest(req: IngestRequest) -> dict:
        received_at = int(time.time() * 1000)
        storage.upsert_device(req.device_id, req.client_ts)

        if not storage.register_batch(req.batch_id, req.device_id, received_at):
            return {"status": "ok", "inserted": 0, "deduplicated": True}

        for ev in req.events:
            storage.insert_event(req.device_id, ev.ts, ev.type, ev.data, received_at)
        return {"status": "ok", "inserted": len(req.events), "deduplicated": False}

    return app
