---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 74fca65fe87f9b5d6900c56ec5512fbd_af20f100a1ca11f193c6525400f8a581
    ReservedCode1: rql6zwY3dwnCqhMmahCUF/xWuuLChvsn0bxA3nOwrYjWNIzB5fgd5eqlFmtwLr2A3Q5/L4pQm8lfmDdVBQKunQ443jAoppWB5VQOzKWQTGwIRZDuZORyDZWbyDAFKaSecQQ72Xj/qidS8pMVJK585EYgbqkGWv8PAdKMtXmQD0Xoub52EFeDPh5KxnE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 74fca65fe87f9b5d6900c56ec5512fbd_af20f100a1ca11f193c6525400f8a581
    ReservedCode2: rql6zwY3dwnCqhMmahCUF/xWuuLChvsn0bxA3nOwrYjWNIzB5fgd5eqlFmtwLr2A3Q5/L4pQm8lfmDdVBQKunQ443jAoppWB5VQOzKWQTGwIRZDuZORyDZWbyDAFKaSecQQ72Xj/qidS8pMVJK585EYgbqkGWv8PAdKMtXmQD0Xoub52EFeDPh5KxnE=
---

---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 74fca65fe87f9b5d6900c56ec5512fbd_1b684e9da1bd11f1a413525400287e28
    ReservedCode1: j9epkje1bF1gV8xwktcFG3dCK57A/mxz8dM3RZ9dMfzpW4CMJa7OJ0h1aQcRwdprDav6UUo4w4nLlJrg3fQrgrmChwvMXIOt8gLvh1dOZDL6OgWOBfu40CXDDNBjMuHlNtMpFwjx7aUfZdMv02rIENajiZiI6bZteXDvqvlrCfyYvnduYQ7a2iTMnYA=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 74fca65fe87f9b5d6900c56ec5512fbd_1b684e9da1bd11f1a413525400287e28
    ReservedCode2: j9epkje1bF1gV8xwktcFG3dCK57A/mxz8dM3RZ9dMfzpW4CMJa7OJ0h1aQcRwdprDav6UUo4w4nLlJrg3fQrgrmChwvMXIOt8gLvh1dOZDL6OgWOBfu40CXDDNBjMuHlNtMpFwjx7aUfZdMv02rIENajiZiI6bZteXDvqvlrCfyYvnduYQ7a2iTMnYA=
---

---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 74fca65fe87f9b5d6900c56ec5512fbd_2fc5b252a09211f1a65b525400826444
    ReservedCode1: Q8evqcX6LxLq3rS5BCQELYwxLf5grR6XuOWAf1jpzyAXK1YS0g0cXmA3XrYW3KBU02hyUFoCPs5aw0YhC/zUlJ3gLyLjbrDXdClhzIoU+jkp0i3IkvNPIiUQjdmZwq8MS6Z/HeCMaHt28OZE++DIW3NSNCPZocwZYSoHTIsCMSfSFgyh0cbcEs6egPk=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 74fca65fe87f9b5d6900c56ec5512fbd_2fc5b252a09211f1a65b525400826444
    ReservedCode2: Q8evqcX6LxLq3rS5BCQELYwxLf5grR6XuOWAf1jpzyAXK1YS0g0cXmA3XrYW3KBU02hyUFoCPs5aw0YhC/zUlJ3gLyLjbrDXdClhzIoU+jkp0i3IkvNPIiUQjdmZwq8MS6Z/HeCMaHt28OZE++DIW3NSNCPZocwZYSoHTIsCMSfSFgyh0cbcEs6egPk=
---

# langTrack 技术实现参考（接口 · 实体 · 数据库 · 数据流）

> 本文是 langTrack 服务端的**代码级技术参考**，所有字段/接口/阈值均对照源码（标注 `文件:行号`）。
> 配套文档：数据链路总览与工作日志见 [`langTrack-roadmap.md`](langTrack-roadmap.md)；ETL 清洗规则细节见 [`etl-cleaning-guide.md`](etl-cleaning-guide.md)。
> 生成日期：2026-08-22，基于 ETL_VERSION `1.0.0`（`etl.py:159`）。

---

## 1. 端到端数据流总览

```mermaid
flowchart LR
    subgraph 采集端["采集层（weiCheckApp 客户端仓库）"]
        A["Android 采集器<br/>usage/notification/location/audio/..."]
    end

    subgraph 接收层["接收层 server.py"]
        B["POST /ingest<br/>幂等去重 + 单事务落库"]
        H["GET /health / GET /dashboard"]
    end

    subgraph 原始层["原始层 storage.py"]
        C[("events 原始事件<br/>devices / ingested_batches")]
    end

    subgraph 加工层["加工层 etl.py（全量/增量重建）"]
        D["事实表 12 张<br/>sessions/daily_stats/stays/trips/places/<br/>anomalies/route_grids/grid_pois/<br/>contract_coverage/etl_runs/dirty_events/etl_state"]
    end

    subgraph 出口层["出口层"]
        F["report.py 日报 CLI<br/>+ L5 画像快照"]
        G["dashboard.py 深色单页"]
        T["langTrack_stats 工具<br/>→ agent 日报自我观察"]
        X["geocode.py / routes.py<br/>高德 regeo·路径规划·POI"]
    end

    A -->|批量 JSON 上报| B --> C
    C -->|"① server 周期线程(默认30min)<br/>② CLI python -m ...etl<br/>③ 工具调用前 _ensure_etl"| D
    D --> F & G & T
    D <-->|增量外呼/回写语义列| X
```

**分层职责一句话**：客户端只采集上报；服务端 `/ingest` 只做"校验+幂等落库"；`etl.py` 把原始 events 加工成事实表；出口层全部**只读事实表**，不反向写采集数据。

---

## 2. HTTP 接口（`server.py:76-107`）

| 方法 | 路径 | 参数 | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` | 存活探针 |
| POST | `/ingest` | body=`IngestRequest` | `{"status":"ok","inserted":N,"deduplicated":bool}` | 幂等：`batch_id` 重复时返回 `{"status":"ok","inserted":0,"deduplicated":true}` |
| GET | `/dashboard` | `?day=YYYY-MM-DD`（可选） | `text/html` | 深色单页仪表盘，调 `render_dashboard_html(conn, day)`（`dashboard.py:237`），每次请求新开只读连接 |

### 2.1 幂等与事务语义（`storage.ingest_batch`, storage.py:89-115）

```mermaid
sequenceDiagram
    participant C as 手机端
    participant S as POST /ingest
    participant D as SQLite
    C->>S: IngestRequest{device_id, batch_id, client_ts, events[]}
    S->>D: upsert_device（ON CONFLICT 更新 last_seen）
    S->>D: INSERT OR IGNORE ingested_batches(batch_id)
    alt rowcount==0（batch_id 已存在）
        D-->>S: 幂等命中，事件不插入
        S-->>C: inserted=0, deduplicated=true
    else 首次见到该 batch
        S->>D: 循环 INSERT events(...) —— 同一事务
        Note over D: 任一步失败 → 整体回滚，<br/>杜绝「批次已登记但事件半插入」窗口<br/>（否则重试会因幂等命中而永久丢批）
        S-->>C: inserted=len(events), deduplicated=false
    end
```

关键点：
- `type` **不做枚举限制**（`schemas.py:1-3`）：客户端持续扩展新类型，服务端按 JSON 原文落库，分析层自行区分——契约符合性由 A① 的 `contract_coverage` 表事后校验，而非入库拦截。
- 后台周期 ETL：FastAPI lifespan 起 daemon 线程（`server.py:61-73`），间隔 `LANGTRACK_ETL_INTERVAL_SECONDS`（默认 1800s）、单次超时 `LANGTRACK_ETL_TIMEOUT_SECONDS`（默认 120s）；以 `subprocess` 方式跑 `python -m gacore.langTrack.etl`，失败仅记日志**不阻塞接收**。

### 2.2 启动方式

```powershell
$env:PYTHONPATH = "src"
python -m gacore.langTrack --host 0.0.0.0 --port 8000 --db langTrack.db
```

---

## 3. 请求实体（`schemas.py`）

```python
class Event(BaseModel):
    type: str      # 自由字符串，见 §2.1 关键点
    ts: int        # 毫秒时间戳（客户端时钟）
    data: dict     # 事件原文，服务端不深校验

