"""FastAPI 应用：POST /ingest 接收上报，GET /health 健康检查，GET /dashboard 仪表盘。"""
from __future__ import annotations

import sqlite3
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from gacore.weitrack.dashboard import DB_PATH, render_dashboard_html
from gacore.weitrack.schemas import IngestRequest
from gacore.weitrack.storage import Storage


def create_app(storage: Storage) -> FastAPI:
    app = FastAPI(title="weiTrack ingest")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(day: str | None = None) -> str:
        conn = sqlite3.connect(DB_PATH)
        try:
            return render_dashboard_html(conn, day)
        finally:
            conn.close()

    @app.post("/ingest")
    def ingest(req: IngestRequest) -> dict:
        received_at = int(time.time() * 1000)
        storage.upsert_device(req.device_id, req.client_ts)

        inserted = storage.ingest_batch(
            req.batch_id,
            req.device_id,
            received_at,
            [(ev.ts, ev.type, ev.data) for ev in req.events],
        )
        if not inserted:
            return {"status": "ok", "inserted": 0, "deduplicated": True}

        return {"status": "ok", "inserted": len(req.events), "deduplicated": False}

    return app
