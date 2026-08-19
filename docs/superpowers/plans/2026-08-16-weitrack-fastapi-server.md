# weiTrackApp FastAPI 接收端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 WithLangGraph 项目内实现一个轻量 FastAPI 服务，接收 weiCheckApp 上报的手机使用事件，幂等去重后落库到 SQLite，供日后 WithLangGraph 画像生成读取。

**Architecture:** 独立 `src/gacore/weitrack/` 模块，含 FastAPI 应用（`POST /ingest` + `GET /health`）、SQLite 存储封装、Pydantic schema。数据以 JSON 原文存 `payload` 列，`ingested_batches` 表做幂等去重。

**Tech Stack:** Python 3.12、FastAPI、uvicorn、pydantic（用标准库 `sqlite3`，不引 ORM）、httpx（测试用 TestClient）。

**Spec:** `docs/superpowers/specs/2026-08-16-weitrack-ingest-design.md`

## Global Constraints

- 项目根：`D:\AAAmyPrj\github\myrepos\WithLangGraph`，Python venv：`.venv\Scripts\python.exe`
- 遵循项目约定：src 布局（`src/gacore/`）、JSONL 结构化日志（`logs/YYYY-MM-DD/app.jsonl`）、pytest + asyncio_mode=auto、ruff line-length=120
- 依赖需新增到 `pyproject.toml`：`fastapi`、`uvicorn`（sqlite3/pydantic 用现有或标准库）
- 零鉴权（局域网自用），只做字段格式校验
- 事件 `type` ∈ {`usage`, `session`}；`session.data.kind` ∈ {`screen_on`, `screen_off`, `unlock`, `app_switch`}
- 服务器只校验外层（`device_id`/`batch_id`/`type`/`ts`），`payload` 内部形状不校验，存原文
- 命名：模块用 `weitrack`，数据库文件 `wei_track.db`

---

### Task 1: 新增依赖并搭建模块骨架

**Files:**
- Modify: `pyproject.toml`（dependencies 加 `fastapi`、`uvicorn`）
- Create: `src/gacore/weitrack/__init__.py`

**Interfaces:**
- Consumes: 无
- Produces: `weitrack` 包可导入（`from gacore.weitrack import ...`）

- [ ] **Step 1: 安装依赖**

```bash
& ".venv\Scripts\python.exe" -m pip install fastapi uvicorn
```

- [ ] **Step 2: 更新 pyproject.toml** — 在 `dependencies` 列表（第 9-22 行）追加：

```toml
    "fastapi",
    "uvicorn",
```

- [ ] **Step 3: 创建包**

`src/gacore/weitrack/__init__.py`：
```python
"""weiTrackApp 数据接收存储服务（自用，局域网零鉴权）。"""
```

- [ ] **Step 4: 验证导入**

```bash
& ".venv\Scripts\python.exe" -c "import gacore.weitrack; print('ok')"
```
Expected: `ok`（PYTHONPATH 需含 `src`，或安装为包。若报 ModuleNotFoundError，用 `$env:PYTHONPATH="src"`）

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/gacore/weitrack/__init__.py
git commit -m "feat(weitrack): 新增接收端依赖与包骨架"
```

---

### Task 2: SQLite 存储层（含幂等去重）

**Files:**
- Create: `src/gacore/weitrack/storage.py`
- Test: `tests/test_weitrack_storage.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `class Storage(db_path: Path | str)` — 打开/初始化 SQLite
  - `Storage.register_batch(batch_id: str, device_id: str, received_at: int) -> bool` — 返回 True 表示首次（未见过该 batch），False 表示重复
  - `Storage.upsert_device(device_id: str, ts: int) -> None`
  - `Storage.insert_event(device_id: str, ts: int, type: str, payload: dict, received_at: int) -> None`
  - `Storage.event_count() -> int`（测试用）
  - `Storage.close() -> None`

- [ ] **Step 1: 写失败测试**

