# weiTrackApp 数据采集上报系统 — 设计规格

- 日期：2026-08-16
- 状态：已批准（用户确认范围与架构）

## 背景与目标

weiCheckApp（Android，Kotlin/Compose，包名 `com.wei.checkapp`）原为屏幕使用时长 App。
本次升级定位为「用户手机行为数据采集器」：手机端持续采集使用行为数据，上报到自托管
服务器存储；日后由 WithLangGraph 基于这些数据生成用户画像（本次范围**不含**画像生成）。

**本次（第一阶段）目标**：打通「客户端采集上报 → 服务器接收存储」完整链路。
数据范围仅限 **usage（App 使用）+ session（亮屏/解锁/会话）**。notification、location
等数据源留待第二阶段扩 schema。

**自用前提**：合规 / 应用商店审核不在考虑范围内。数据全部上传到用户自有服务器。

## 架构

```
┌─────────────────────────────────────────────┐
│  手机 weiCheckApp (Android)                  │
│                                             │
│  CollectorService (前台服务, 常驻)            │
│   ├─ UsageRepository → UsageStatsManager     │
│   ├─ 亮屏/解锁/会话事件                        │
│   └─ 本地 SQLite 暂存 (断网也能攒, 不丢)        │
│             │                                │
│             │ HTTP POST /ingest (批量 JSON)   │
│             ▼                                │
└─────────────────────────────────────────────┘
             │ Wi-Fi 局域网
             ▼
┌─────────────────────────────────────────────┐
│  本机 FastAPI 服务 (WithLangGraph 项目内)      │
│   ├─ POST /ingest → 校验 + 幂等 + 落库         │
│   ├─ SQLite 存储 (wei_track.db)              │
│   └─ GET /health                             │
│             │                                │
│             ▼ (日后)                          │
│   WithLangGraph → 画像生成 (本次不实现)        │
└─────────────────────────────────────────────┘
```

## 数据模型（采集端 ↔ 服务器契约）

### 上报接口

**`POST /ingest`** — 批量上报事件。

请求体（JSON）：
```json
{
  "device_id": "f38f655a-<uuid>",
  "batch_id": "8f2c91e4-xxxx-xxxx",
  "client_ts": 1723700000000,
  "events": [
    {
      "type": "usage",
      "ts": 1723699800000,
      "data": {
        "pkg": "com.ss.android.ugc.aweme",
        "app": "抖音",
        "foreground_ms": 300000
      }
    },
    {
      "type": "session",
      "ts": 1723700000000,
      "data": { "kind": "screen_on" }
    }
  ]
}
```

- `device_id`：首次启动生成的 UUID，本地持久化，标识「哪台手机」。
- `batch_id`：客户端生成，服务器据此做**幂等去重**（网络重试不导致数据翻倍）。
- 事件带独立 `ts`（epoch 毫秒），服务器按时间落库。

### 字段约束

- `type` ∈ {`usage`, `session`}
- `session.data.kind` ∈ {`screen_on`, `screen_off`, `unlock`, `app_switch`}
- `usage.data` 必含 `pkg`、`foreground_ms`（该周期前台毫秒），`app` 为可读名（可空）
- 服务器**只校验外层**（`device_id`/`batch_id`/`type`/`ts`），`payload` 内部形状不校验，
  存原文——因为 schema 仍在演进，客户端可自由扩展 data 字段，服务器不设硬约束。

### SQLite Schema（wei_track.db）

```sql
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
  payload     TEXT NOT NULL,   -- 事件 data 的 JSON 原文
  received_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_device_ts ON events(device_id, ts);
```

设计要点：
- `payload` 存 **JSON 原文**，而非拆成固定列 —— schema 仍在演进，原文最灵活，
  日后 WithLangGraph 直接读 JSON 即可。
- `ingested_batches` 表做幂等去重：同一 `batch_id` 重复 POST 直接忽略。
- `events` 按 `(device_id, ts)` 建索引，画像查询按设备/时间扫。

## 组件设计

### 采集端（weiCheckApp，Android）

**核心：本地暂存 + 批量异步上报**（采集与上报解耦，断网不丢数据）。