class IngestRequest(BaseModel):
    device_id: str
    batch_id: str   # 客户端生成的批次号 = 幂等键
    client_ts: int
    events: list[Event]
```

上报示例：

```json
{
  "device_id": "c2b6198c-e879-4bad-aee7-98d227dc9852",
  "batch_id": "b-20260822-001",
  "client_ts": 1755800000000,
  "events": [
    {"type": "session", "ts": 1755800001000,
     "data": {"screen_on": 1, "unlock": 0, "switch": 2}}
  ]
}
```

---

## 4. 数据库表字典

SQLite 单库 `data/langTrack.db`（gitignore）。分两层：**原始层**只追加不改写；**事实层**随 ETL 可重建（除人工标注外）。

### 4.1 原始层（`storage.py _SCHEMA`, L8-34）

#### devices — 设备登记

| 字段 | 类型 | 用途 |
|---|---|---|
| device_id | TEXT PK | 设备唯一标识 |
| first_seen / last_seen | INTEGER | 首次/最近上报毫秒时间戳；`upsert_device` ON CONFLICT 刷新 last_seen |

#### ingested_batches — 批次账本（幂等的根基）

| 字段 | 类型 | 用途 |
|---|---|---|
| batch_id | TEXT PK | 客户端批次号；重复即判重 |
| device_id / received_at | TEXT / INTEGER | 归属设备 / 服务端收到时刻(ms) |

#### events — 原始事件（唯一事实来源，ETL 的输入）

| 字段 | 类型 | 用途 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | 行号 |
| device_id / ts / type | TEXT / INTEGER / TEXT NOT NULL | 设备 / 毫秒时间戳 / 事件类型（自由串） |
| **payload** | TEXT NOT NULL | **JSON 原文字符串**。注意列名是 `payload` 不是 `data`（历史踩坑 #2，写 SQL 前先 PRAGMA 确认） |
| received_at | INTEGER | 服务端接收时刻(ms)，区别于客户端 `ts` |

索引：`idx_events_device_ts(device_id, ts)`。

旧库迁移：`_add_timestamp_columns`（storage.py:37-59）对三张表补 `created_at/updated_at` 并用各自业务时间列回填东八区可读时间。

### 4.2 事实层（`etl.py _SCHEMA`, L163-516）

公共约定：事实表带 `etl_version` + `created_at`/`updated_at`（东八区），由 B8 `_stamp_fact_tables`（etl.py:1136）统一打标，其自然时间列映射：

| 表 | 自然时间列 | 表 | 自然时间列 |
|---|---|---|---|
| sessions | start_ms | places | first_seen |
| stays / trips | start_ts | anomalies | ts |
| daily_stats / route_grids | day（非毫秒） | grid_pois | queried_at |

#### sessions — 前台 App 会话（B 重建核心表）

| 字段 | 用途 |
|---|---|
| device_id, day | 设备 + 东八区归属日 |
| pkg, app, activity | 包名 / 显示名 / Activity |
| start_ms, end_ms, duration_ms | 会话起止与时长 |

索引 `idx_sessions_day/pkg`；构建函数 `build_sessions(events)`。

#### daily_stats — 天粒度聚合（画像主输入）

PK `(device_id, day)`（B2 修正；`device_id DEFAULT 'unknown'` 兼容旧库行）。

| 字段 | 用途 |
|---|---|
| total_screen_ms | 当日屏幕总时长（persona 屏幕健康度、report 屏幕节的来源） |
| app_ranking_json | App 时长排行 JSON 数组 `[{"app","ms"},...]`（persona 分类聚合逐条消费） |
| notification_count / notification_clicked | 通知数 / 点击数 |
| top_notification_apps_json | 通知 Top 应用 |
| screen_on/off_count, unlock_count, switch_count | 亮灭屏 / 解锁 / 切换次数 |
| location_count, audio_clip_count | 定位点数 / 音频片段数 |

#### stays — L1 停驻点

`build_stays(events)` 产出；索引 day/device。

| 字段 | 用途 |
|---|---|
| start_ts, end_ts, duration_ms | 停驻起止 |
| center_lat/lon, min/max_lat/lon | 中心与包围盒 |
| n_points, radius_m | 参与点数 / 弥散半径 |
| grid_key | 网格键 = 与 places 关联的外键（逻辑外键） |
| day | 东八区归属日（`_TZ_CST` 显式时区归日） |

#### trips — L3 移动段（相邻停驻点之间的 gap）

`routes.build_trips(events, stays)`（routes.py:64-130）：双阈值过滤 `duration ≥ TRIP_MIN_DURATION_MS(60s)` 且 haversine 距离 `≥ TRIP_MIN_DIST_M(300m)`；无采样点用前后停驻中心兜底。

| 字段 | 用途 |
|---|---|
| 起止/距离组 | start/end_ts, duration_ms, start/end_lat/lon, dist_m, n_points, day |
| polyline, route_key, route_mode, route_encoded_at | 高德路径规划结果缓存四件套；ETL 全量重建时按 `UNIQUE(device_id,start_ts,end_ts)` 带回旧值，**避免重复烧配额**（etl.py:1285-1309） |

#### places — 常驻点（唯一"半人工"状态的事实表）

UPSERT 保标签：`ON CONFLICT(device_id, grid_key)` 只更新统计并保留 label（etl.py:1312-1326）；人工确认的「家/公司」持久化在 `data/place_labels.json`，每次 ETL 末尾 `apply_labels` 恢复（踩坑 #4 的解法）。

| 字段 | 用途 |
|---|---|
| grid_key, lat/lon, first_seen, last_seen, visit_count | 网格身份与到访统计 |
| label | 家 / 公司 / 未知（用户确认值，ETL 不覆盖） |
| is_primary | 自动标记 top2 主常驻点 |
| address, poi, district, township, business_area, poi_type | L2 regeo 语义回填 |
| poi_l1/l2/l3, poi_signal, poi_fallback, matched_level | P1-2 POI 三级语义（大类;中类;细类）+ 名称硬信号 + 无 POI 兜底描述 |
| behavior | 行为语义 |
| candidate_label, confidence_home/work | L1 家/公司**置信度候选**（未确认前不污染 label） |
| geocoded_at | 最近一次 regeo 编码时刻（增量编码依据） |

#### contract_coverage — A① 契约覆盖校验

`build_contract_coverage(conn)`（etl.py:980-1036）全量重建：`SELECT type,COUNT(*),MAX(ts) FROM events GROUP BY type` 对照 `contract.EXPECTED_EVENT_TYPES`。

| 字段 | 用途 |
|---|---|
| type | PK，事件类型 |
| expected / consumed / desc | 是否期望(1) / ETL 消费度(true·partial·false，仅展示) / 中文说明 |
| arrived / event_count / last_seen_ts | 是否到达 / 到达次数 / 最近到达时刻 |
| status | `ok`(7 天内到达) / `stale`(超 STALE_DAYS=7 天) / `missing`(契约有实际无) / `unexpected`(实际有契约无) |

#### etl_state — B1 增量水位线

`device_id PK, last_event_ts, last_run_at`；成功重建后写入 per-device 最大事件 ts（etl.py:1187-1201）；`--incremental` 时以此为锚回看 3 天确定受影响日集合。

#### etl_runs — B5 运行血缘

每次运行一行：`version/mode(full|incremental)/device_id/affected_days/status/git_rev/rows_daily/sessions/stays`。

#### dirty_events — B4 脏事件隔离区

schema 校验不过的事件落此处（type/raw/reason），**不进 events 主流程、不崩 ETL**。

#### anomalies — P1-3 异常叙事

打破规律的新地点/事件（`UNIQUE(day,kind,grid_key)`），供日报叙事；路线变化事件复用本表（kind 区分）。

#### route_grids / grid_pois — 通勤带与沿途 POI

`route_grids` PK`(device_id,day,grid_lat,grid_lon)`：trips.polyline 网格量化后的高频经过网格（纯本地零配额）；`grid_pois` PK`(grid_lat,grid_lon)`：每网格一条周边 POI 缓存（高德 around 约 100 次/日，低频克制）。

### 4.3 表关系

```mermaid
erDiagram
    devices ||--o{ events : "device_id"
    devices ||--o{ daily_stats : "device_id"
    devices ||--o{ sessions : "device_id"
    devices ||--o{ stays : "device_id"
    devices ||--o{ trips : "device_id"
    devices ||--o{ places : "device_id"
    places ||--o{ stays : "grid_key(逻辑外键)"
    trips }o--|| places : "起点/终点邻近网格"
    events ||--o{ contract_coverage : "type 对照契约"
    etl_state ||..|| devices : "per-device 水位线"
```

---

## 5. 出口实体（消费侧契约）

### 5.1 LangTrackDayStats TypedDict（`tools/langTrack_tools.py:112-144`）

agent 工具 `langTrack_stats(day)` 的返回结构（gacore 主 agent 日报自我观察的数据源）：

| 字段 | 类型 | 来源与用途 |
|---|---|---|
| day | str | 查询日（默认今天） |
| available | bool | 当日 daily_stats 无行 → False |
| screen_ms / screen_hours | int/float | total_screen_ms 及小时换算 |
| top_apps | list[dict] | app_ranking_json 前 8 |
| notification_count / clicked | int | 当日通知量 |
| unlock_count / switch_count / location_count | int | 解锁/切换/定位 |
| places | list[dict] | places 按 visit_count 前 4 `{label,visits}` |
| sleep_signal | str | 凌晨 00-05 点 audio_env 样本 >5 → "疑似熬夜"，否则"未见熬夜信号"（tools:218-232） |
| coverage | list[dict] | contract_coverage 中 status≠ok 的行（缺失/停滞/未登记），旧库无此表时降级为空（tools:234-253） |
| persona | dict | §5.2 七日画像（tools:283） |

调用前置：`_ensure_etl()` 先跑一遍 ETL 保证读到最新（tools:80-106，失败不阻塞）。

### 5.2 persona.build() 返回结构（`persona.py:147-551`）

纯读聚合，不动 ETL 不加表（C1 外挂式）；`conn`/`db_path` 二选一，`device_id=None` 时全量读（旧库无 device_id 列自动退化）：

```python
{
  "device_id": str|None, "days": 7, "available": bool,
  "category_usage": [ {"category","ms","hours","pct"} ],  # 按 ms 降序
  "uncategorized":  [app 名...],          # 提示需补 app_categories.json
  "screen_health": {"avg_total_ms","avg_hours","trend":"up|down|flat",
                    "heavy_user":bool,"heavy_days":int,"note"},
  "rhythm":       {"segments":{时段:ms}, "night_pct":float,
                   "night_owl":bool, "peak_segment":str|None},
  "routine":      {"regular":bool,"work_start":"HH:MM|None",
                   "commute_stable":bool,"home_days","work_days","note"},
  "traits":       [特征短语...], "card": "一句话画像。",
}
```

判定阈值（persona.py:69-77 等）：

| 维度 | 规则 |
|---|---|
| 重度屏幕 | 单日 >5h（`_DEFAULT_HEAVY_MS`），且窗口内 ≥60% 天数（`_DEFAULT_HEAVY_FRAC`）→ heavy_user |
| 屏幕趋势 | 今日 vs 其余天均值偏差 >±10% → up/down，否则 flat |
| 夜猫子 | 深夜+凌晨(23:00-05:00)会话占比 ≥25% |
| 作息规律 | 家停驻 ≥3 天 且 公司停驻 ≥3 天 |
| 通勤稳定 | 窗口内 trips ≥3 段 |
| 时段分桶 | `_SEGMENTS`：凌晨0-5/上午5-11/午后11-14/下午14-18/晚上18-23/深夜23-24（东八区，与 report 一致） |

分类映射：`data/app_categories.json`（gitignore，显示名→大类）覆盖代码内置 `_DEFAULT_CATEGORIES` 兜底（persona.py:87-101），未登录 app 归"其他"并进 `uncategorized`。

### 5.3 采集契约 EXPECTED_EVENT_TYPES（`contract.py:11-33`）

18 个期望类型 × consumed（`true`=ETL 消费 / `partial`=部分 / `false`=仅采集）：

usage、session、notification、location、audio_env、audio_clip、accel(false)、snapshot(partial)、screen_content、clipboard、input、media、bt_device、battery、network、app_lifecycle、call、sms。`STALE_DAYS=7`。

契约由人维护：客户端新增/废弃类型时显式更新此文件，ETL 校验产出 unexpected/missing。

---

## 6. ETL 流程详解（`etl.run`, etl.py:1204-1409）

```mermaid
flowchart TD
    M0["迁移: _migrate_fact_tables / migrate_places / places.is_primary"] --> A1["merge_device_aliases<br/>同设备重装别名归一(events改写/places合并/devices吸收)"]
    A1 --> A2["load_events 清洗<br/>clean_ts: ts>1e12 过滤脏时间戳"]
    A2 --> B{"--incremental ?"}
    B -->|是且有水位线| B1["读 etl_state 水位线<br/>回看3天得 affected_days<br/>仅 DELETE/重建这些 day"]
    B -->|否| B2["DELETE 全表后重建"]
    B1 & B2 --> C1["build_sessions → sessions<br/>build_daily_stats → daily_stats"]
    C1 --> C2["build_stays → stays (L1)"]
    C2 --> C3["build_trips → trips (L3)<br/>旧 polyline 四件套按 UNIQUE 键带回"]
    C3 --> D1["build_places → UPSERT places<br/>保留label + infer_home_work_candidates<br/>top2 is_primary + apply_labels(place_labels.json)"]
    D1 --> D2["detect_anomalies → anomalies"]
    D2 --> E1["geocode.incremental_encode<br/>仅未编码常驻点 regeo(省配额)"]
    E1 --> E2["routes.incremental_encode_trips<br/>仅未编码移动段补路, 0.5s/段防CUQPS"]
    E2 --> E3["detect_route_changes → anomalies(kind区分)"]
    E3 --> F1["routes.build_route_grids 通勤带(零配额)"]
    F1 --> F2["encode_belt_pois沿途POI<br/>单次上限 _POI_MAX_PER_RUN"]
    F2 --> G1["build_contract_coverage → contract_coverage"]
    G1 --> G2["_stamp_fact_tables(B8 打标)<br/>+_update_etl_state(写水位线)"]
```

要点：
- **失败隔离**：geocode/补路/POI 任一外呼失败只打日志跳过，不影响事实表重建（各步 try/except SystemExit/Exception）。
- **places 是唯一不完全重建的表**：UPSERT 累计统计 + label 优先级「已确认 > 别名 > 未知」。
- CLI：`python -m gacore.langTrack.etl [--db PATH] [--purge] [--no-geocode] [--no-route] [--no-poi] [--incremental]`（etl.py:1412-1423）；`--purge` 先清理异常事件再重建。
- 三种触发：server 周期线程（§2.1）/ 手动 CLI / `langTrack_stats` 调用前 `_ensure_etl()`。

---

## 7. 外部依赖与配置

| 项 | 位置 | 说明 |
|---|---|---|
| 高德 Key | `.env`（字节查找读取，容错混合编码，踩坑 #3） | regeo / 路径规划(walking 默认，`LANGTRACK_ROUTE_MODE` 可切 driving) / around POI |
| `LANGTRACK_ETL_INTERVAL_SECONDS` | env | 周期 ETL 间隔，默认 1800s |
| `LANGTRACK_ETL_TIMEOUT_SECONDS` | env | 单次 ETL 子进程超时，默认 120s |
| `data/place_labels.json` | 文件 | 人工确认的家/公司标签持久化（ETL 重跑恢复） |
| `data/app_categories.json` | 文件（gitignore） | App 分类映射；缺失时代码内置默认兜底 |
| `data/profiles/langTrack_profile_<day>.json` | 文件 | report L5 画像快照（含 coverage/persona） |

---

## 8. 测试与常用命令

```powershell
# langTrack 全部测试（py12 环境）
$env:PYTHONPATH="src"
& "D:\softwares\miniconda\envs\py12\python.exe" -m pytest tests/ -k langTrack -q

# ETL / 报告 / 地理编码 / 标签确认
python -m gacore.langTrack.etl --purge
python -m gacore.langTrack.report --day 2026-08-22
python -m gacore.langTrack.geocode
python -m gacore.langTrack.label_places
```

测试覆盖锚点：storage 幂等事务（test_langTrack_storage）、server 端点（test_langTrack_server）、契约覆盖（test_langTrack_contract，3 例）、persona 五维+降级（test_langTrack_persona，6 例）、工具出口（test_tools_langTrack）。


---

## 9. 人物卡切换（/角色）

> 前端人设层：**工具能力与人设解耦**。工具可用性由运行时装配（graph/model 绑定）决定，角色激活只叠加人格文本，不削减工具准则。

### 9.1 资产与接口（`src/gacore/character.py`）

| 项 | 位置 | 说明 |
|---|---|---|
| 物料目录 | `config/assets/characters/<id>.md`（`character.py:card_dir`） | 卡 id=文件名 stem；显示名=`# ` 首标题；prompt=`# ` 标题后正文 |
| `list_cards(cfg)` | `character.py` | 扫描目录返回 `Card(id,name,path)` 列表，按 id 排序 |
| `card_prompt(cfg, id)` | `character.py` | 返回人格文本；缺失/不可读返回 `None`，调用方回退默认助手不崩溃 |
| `card_name(cfg, id)` | `character.py` | 显示名查询，无此卡返回 `None` |

### 9.2 装配与持久化

```mermaid
flowchart LR
    A["config/assets/characters/*.md<br/>纯数据资产"] --> B["character.py<br/>扫描+读取(只读)"]
    B --> C["state.py active_card<br/>会话内指定"]
    C --> D["context.py 唯一注入点<br/>L0工具准则 + 人物卡 + 工具桥接句"]
    E["frontends/qq.py /角色<br/>查看/切换/off"] -->|持久化| F["data/active_cards.json<br/>user->card 映射,重启不丢"]
```

- 角色激活时 system prompt = **L0 工具准则 + 人物卡 + 工具桥接句**（“保留系统智能体全部能力，可调用系统工具，用角色口吻表达”），实例见 `context.py`。
- 切换角色即新开一段对话（复用 `/new` 清理逻辑），历史互不串戏（`qq.py:1107`）。
- 持久化：`qq.py:_card_state_file()/_load_user_cards()/_save_user_cards()`，文件 `data/active_cards.json`，损坏降级为 `{}`。

### 9.3 测试

`tests/test_character.py`(7)、`tests/test_character_system_prompt.py`(4)、`tests/test_qq.py` `/角色` 相关 3 条；回归 69 passed。
### 9.4 主动推送（openid 落库 + qq_push）

- 落库：`frontends/qq.py:_record_known_user()` —— 每次收到消息把用户 openid 写入 `data/qq_known_users.json`（`first_seen`/`last_seen`，损坏自动降级重建，失败仅告警不阻塞消息处理）。
- 推送：`gacore/langTrack/qq_push.py`（独立进程，`python -m gacore.langTrack.qq_push`，需 `PYTHONPATH=src`），读取 `data/qq_known_users.json` 后经 botpy `post_c2c_message` 主动推送。支持 `--to <openid>` 指定用户、`--show` 列出已知用户、`--sandbox` 切换沙箱域名。
- **实现要点（实测踩坑）**：推送必须用 botpy 的 `BotHttp + BotAPI` 纯 REST（`http.login(Token(appid, secret))` → `api.post_c2c_message`），**不要用 `Client`**——`Client.start()` 会永久阻塞在 websocket 会话循环（`_pool_init` 的 `while not self._closed: await coroutine`），且 `Client.close()` 不关 ws，一键推送进程会假死、消息根本发不出去。修复后脚本数秒内干净退出（2026-08-22 实测）。
- 实测验证（2026-08-22）：平台返回真实 message id（`ROBOT1.0_…`），船长 QQ 两条主动推送均送达。
- **agent 工具**：`gacore/tools/qq_tools.py:qq_push`（`@tool`，schema=message/to）——复用 `langTrack/qq_push.py:send_c2c`（CLI 与工具共用同一发送实现）；botpy 异步调用经模块级单 worker 线程桥（`asyncio.run` in thread，90s 超时），QQ 前端 asyncio 环境下安全调用；`to` 缺省=全部已知用户（主人）；返回 TypedDict（`sent{ok,to,failures,ids}` / `error`），不抛异常。注册于 `tools/__init__.py`（TOOL_NAMES/_TOOLS，工具总数 26）；测试 `tests/test_tools_qq_push.py` 8 条（2026-08-22 真实发送冒烟通过）。
- 前置条件与策略：botpy 沙箱模式主动推送要求接收方 openid 已在 QQ 开放平台沙箱名单；**2025-04 起官方公告不再支持「主动消息推送」**（能力收敛），实测仍送达但属灰色地带；最稳路径是用户消息后 5 分钟内的被动回复（带 `msg_id`）。


### 9.5 QQ 对话上下文持久化（AsyncSqliteSaver，2026-08-24）

- **存储载体**：`data/gacore_chat.db`（SQLite）。build_config 以 `AsyncSqliteSaver.from_conn_string()` 注入 graph 作为 checkpointer，替代原 MemorySaver 内存态——**重启不丢对话**。saver 生命周期挂在 helper（`__aenter__`/`__aexit__`）上全程复用，切勿每次 invoke 重建。
- **线程键**：`qq.py:_thread_for(user, group)` → `f"qq-{group}:{user}"`，稳定映射保证跨重启命中同一 thread。
- **删除语义（关键踩坑）**：`/new`、`/reboot`、`/reset` 清理上下文走异步接口 `await checkpointer.adelete_thread(thread_id)`；主线程直接调同步 `delete_thread` 会抛 `InvalidStateError`。get_state 读取同理必须 `await`（含 `update_*_overview` 场景）。
- **用户会话目录**：`_user_threads` 由 dict 升级为落盘持久化——`data/qq_user_threads.json`，启动读入、变更即存、损坏降级 `{}`（对齐 active_cards.json 同款读写模式）。
- **入口改造**：qq.py `main` 改为 `async _boot` 经 `asyncio.run` 启动；`start.py` 对 `build_config()` 加 `await`。
- **前提条件**：需 `langgraph-checkpoint-sqlite`（pyproject 已声明）；QQ 前端运行还依赖 `qq-botpy`。
- **与角色卡互不干扰**：对话 checkpointer（gacore_chat.db）与人物卡映射（data/active_cards.json）独立存储；切卡/清上下文仍走 `/new` 等既有逻辑。

### 9.6 QQ 跨天记忆自动翻篇（onboard pack + _maybe_rollover，2026-08-24）

> 目标：对话上下文不再无限累积 + 跨天记忆延续。设计文档见 `output/qq-crossday-rollover-design.md`。

**核心思想**：把记忆导出（23:50 daily-report 成功后）与消费（次日首条消息）解耦，通过 `data/onboard_pack.json` 单文件交接。消费端只在真实跨天时一次性把「昨日日报 + 长期画像」注入首轮 system prompt，旧 checkpoint 完整保留可回溯。

**数据流时序**：

```mermaid
sequenceDiagram
    participant S as scheduler.run_job (23:50)
    participant P as data/onboard_pack.json
    participant Q as qq.on_message -> _maybe_rollover
    participant G as graph process (首轮)
    S->>S: daily-report job 成功后执行 _export_onboard_pack
    S->>P: 写入(date/created_at/source_job/prev_thread_id/payload)
    Note over Q: 次日用户首条消息
    Q->>P: 读 pack
    Q->>Q: pack.date<today && 仍是旧 thread → 新 thread_id + 更新 user_threads.json
    Q->>P: unlink(pack, missing_ok=True)
    Q->>G: state["rollover_context"]=昨日日报摘要+长期画像
    G->>G: build_system_prompt 注入 → cleanup_images 清除 rollover_context
```

**关键位置**：

| 职责 | 位置 | 说明 |
|---|---|---|
| 配置策略 | `config.py:RolloverConfig` / `Config.rollover` | enabled / inject_long_term_full / keep_old_thread / recent_days(默认3)；`from_env` 读 `GACORE_ROLLOVER_*` |
| 记忆包导出 | `scheduler.py:_export_onboard_pack`（+`_long_term_insight/_summarize_long_term/_load_active_qq_thread/_onboard_pack_path`） | 复用 `daily_notes.load_recent_daily_summaries(cfg, days)`；画像从 `memory/global_mem_insight.txt`（兜底 `global_mem*.txt`）；同名覆盖天然幂等 |
| 尾随触发 | `scheduler.py:run_job` | `if error is None and "daily" in job.name.lower(): try: _export_onboard_pack(cfg) except ...只记日志`——**不新增独立 job**，失败不阻塞日报 |
| 翻篇消费 | `qq.py:_maybe_rollover(user_id)`（`on_message` 入口 await） | 保旧 checkpoint 不 delete；`qq-{user_id}-{uuid8}` 新 thread；更新 `_user_threads`+落盘；stage 到 `self._pending_rollover[user_id]`；清 pack |
| 首轮注入 | `qq.py:_run_agent` | `self._pending_rollover.pop(user_id)` → `state["rollover_context"]` |
| system prompt 注入 | `context.py:build_system_prompt` | `=== 昨日记忆注入 ===` 节，仅 `rollover_context` 非空时追加 |
| 一次性语义 | `graph.py:cleanup_images` | 每轮 END 前返回 `rollover_context: None`，保证注入只在首轮出现 |
| state channel | `state.py:GAState.rollover_context` | 声明为 `str | None`，随 checkpoint 持久化 |

**幂等/防重**：pack `date >= today` 不动（同一天不重复翻篇）；`prev_thread_id` 与当前 thread 不一致（已被 `/new` 或已翻篇）→ 只 unlink pack 不翻篇；翻篇后 unlink `missing_ok=True`。

**兜底铁律**：`_maybe_rollover` 整体 try/except，任一失败仅记 `rollover skipped (safe fallback to normal chat)`，聊天永不阻塞。注入对用户完全静默，仅日志留痕（`cross-day rollover executed` 含 old/new thread、pack_date、injected_chars）。

**已知边界**：翻篇发生在 asyncio 主循环 on_message 入口；若首条是纯图片/命令消息，stage 可能在首个真实文本轮才被消费（`_run_agent`）；`rollover_context` 在 wait_for_text 中断后 resume 时仍在 state（cleanup_images 尚未跑），会在 resume 轮注入后清除——行为可接受。

## 9.7 QQ 去人机味改造：入口分级闸门 + 真问题多方案输出

**目标**：随口话不再走完整多轮 graph（省成本、去人机味），真决策问题给出多方案结构化对比并按条发送。设计文档 `output/qq-chat-multi-answer-research-20260824.md`（方案 A + B）。

**核心思想**：消息入口按「随口话 / 真问题」分流。随口话（极短或白名单词、且无意图词）→ 独立轻量 LLM 直接生成 1~2 句即兴回应（不建 agent、不调工具、不跑 graph、不写线程）；真问题 → 正常 agent 回路，决策类问题另注入多方案输出指令，发送端按【方案N】锚点拆多条消息。所有判定 fail-open：边界情形一律放行进完整回路，绝不误杀正经问题。

**Phase 1 分流时序**：

```mermaid
flowchart LR
    M[on_message 文本] --> G{trivial_detect}
    G -- 命中(≤8字或白名单词, 无意图词) --> T[_trivial_reply]
    G -- 未命中 --> R[_run_agent 正常回路]
    T --> L[get_llm 无工具 bind temp=1.0 max_tokens=60]
    L --> P[prompt: 时间/饭点/今日画像/口吻/人物卡]
    P --> S[发送 1~2 句]
```

**关键位置**：

| 职责 | 位置 | 说明 |
|---|---|---|
| 随口话判定 | `qq.py:trivial_detect` | ≤`_TRIVIAL_MAX_LEN`(8) 字或命中 `_TRIVIAL_WHITELIST`，且不含 `_INTENT_WORDS`（如何/怎么/为什么/帮我/推荐/方案/对比/能否…）→ True；`any(w in t for w in _INTENT_WORDS)` 优先短路放行 |
| 轻回应入口 | `qq.py:on_message` 第 4 步前 | `asyncio.create_task(self._trivial_reply(...))` 后 return，不进入停顿图/中断/正常轮 |
| 轻回应实现 | `qq.py:_trivial_reply` | `get_llm([], bind_tools=False).bind(temperature=1.0, max_tokens=60)`；注入 `_meal_period(now.hour)`/`load_recent_daily_summaries(cfg)`/`_recent_user_voice(user_id)`/人物卡 `card_prompt`；无固定模板；异常降级 `"嗯，我在。"` |
| 口吻采样 | `qq.py:_recent_user_voice` | `graph.aget_state` 读 thread 最近 1~3 条 HumanMessage.content，失败降级空串 |
| A0 提示兜底 | `context.py:_RESPONSE_LAYER_RULES` | `build_system_prompt` 恒注入 `[回应分层铁律]`：随口话/情绪话/简短问候 → 一句话带过（20 字内，不调工具不展开）；明确提问/任务 → 全力作答；无法确定一律按正经问题。防漏网随口话仍长篇 |
| 决策类判定 | `qq.py:proposal_detect` | 命中 `_PROPOSAL_KEYWORDS`（推荐/哪个好/怎么选/方案/对比/帮我决定/建议/选择…）→ True |
| 模式注入 | `qq.py:_run_agent` → `state["output_mode"]="proposal"` | 仅首轮；`context.py` 在 `output_mode=="proposal"` 时追加 `=== 多方案输出模式 ===`（`_PROPOSAL_HEADER/_PROPOSAL_RULE`）：【方案一】..【方案三】至多 3 个 + “我建议选…”收尾 |
| 拆条发送 | `qq.py:_split_by_proposal` + `_stream_agent` 尾部 | 按 `_PROPOSAL_RE`(`【方案N】`) 锚点分段，首段=开场、末段=收尾建议；复用 `_SPLIT_LIMIT` 基建逐条发送；锚点 <2 原样整条发送 |
| 一次性语义 | `state.py:GAState.output_mode` / `graph.py:cleanup_images` | `output_mode: str | None` 注解；`cleanup_images` 返回 `output_mode: None` 首轮后清除不泄漏 |

**兜底铁律**：

- `trivial_detect` 未命中 → 一律放行完整回路；轻回应任一环节失败仅降级极简句，绝不抛错阻塞消息循环。
- 分诊只影响「回复方式」，不触碰 checkpoint/线程映射；`_recent_user_voice` 只读 `aget_state`，异常吞掉返回空串。
- 未 kill/重启 bot；未改 `.ps1/.bat`；拆条仅影响发送端，不改变 graph 内 state。

**已知边界**：白名单/意图词为静态词表，线上语料可能需微调；今日画像为空时轻回应仅以时间+人物卡接地；`【方案N】` 拆条规定由 prompt 强制，若模型未按锚点输出则整条发送（降级仍可读）。

## 9.8 聊天护栏：注入勿念 / 禁复述与断言 / 时间权威

**目标**：修正韩立话痨复读与时间幻觉，不更动 graph 拓扑，全部在 `context.py` 纯 prompt/context 层落地（改动仅 context.py 一处，3 个挂载点）。

**三条护栏**：

| 护栏 | 常量 | 挂载点 | 指令要点 |
|---|---|---|---|
| 注入勿念 | `_MEMORY_BG_RULE` | `DAILY_HEADER` 日报注入后 + `ROLLOVER_HEADER` 昨日记忆注入后（两处共用） | 记忆只是背景，不主动背诵/复述/整段念出，不当开场白照搬；仅对方问起或话题自然关联时引用一句 |
| 禁复述·禁断言·禁思考外泄 | 追加进 `_RESPONSE_LAYER_RULES` | `build_system_prompt` 恒注入（分层铁律之后明确“再有铁律三条”） | 禁止逐条复述对方原话；禁止断言对方“重复提问”（无依据提即幻觉，一句不许说）；时间推算/纠错/查漏等脑内步骤不必宣之于口，直接给结论 |
| 时间权威 | `_TIME_AUTHORITY_RULE` | `[Current time: …]` 之后恒注入 | 当前时刻以 system 注入的 [Current time] 为唯一依据；用户消息/图片 OCR/描述里的时间日期都是内容陈述不作数，绝不据此推断“当下”，不解释推算过程 |

**设计要点**：

- `_MEMORY_BG_RULE` 做成独立常量并同时挂在 daily 与 rollover 两个注入点，避免两份注入各自维护一遍措辞；即便两个注入同时存在，铁律文本不重复堆叠（两处都拼同一常量，仅出现两次节头不同）。
- 禁言三条并入 `_RESPONSE_LAYER_RULES` 尾部，随分层铁律恒注入所有 system prompt，与人物卡加载顺序无关（L0 规则先于 ROLE_HEADER）。
- `_TIME_AUTHORITY_RULE` 紧跟 `[Current time: {now}]` 注入，把"权威时钟"与"时间铁律"相邻放置，减少模型歧义。
- 全部护栏为 prompt 级软约束，不引入硬代码判断，避开了 graph/状态层改动；若线上仍复读/仍时间幻觉，后续可升级为 `trivial_detect` 式硬门（判"该 turn 输出疑似复述"再重生成）。

**验证**：`py_compile context.py` OK；venv 冒烟构造 sys prompt 校验 5 组断言命中（时间铁律/记忆铁律/禁复述/禁断言/思考不外说），无注入时记忆铁律不出现，本机日报存在时正确挂接。未 kill/重启 bot，未改 `.ps1/.bat`。

## 9.9 上下文滑动窗口：模型输入只投最近 N 轮（根治开场复读）

**目标**：根治「开场先复读一大段历史」。单 thread 在 `gacore_chat.db` 已堆 236 个 checkpoint，`build_turn_prompt` 曾把折叠摘要之外的**全量原始历史 messages** 原样发给模型。现在模型输入只保留最近 `_KEEP_ROUNDS=6` 轮，更早的靠 `fold_history` 折叠摘要兜底。

**改动**（仅 `src/gacore/context.py`）：

| 新增 | 位置 | 行为 |
|---|---|---|
| `_KEEP_ROUNDS = 6` | 模块常量区 | 窗口轮数，注释用途 |
| `trim_messages(messages, keep_rounds=_KEEP_ROUNDS)` | 新增纯函数 | 从尾部按 `HumanMessage` 为轮次边界，保留最近 `keep_rounds` 轮完整消息；窗口起点恒为 Human，其间 AI/Tool 配对一并保留，无孤儿 ToolMessage；短历史原样返回；不改入参/state |
| `build_turn_prompt` 返回值 | 第 233 行附近 | `return [SystemMessage(content=prompt), *trim_messages(messages)]`（原 `*messages`） |

**数据流**（mermaid）：

```mermaid
flowchart LR
    A["state.messages<br/>全量（236 checkpoint 一个不删）"] -->|fold_history| B["折叠摘要 → system prompt<br/>=== Earlier context ==="]
    A -->|trim_messages keep_rounds=6| C["最近 6 轮原始消息<br/>起点为 Human、无孤儿 ToolMessage"]
    B --> D["模型输入"]
    C --> D
```

**要点**：

- **纯函数、不动存储**：checkpoint 仍全量持久化，滑动窗口只发生在「入模型」时；`trim_messages` 不改 state、不改入参。
- **轮次边界语义**：以 `HumanMessage` 为轮界，窗口起点必是 Human，从尾部数第 `keep_rounds` 个 Human 起保留，杜绝把 ToolMessage 单独截出来制造孤儿。
- **折叠摘要仍进 system**：`fold_history` 与 `_KEEP_ROUNDS` 分工——更早历史由折叠摘要提供粗粒度背景，最近 6 轮提供全量细节。

**验证**：`py_compile context.py` 退出码 0；miniconda py12（`D:\softwares\miniconda\envs\py12\python.exe`）`import gacore.context` 冒烟 OK。行为自测：20 轮 Human/AI/Tool 混合 47 条 → 14 条/6 轮、首条 Human、最近 Human 必在、输入不变、窗口内无孤儿 ToolMessage；短历史原样返回；`build_turn_prompt` = SystemMessage + 裁剪消息且折叠摘要仍在。未 kill/重启 bot，未改 `.ps1/.bat`。
*（内容由AI生成，仅供参考）*

## 2026-08-25：QQ 发送端去重基线持久化（修复"叠发"）
### 现象与定位
QQ 截图显示一条消息内同时出现"上一轮方案回复"与"本轮会错意招呼"，语义跳脱。定位到发送层 `src/gacore/frontends/qq.py::_stream_agent`：
- 包装图 `process → cleanup_images → END`，`cleanup_images`（graph.py:146）对 `state["messages"]` 做标记清洗后 `return {"messages": cleaned, ...}`，`stream_mode="updates"` 会让 **全量历史消息** 在每次 turn 的最后一个 chunk 再次出现；
- `_stream_agent` 用 `_rendered_msg_ids`（模块级 dict，纯 RAM）做已发送去重，设计上允许同一消息被多 chunk 命中；
- 只要进程重启，RAM 集合清空，当前 turn 的 `cleanup_images` 全量 chunk 就会把历史已发 AIMessage 重新 append 进 `reply_parts`，最终 `final_text = "

".join(reply_parts)` 整体作为一条消息发出 → 表现为"把回答都叠在一块"。
### 修复
在 `_stream_agent` 抵达 astream 之前，先 `await self.graph.aget_state(config)` 读出本 thread checkpoint 中已持久化的消息 id，逐一 `rendered.add(mid)` 作为基线。因 checkpointer 持久化在 SQLite，即使进程重启，"历史已发"依然可判定，杜绝重放。基线读取为 best-effort，失败时保持原行为。
### 关联改动
- 本次未动：`context.py::trim_messages`（入模窗口，另条链路已治"开场复读"）；`_split_by_proposal`（发送期方案拆条，逻辑不变）。
### 部署
- 语法通过；bot 404→ pid 2132 @23:29:52 ready，脚本以 `conda py12 -u frontends/qq.py` 且需 `PYTHONPATH=D:\AAAmyPrj\github\myrepos\WithLangGraph\src` 启动（直接 Start-Process 不带该变量会 ModuleNotFoundError: No module named 'gacore'）。
*（内容由AI生成，仅供参考）*

## 9.10 时间铁律升级为硬约束 + 新增 get_time 权威时钟工具（2026-08-26）

**目标**：焊死时间幻觉。此前 `_TIME_AUTHORITY_RULE` 只认 `[Current time]` 为唯一依据但未强制"必须调工具"，模型仍可能凭记忆/上下文猜"当下几点几分"。本轮升级为"禁止"语气硬约束，并新增 `get_time` 工具，让时间答案必须走系统时钟。

**改动清单**：

| 文件 | 改动 |
|---|---|
| `src/gacore/context.py` | 升级 `_TIME_AUTHORITY_RULE`：时间只允许两个权威来源（①`get_time` 工具返回值；②system 注入的 `[Current time]`）；未经调用时间工具/未读到注入时间时**禁止**断言任何具体时钟读数；时间问题**必须先调用 `get_time` 再作答**，工具不可用时以 `[Current time]` 为准；保留原约束（用户消息/OCR 里的时间只是内容陈述不作数、不解释推算过程） |
| `src/gacore/tools/get_time.py`（新增） | `@tool` 纯函数工具，返回 `YYYY-MM-DD 星期N HH:MM:SS (Asia/Shanghai, UTC+8)`，时区 `timezone(timedelta(hours=8))`；docstring 告知模型"需要时间必须先调本工具"；无 I/O、无副作用 |
| `src/gacore/tools/__init__.py` | 注册：import `get_time`、`TOOL_NAMES` 与 `_TOOLS` 首位追加 `get_time`（顺序首位，更易被模型优先调用） |

**工具注册链**（单一事实源 → graph 装配 → 模型可见）：

```mermaid
flowchart LR
    A["tools/__init__.py<br/>TOOL_NAMES + _TOOLS + build_tool_list()"] -->|build_tool_list(cfg)| B["graph.py:177<br/>_build_core_agent tool_list"]
    B -->|create_agent(tools=tool_list)| C["模型绑定全部工具<br/>含 get_time"]
    C -->|涉及时间问题| D["get_time 返回系统时钟<br/>盖章为唯一权威来源"]
```

**设计要点**：

- `get_time` 登在 `TOOL_NAMES` 首位：绑定顺序影响工具选择，把"时钟"放最前提高调用概率。
- 铁律措辞用"禁止/必须先调用"，从"建议"升级为"硬约束"；同时给模型留降级兜底（工具不可用时 `[Current time]`），避免无工具环境空转。
- 纯函数工具无副作用、无需 `cfg`，`build_tool_list` 无需配置即可导出；qq.py 轻回应分支（`get_llm([], bind_tools=False)`）不挂工具，不受影响。
- `get_time` 返回东八区字符串直接可读，不要求模型再从时间戳换算，杜绝二次推算。

**验证**：`py_compile` 三文件退出码 0；miniconda py12 冒烟——铁律含"禁止/先调用"断言 PASS；`TOOL_NAMES` 与 `build_tool_list()`（27 个工具）均含 `get_time`；实调 `get_time.invoke({})` 返回 `2026-08-26 星期三 00:12:33 (Asia/Shanghai, UTC+8)`。未 kill/重启 bot，未改 `.ps1/.bat`。

## 9.11 时间硬化 v2：入口短路 + 完整锚点 + 记忆历史标记（2026-08-26）

**目标**：堵死"韩立凭历史/记忆旧时间报当下"。`get_time` 工具 + 铁律虽已挂上，但模型仍可能走轻回应分支或直接引用记忆里的旧时刻（如把昨晚 23:54 当当下 09:51）。本轮做三层结构性硬化：入口代码短路（P0）、完整时间锚点置底（P1）、记忆注入打历史标记（P2）。

**改动清单**：

| 文件 | 改动 |
|---|---|
| `src/gacore/frontends/qq.py` | P0：新增模块级 `_TZ_SH` / `_WEEK_CN` / `_TIME_INTENT_RE` / `_is_time_intent` / `_time_intent_answer`（`datetime.now(_TZ_SH)` 拼"年月日 星期 时分秒（Asia/Shanghai, UTC+8）"）；`on_message` 在鉴权之后、`_record_known_user` / `_maybe_rollover` / `trivial_detect` 之前插入短路——正则命中"几点/几点钟/几点了/什么时间/今天几号/星期几/礼拜几/周几/过了多久/多久了"等即 `send_text` 原地秒回，不进 LLM / graph / get_time。P2：`_trivial_reply` 的 ctx 升级为完整锚点 + `[历史时间禁令]`，daily 注入走 `_stamp_memory_history` |
| `src/gacore/context.py` | P1：`build_system_prompt` 移除中段单行 `[Current time]`，改为 prompt 末尾（hints 后、return 前）拼完整锚点块 `【当前真实时间】YYYY-MM-DD HH:MM:SS 星期X（Asia/Shanghai, UTC+8）` + `[历史时间禁令]` 声明；`_TIME_AUTHORITY_RULE` 追加"历史/记忆里时间均为陈旧记录禁止当当下"。P2：`DAILY_HEADER` 改历史声明文案，新增 `stamp_daily_history` 给含时间戳行加 `[历史@时间戳]` 前缀 |

**设计要点**：

- P0 闸门放在所有记忆 / LLM 副作用之前：时间问题不触发 rollover、不写记忆、不进 graph，纯函数拼字符串秒回，时区显式东八区。
- P1 锚点块每轮用 `datetime.now(_TZ)` 重建并 pin 在 prompt 最末（hints 之后、return 之前），离用户消息最近；锚点自带 `[历史时间禁令]`，与铁律 `_TIME_AUTHORITY_RULE` 双写一致性声明——历史注入（每日笔记 / 昨日记忆 / 对话历史）中的任何时间一律视为陈旧记录。
- P2 时间戳行识别用 `\d{1,2}[点时]` 或日期格式正则，命中即加 `[历史@时间戳]` 前缀，明确"供了解过往、绝不代表当前"；轻回应分支同款处理，避免双路径不一致。
- 与 `get_time` 工具双轨：P0 短路覆盖"纯问时间"的最常见路径，其余时间相关问题仍由工具 + 锚点兜底。

**验证**：`py_compile` context.py / qq.py 通过；py12 冒烟——P0 11 用例判中全对、`_time_intent_answer()` 返回 `现在是 2026年08月26日 星期三 10:11:30（Asia/Shanghai, UTC+8）`；P1 `build_system_prompt` 锚点完整且位于末尾、含"陈旧记录 / 严禁当作当下时刻作答"；P2 `stamp_daily_history` 时间戳行正确打标。未重启 bot。

**后续变更（2026-08-26）**：P0 入口短路已整体移除——`qq.py` 中 `_TIME_INTENT_RE` / `_is_time_intent` / `_time_intent_answer` 及 `on_message` 内最前置"秒回"块全部删除，时间类提问恢复走原链路（trivial 闸门 + LLM 主流程，依托 P1 完整锚点与 `get_time` 工具自然作答）；P1 / P2 保留不变；`_TZ_SH` / `_WEEK_CN` / `_stamp_memory_history` 仍被 `_trivial_reply` 引用故一并保留。


## 9.12 daily-report prompt 升级：人物速写（2026-08-26）
- 位置：config/schedule.json → jobs[0].prompt（daily-report，deliver_to=email）。
- 产出从"归档+画像增量"扩为三层，新增「人物速写」：以 file_read 读 memory/global_mem_insight.txt + global_mem.txt 建立长期脉络，search_daily 近 2-3 天做昨日对比；速写要点 = 一句话状态 + 兴趣连线(加深/新苗头/翻篇) + 微变化/苗头。
- 最终回复（邮件正文）= 人物速写为主体；详细版写 daily note。
- 注意：agent 无读 long-term 专用工具，靠 file_read 读文件，路径相对项目根；global_mem 文件由 start_long_term_update 写。
- 依赖：工具 file_read / search_daily / langTrack_stats / bili_history / code_run 均已注册（TOOL_NAMES）。

## 9.13 时间约束强化 + LLM 请求体日志（2026-08-27）
**目标**：①把时间铁律从"时间问题的统一出处"推广到"凡涉及时间概念先看真实时间"；②核验既有硬化 prompt 是否真被装配生效；③给真实 LLM 调用增加完整请求体日志，便于日后排查。
### 强化点（`context.py` / `frontends/qq.py`）
| 位置 | 改动 |
| --- | --- |
| `context.py:_TIME_AUTHORITY_RULE`（:42） | 扩写为"凡是回答里会用当前时间概念（几点几分/几号/星期/时段/距某时刻多久/工作日周末/班次上下班/今天昨天明天/日期推算）必须先调 `get_time` 拿系统时钟，严禁用历史/记忆/注入文本推算当下"；原"只认 get_time 返回值与 [Current time] 两个权威源"保留 |
| `context.py:build_system_prompt`（:197） | 末尾锚点块追加"凡涉及当前时间/日期/星期/时段/班次/剩余时长的问题，先调用 get_time 工具以官方时钟作答" |
| `frontends/qq.py:_trivial_reply`（:1698） | 轻回应 ctx 真实时间注入行后追加硬声明"凡涉及现在几点/几号/星期几/还剩多久/班次判断等时间问题，一律按上面这行真实时间作答"（该分支无 get_time，注入时间为唯一依据） |
### 生效链路（既有项核实，均有代码引用证据）
- P1 锚点：`context.py:197-198` → `middleware.py:83 GAPromptMiddleware.modify_model_request` → `graph.py:179-184 build_tool_list + create_agent`（首轮 system prompt）。
- P2 记忆标记：`context.py:170`（DAILY_HEADER + `stamp_daily_history`）；`qq.py:1703`（`_stamp_memory_history` 对 daily 时间戳行加 `[历史@时间戳]`）。
- `get_time` 工具：`tools/__init__.py:22/41/71`（import / TOOL_NAMES 首位 / _TOOLS 首位）→ `graph.py:177 build_tool_list` → `create_agent(tools=...)`。
- daily-report 三层：`config/schedule.json` jobs[0].prompt → `scheduler.py:run_job`（23:50）→ `logs/scheduled/daily-report_<ts>.md`；运行证据见 `daily-report_20260827_000050.md`（摘要"三层产出完成"，Reply 以人物速写为主体）。
### LLM 请求体日志（`llm.py` + 新增 `src/gacore/llm_request_log.py`）
- 配置：`get_llm`（`src/gacore/llm.py`）返回实例前统一 `install_llm_logging(llm, provider)`——单挂点覆盖主 agent graph / scheduler job / qq trivial 三路，不侵入各调用点。
- 机制：`install_llm_logging` 对模型实例 monkey-patch `invoke/ainvoke/stream/astream/bind_tools`，capture 后原样转发；`bind_tools` 把工具定义暂存到实例（`_gacore_bound_tools`），后续调用随记录写入；调用前拦截任意方法拼完整记录（messages / tools / params / provider / model / run_kind / timestamp / thread?）。
- 落盘：`logs/{YYYY-MM-DD}/llm_requests.jsonl`，JSONL 追加写，`ensure_ascii=False`，utf-8。
- 脱敏：递归遍历结构，键名命中 `api_key|access_token|Authorization|secret|token`（大小写不敏感）的值 → `***`；超长字符串（>2000 字符）截断；`messages` 内 image 内容只记元数据不记 base64。
- 兜底：登录全程 try/except，失败仅 best-effort 静默（不阻断模型调用）；线程安全借 `threading.Lock`。
- 验证（桩 FakeModel，不联网）：invoke/ainvoke/astream/stream 四 run_kind 全部写盘；绑 tools 后记录含 ntools=2；params 含 temperature/model_kwargs；api_key 掩码 `***` 且原文未泄漏；py_compile 四文件 EXIT=0。
### 修复：pydantic v2 不兼容导致启动崩溃（2026-08-27）
- **现象**：重启 bot 时 `get_llm → install_llm_logging` 抛 `ValueError: "ChatOpenAI" object has no field "invoke"`，start.py 初始化失败。
- **根因**：旧实现直接实例动态属性赋值（`llm.invoke = _invoke` 等）；新版 langchain `ChatOpenAI` 是 pydantic v2 `BaseModel`，`__setattr__` 拒绝未声明字段赋值。
- **规避**（选用 `object.__setattr__` 而非实例包装，理由见下）：新增 `_patch_instance(obj, name, fn)`，内部 `object.__setattr__(obj, name, fn)` 绕过字段校验直接写实例 `__dict__`；属性查找仍走实例 dict（类上无同名 data descriptor），所有既有调用点透明无改动。
  - 为何不用包装/代理：包装类需透传全部 pydantic 字段与方法、破坏 `isinstance(llm, ChatOpenAI)` 及 `.bind_tools()` 链式返回、并让 `llm.dump_model()`/序列化等内部行为失效，侵入面远大于一次性绕过 setattr；且本项目已在实例层 patch 五个方法 + 两个标记，逐方法包装会与 langchain 内部对实例方法的引用割裂。
- **验证**：真实 `ChatOpenAI(api_key=哑key)` install/idempotent/bind_tools(`_ChatModelBinding`)/工具定义捕获全过；`FakeMessagesListChatModel`（pydantic v2）四 run_kind 写盘、工具定义（name/description/args）序列化正确、`api_key` → `***` 掩码且原文未落盘；py_compile 四文件 EXIT=0。冒烟脚本（temp 目录）与临时测试日志已说明。
*（内容由AI生成，仅供参考）*

*（内容由AI生成，仅供参考）*

## 9.14 输出侧时间守卫 + daily_notes 东八区 + 铁律补强（2026-08-27）

**目标**：给时间约束加「输出侧强制兜底」。此前 `get_time` 工具 / 时间铁律 / 完整锚点均为提示侧，模型仍可能断言错误时刻而无人拦截；同时让 `daily_notes` 相对日期不受服务器时区影响；铁律补「先经 get_time 核实一次」条款。

### 改动清单

| 文件 | 改动 |
| --- | --- |
| `src/gacore/middleware.py` | 新增 `_TZ`(UTC+8)、`_MAX_TIME_GUARD_RETRIES=2`、`_TIME_GUARD_PROMPT`（修正指令模板）；新增 `check_reply_time_assertions(reply, now) -> list`——校验「现在N点/中文钟点/N:MM/点半一刻三刻/点Y分」时钟读数（分钟级精度 + 12 小时制双候选）、「今天星期X」、日期格式「X月X日」三类断言，明显偏离才返回违规清单；否定句（含不/没/非）不算断言、直接跳过；`GATurnLogicMiddleware.after_model` 在 AI 产出后置校验：命中 → 向 messages 追加 HumanMessage（`_TIME_GUARD_PROMPT`+真实时钟+违规清单）并 `jump_to=model` 触发重试，`time_guard_retries` 计数超 2 → `exit_reason=TIME_GUARD_EXCEEDED` 硬停 |
| `src/gacore/state.py` | `GAState.time_guard_retries: int`；`new_state()` 置 0；`EXIT_REASONS` 增 `TIME_GUARD_EXCEEDED="time_guard_exceeded"` |
| `src/gacore/tools/daily_notes.py` | `datetime.now(UTC)` → 模块常量 `_TZ = timezone(timedelta(hours=8))`；`_resolve_date` / `load_recent_daily_summaries` 用 `datetime.now(_TZ)` 解析 today/yesterday/近期摘要的 day 边界 |
| `src/gacore/context.py` | `_TIME_AUTHORITY_RULE` 扩写：即使看到注入的【当前真实时间】锚点、回复要引用具体时刻也应优先经 `get_time` 核实一次；凡涉及当前时间概念必先调 get_time，禁止凭记忆/上下文/锚点以外推算直接断言；声明输出侧时间守卫会拦截明显偏离并强制重试 |

### 设计要点

- **仅拦「明显偏离」**：守卫容忍分钟级误差与 12 小时制双候选，避免把"8 点 03 分"误判为偏离"8 点"；无时间断言的回复直接放行，零误伤。
- **重试复用既有通道**：修正指令以 HumanMessage 追加进 messages 并 `jump_to=model`，复用空响应重试同款跳转通道，驱动模型重新走 get_time，不改变 LangGraph 拓扑；超预算用 `exit_reason=TIME_GUARD_EXCEEDED` 硬停，不与既有 exit_reason / retry 机制冲突，不带病输出错误时间。
- **返修防误伤（当日 code-review 命中）**：① 否定句不算断言——「今天不是星期三」「现在没到3点」含不/没/非直接跳过，守卫不再拦否认语气；② 半刻钟/刻钟精度——「现在3点半」「3点一刻」折成 30/15 分会话，16:00 实际撞上「3点半」不再误拦；③ 冒号制与「点Y分」显式分钟也纳入分钟级判定。
- **时区单基准**：daily_notes 统一东八区消除 UTC/服务器本地时区的"今天"漂移，与 get_time 返回值 / system 锚点为同一基准（Asia/Shanghai UTC+8）。
- **铁律与守卫协同**：铁律要求「先调 get_time 核实」，守卫在输出侧兜底强制重试——双保险，且措辞互相引用不矛盾。

### 验证

- `py_compile` middleware.py / state.py / daily_notes.py / context.py 退出码 0。
- 自测（temp/self_test_time_guard.py）：守卫 12 例全过（正常时钟 / 12 小时制双候选 / 多违规 / 无断言不误伤 / 星期错 / 日期错等）；`_resolve_date` UTC 服务器场景 `08-26` → `08-27`；`build_system_prompt` 含新增铁律短语。详见 roadmap 对应记录。
- 未 kill/重启 bot；未改 `.ps1/.bat`。