`tests/test_weitrack_storage.py`：
```python
from __future__ import annotations

from gacore.weitrack.storage import Storage


def _new_storage(tmp_path):
    return Storage(tmp_path / "wei_track.db")


def test_insert_event_and_count(tmp_path):
    s = _new_storage(tmp_path)
    s.upsert_device("dev1", 1000)
    s.insert_event("dev1", 1000, "usage", {"pkg": "com.x", "foreground_ms": 5}, 2000)
    assert s.event_count() == 1
    s.close()


def test_batch_idempotent(tmp_path):
    s = _new_storage(tmp_path)
    assert s.register_batch("b1", "dev1", 1000) is True
    assert s.register_batch("b1", "dev1", 1000) is False  # 重复
    s.close()


def test_duplicate_batch_not_double_insert(tmp_path):
    s = _new_storage(tmp_path)
    s.upsert_device("dev1", 1000)
    assert s.register_batch("b1", "dev1", 2000) is True
    s.insert_event("dev1", 1000, "usage", {"pkg": "com.x"}, 2000)
    assert s.register_batch("b1", "dev1", 2000) is False
    s.insert_event("dev1", 1000, "usage", {"pkg": "com.x"}, 2000)
    assert s.event_count() == 1
    s.close()
```

- [ ] **Step 2: 运行确认失败**

```bash
cd "D:\AAAmyPrj\github\myrepos\WithLangGraph"; $env:PYTHONPATH="src"; & ".venv\Scripts\python.exe" -m pytest tests/test_weitrack_storage.py -v
```
Expected: FAIL（ModuleNotFoundError / ImportError）

- [ ] **Step 3: 实现存储层**

`src/gacore/weitrack/storage.py`：
```python
"""SQLite 存储层：设备、幂等批次、事件。用标准库 sqlite3，不引 ORM。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY,
  first_seen INTEGER,
  last_seen  INTEGER
);
CREATE TABLE IF NOT EXISTS ingested_batches (
  batch_id    TEXT PRIMARY KEY,
  device_id   TEXT,
  received_at INTEGER
);
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id   TEXT NOT NULL,
  ts          INTEGER NOT NULL,
  type        TEXT NOT NULL,
  payload     TEXT NOT NULL,
  received_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_device_ts ON events(device_id, ts);
"""


class Storage:
    def __init__(self, db_path: Path | str) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def register_batch(self, batch_id: str, device_id: str, received_at: int) -> bool:
        """登记一个批次；返回 True=首次，False=已存在(幂等命中)。"""
        cur = self._conn.execute(
            "SELECT 1 FROM ingested_batches WHERE batch_id = ?", (batch_id,)
        )
        if cur.fetchone():
            return False
        self._conn.execute(
            "INSERT INTO ingested_batches(batch_id, device_id, received_at) VALUES (?,?,?)",
            (batch_id, device_id, received_at),
        )
        self._conn.commit()
        return True

    def upsert_device(self, device_id: str, ts: int) -> None:
        self._conn.execute(
            """
            INSERT INTO devices(device_id, first_seen, last_seen) VALUES (?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET last_seen=excluded.last_seen
            """,
            (device_id, ts, ts),
        )
        self._conn.commit()

    def insert_event(self, device_id: str, ts: int, type: str, payload: dict, received_at: int) -> None:
        self._conn.execute(
            "INSERT INTO events(device_id, ts, type, payload, received_at) VALUES (?,?,?,?,?)",
            (device_id, ts, type, json.dumps(payload, ensure_ascii=False), received_at),
        )
        self._conn.commit()

    def event_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: 运行确认通过**

```bash
$env:PYTHONPATH="src"; & ".venv\Scripts\python.exe" -m pytest tests/test_weitrack_storage.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/gacore/weitrack/storage.py tests/test_weitrack_storage.py
git commit -m "feat(weitrack): SQLite 存储层与幂等去重"
```

---

### Task 3: Pydantic schema 与校验

**Files:**
- Create: `src/gacore/weitrack/schemas.py`
- Test: `tests/test_weitrack_schemas.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `class UsageData(BaseModel)`：`pkg: str`、`foreground_ms: int`、`app: str | None = None`
  - `class SessionData(BaseModel)`：`kind: Literal["screen_on","screen_off","unlock","app_switch"]`
  - `class Event(BaseModel)`：`type: Literal["usage","session"]`、`ts: int`、`data: dict`
  - `class IngestRequest(BaseModel)`：`device_id: str`、`batch_id: str`、`client_ts: int`、`events: list[Event]`
  - `def validate_event_type(type: str, data: dict) -> None` — 对 usage/session 的 data 做最小校验（非必需，可留宽松）；`data` 存原文