1. **CollectorService**（前台服务，常驻通知）——唯一能稳定长期后台运行的方式：
   - 每 5 分钟读一次 UsageStats，计算增量，写入本地暂存库
   - 监听亮屏 / 解锁 / 前台 App 切换事件
   - **动态注册网络回调** `registerDefaultNetworkCallback`（不需要额外权限）：
     检测到恢复局域网连接 → 自动触发一次上报
2. **本地暂存库**（Room/SQLite）：所有事件先落本地，再异步上报。
3. **Uploader**（后台任务）：攒够一批（50 条或 5 分钟）POST 到 `/ingest`，
   成功后删除已传记录。

### 同步触发与状态展示（新增，用户确认）

**三种触发方式**（同一个上报入口 `syncNow()`，幂等、可重入）：
1. **周期自动**：攒够 50 条或 5 分钟自动上报（既有）
2. **手动立即同步**：UI 上「立即同步」按钮，点击立即触发一次 `syncNow()`
3. **连上局域网自动同步**：CollectorService 里 `registerDefaultNetworkCallback`
   检测到 Wi-Fi/局域网恢复 → 自动 `syncNow()`

**同步状态展示**（今日页或设置页显示给用户）：
- 状态：空闲 / 同步中 / 成功 / 失败（带上次错误信息）
- 上次同步时间
- 待上传条数（本地暂存库里还没传的）
- 服务器地址配置（用于连接本机 FastAPI）

**状态存储**：`SyncState` 数据模型（本地持久化）：
```
isSyncing: Boolean
lastSyncAt: Long?
lastSuccessAt: Long?
lastError: String?
pendingCount: Int   // 由暂存库实时计算
```
UI 通过 ViewModel 暴露 `StateFlow<SyncState>`，上传器更新它。

**同步状态模型要点**：UI 只读 `SyncState`，不做并发控制；上传器是唯一写入者，
用单例 + 互斥保证同一时刻只有一个上传任务在跑（手动点击期间若周期触发，直接跳过，
避免并发重复上报）。

### 服务器（WithLangGraph 项目内，Python）

新增 `src/gacore/weitrack/` 模块（遵循项目 src 布局、JSONL 日志、pytest + ruff 约定）：

- `server.py`：FastAPI 应用，`POST /ingest`、`GET /health`。
- `storage.py`：SQLite 读写封装（建表、幂等去重、插入事件、更新设备）。
- `schemas.py`：Pydantic 请求模型 + 字段校验。

依赖（需新增到 pyproject.toml）：`fastapi`、`uvicorn`、`pydantic`（项目已用 pydantic 相关
依赖但需显式声明）。SQLite 用 Python 标准库 `sqlite3`，不额外引 ORM，保持轻量。

日志遵循项目 JSONL 规范：`logs/YYYY-MM-DD/app.jsonl`，记录 ingest 请求、幂等命中、
落库条数；错误日志附 error_type / context。

## 错误处理

- 非法请求（缺字段 / 非法 type / 非法 batch 格式）→ `422`，不入库。
- 未知 device_id → 自动注册到 `devices` 表（first_seen）。
- 重复 batch_id → `200`（幂等），不重复插入。
- 服务器不在线 → 客户端本地暂存，网络恢复后补传（客户端侧行为）。

## 测试

- `tests/test_weitrack.py`（新增，遵循 pytest + asyncio_mode=auto 约定）：
  - `POST /ingest` 正常落库
  - 幂等：同 batch_id 重复提交不翻倍
  - 非法 payload → 422
  - 新 device 自动注册
- ruff 校验通过。

## 明确不做（本次范围外）

- notification / location 采集（第二阶段）
- 画像生成（日后用 WithLangGraph，不在本次）
- 云服务器部署（当前用本机 FastAPI）
- 鉴权（局域网自用，零鉴权；只做字段格式校验防脏数据）

## 验收标准

1. `POST /ingest` 收到手机上报的数据，落库到 `events` 表。
2. 同 batch 重传不导致数据翻倍。
3. 手机断网期间数据本地暂存，恢复后补传成功。
4. `GET /health` 返回 `{status: "ok"}`。
5. Android 端前台服务常驻，数据能持续积累。
6. **手动「立即同步」按钮**：点击后立即上报一次，UI 显示同步中 → 成功。
7. **连上局域网自动同步**：手机连上 Wi-Fi 后自动上报，无需点按钮。
8. **同步状态展示**：设置/今日页能看到同步状态、上次同步时间、待上传条数。