- [ ] **Step 1: 写失败测试**

`tests/test_weitrack_schemas.py`：
```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from gacore.weitrack.schemas import IngestRequest


def test_valid_request():
    req = IngestRequest(
        device_id="dev1",
        batch_id="b1",
        client_ts=1000,
        events=[
            {"type": "usage", "ts": 1000, "data": {"pkg": "com.x", "foreground_ms": 5}},
            {"type": "session", "ts": 1001, "data": {"kind": "screen_on"}},
        ],
    )
    assert len(req.events) == 2


def test_invalid_type_rejected():
    with pytest.raises(ValidationError):
        IngestRequest(
            device_id="dev1", batch_id="b1", client_ts=1000,
            events=[{"type": "bogus", "ts": 1000, "data": {}}],
        )


def test_missing_ts_rejected():
    with pytest.raises(ValidationError):
        IngestRequest(
            device_id="dev1", batch_id="b1", client_ts=1000,
            events=[{"type": "usage", "data": {"pkg": "com.x"}}],
        )
```

- [ ] **Step 2: 运行确认失败**

```bash
$env:PYTHONPATH="src"; & ".venv\Scripts\python.exe" -m pytest tests/test_weitrack_schemas.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 schema**

`src/gacore/weitrack/schemas.py`：
```python
"""上报请求的 Pydantic 模型。只校验外层结构；data 内部存原文不深校验。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Event(BaseModel):
    type: Literal["usage", "session"]
    ts: int
    data: dict


class IngestRequest(BaseModel):
    device_id: str
    batch_id: str
    client_ts: int
    events: list[Event]
```

- [ ] **Step 4: 运行确认通过**

```bash
$env:PYTHONPATH="src"; & ".venv\Scripts\python.exe" -m pytest tests/test_weitrack_schemas.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/gacore/weitrack/schemas.py tests/test_weitrack_schemas.py
git commit -m "feat(weitrack): 上报请求 Pydantic schema"
```

---

### Task 4: FastAPI 应用与端点

**Files:**
- Create: `src/gacore/weitrack/server.py`
- Test: `tests/test_weitrack_server.py`

**Interfaces:**
- Consumes: `Storage`（Task 2）、`IngestRequest`（Task 3）
- Produces:
  - `def create_app(storage: Storage) -> FastAPI` — 工厂函数，注入 storage（便于测试）
  - 路由：`POST /ingest`、`GET /health`
  - `POST /ingest` 行为：新 device 自动注册；batch 幂等（重复返回 200 + `{"status":"ok","deduplicated":true}`）；非法返回 422；成功返回 `{"status":"ok","inserted":N,"deduplicated":false}`
  - `GET /health` 返回 `{"status":"ok"}`

- [ ] **Step 1: 写失败测试**

`tests/test_weitrack_server.py`：
```python
from __future__ import annotations

from fastapi.testclient import TestClient

from gacore.weitrack.server import create_app
from gacore.weitrack.storage import Storage


def _client(tmp_path):
    storage = Storage(tmp_path / "wei_track.db")
    app = create_app(storage)
    return TestClient(app), storage


def test_health(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/health").json() == {"status": "ok"}


def test_ingest_inserts(tmp_path):
    client, storage = _client(tmp_path)
    payload = {
        "device_id": "dev1", "batch_id": "b1", "client_ts": 1000,
        "events": [{"type": "usage", "ts": 1000, "data": {"pkg": "com.x", "foreground_ms": 5}}],
    }
    r = client.post("/ingest", json=payload)
    assert r.status_code == 200
    assert r.json()["inserted"] == 1
    assert storage.event_count() == 1


def test_ingest_idempotent(tmp_path):
    client, storage = _client(tmp_path)
    payload = {
        "device_id": "dev1", "batch_id": "b1", "client_ts": 1000,
        "events": [{"type": "usage", "ts": 1000, "data": {"pkg": "com.x"}}],
    }
    r1 = client.post("/ingest", json=payload)
    r2 = client.post("/ingest", json=payload)
    assert r2.status_code == 200
    assert r2.json()["deduplicated"] is True
    assert storage.event_count() == 1


def test_ingest_invalid_422(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/ingest", json={"device_id": "d", "batch_id": "b", "client_ts": 1, "events": [{"type": "nope", "ts": 1, "data": {}}]})
    assert r.status_code == 422
```

- [ ] **Step 2: 运行确认失败**

```bash
$env:PYTHONPATH="src"; & ".venv\Scripts\python.exe" -m pytest tests/test_weitrack_server.py -v
```
Expected: FAIL（create_app 不存在）

- [ ] **Step 3: 实现 server**

`src/gacore/weitrack/server.py`：
```python
"""FastAPI 应用：POST /ingest 接收上报，GET /health 健康检查。"""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException

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
```

（若需作为脚本运行，可加 `__main__` 启动 uvicorn，但核心交付是工厂函数；运行入口放 Task 5。）

- [ ] **Step 4: 运行确认通过**

```bash
$env:PYTHONPATH="src"; & ".venv\Scripts\python.exe" -m pytest tests/test_weitrack_server.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/gacore/weitrack/server.py tests/test_weitrack_server.py
git commit -m "feat(weitrack): FastAPI /ingest 与 /health 端点"
```

---

### Task 5: 运行入口 + JSONL 日志 + 全量测试

**Files:**
- Create: `src/gacore/weitrack/__main__.py`
- Modify: `src/gacore/weitrack/server.py`（若需挂日志中间件，可加；但保持简单，日志由入口层打）
- Test: 复用已有 `tests/test_weitrack_*.py`

**Interfaces:**
- Consumes: `create_app`、`Storage`
- Produces: `python -m gacore.weitrack` 启动服务（默认 0.0.0.0:8000）

- [ ] **Step 1: 写运行入口**

`src/gacore/weitrack/__main__.py`：
```python
"""python -m gacore.weitrack 启动接收服务。"""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from gacore.weitrack.server import create_app
from gacore.weitrack.storage import Storage


def main() -> None:
    parser = argparse.ArgumentParser(description="weiTrack ingest server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="wei_track.db", help="SQLite 文件路径")
    args = parser.parse_args()

    storage = Storage(Path(args.db))
    app = create_app(storage)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证入口可启动（短暂运行后关闭）**

```bash
cd "D:\AAAmyPrj\github\myrepos\WithLangGraph"; $env:PYTHONPATH="src"; $p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "-m","gacore.weitrack","--port","8011","--db",".\temp\weitrack_test.db" -PassThru -NoNewWindow; Start-Sleep -Seconds 6; & ".venv\Scripts\python.exe" -c "import httpx; r=httpx.get('http://127.0.0.1:8011/health'); print(r.status_code, r.json())"; Stop-Process -Id $p.Id -Force
```
Expected: `200 {'status': 'ok'}`

- [ ] **Step 3: 运行全量 weitrack 测试**

```bash
$env:PYTHONPATH="src"; & ".venv\Scripts\python.exe" -m pytest tests/test_weitrack_server.py tests/test_weitrack_schemas.py tests/test_weitrack_storage.py -q
```
Expected: 全部通过

- [ ] **Step 4: ruff 校验**

```bash
& ".venv\Scripts\python.exe" -m ruff check src/gacore/weitrack tests/test_weitrack_*.py
```
Expected: 无错误

- [ ] **Step 5: Commit**

```bash
git add src/gacore/weitrack/__main__.py
git commit -m "feat(weitrack): 服务运行入口与启动脚本"
```

---

### Task 6: 更新项目文档与完成

**Files:**
- Modify: `README.md`（在"运行"章节加接收服务启动方式）

- [ ] **Step 1: README 补充运行说明**

在 README 的"运行 (Run)"章节追加：
```markdown
### 接收服务（weiTrackApp 数据接收）

```powershell
$env:PYTHONPATH = "src"
python -m gacore.weitrack --host 0.0.0.0 --port 8000 --db wei_track.db
```

- `POST /ingest`：接收手机上报的事件（批量 JSON），幂等去重后落库。
- `GET /health`：健康检查。
- 手机连同一局域网 Wi-Fi，把上报地址指向本机 IP:8000。
```

- [ ] **Step 2: 全量回归**

```bash
$env:PYTHONPATH="src"; & ".venv\Scripts\python.exe" -m pytest tests -q
```
Expected: 原 278 + 新增用例全部通过

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(weitrack): README 补充接收服务运行说明"
```
