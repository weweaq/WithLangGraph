"""langTrack 位置事实 v2：schema 冻结与事务化迁移骨架（§2.2 / §2.4）。

本模块只负责“位置事实 v2 表结构”与“数据库层面的事务切换”，不负责：
- 坐标解析 / canonical 聚类 / 新旧匹配（见 location_facts.py，纯算法层）；
- 标签文件两阶段替换（label_places.py，Task 4 落地）；
- geocode 外呼 / 路线编码（只能在 status=complete 之后运行）。

对外四个事务函数：
- :func:`create_location_v2_tables`：幂等创建全部 v2 事实表 + 迁移审计表（正式名 *v2）。
- :func:`validate_location_v2`：唯一键 / 索引 / 东八区时间列 / 孤儿 stays.place_id 校验。
- :func:`activate_location_v2`：BEGIN IMMEDIATE 事务内把 v1 表备份、shadow/v2 表转正、
  写 PRAGMA user_version=2 与 pending_label_swap 状态；任一步失败整段 ROLLBACK。
- :func:`rollback_location_v2`：BEGIN IMMEDIATE 事务内把 v2 正式表改名为
  *_failed_v2_<run_id>、恢复 *_v1_backup、写 user_version=1 与 rolled_back。

设计约束（高内聚低耦合）：
- 所有 DDL/DML 只出现在本模块；etl.py 仅通过 CLI 参数调用 build_location_shadow。
- activate/rollback 内部不允许出现文件写入或网络外呼（geocode/route 一律在外）。
- shadow 表（shadow_*_v2）由 build_location_shadow 生成，只读对比，不覆盖正式表。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# 表清单
# ---------------------------------------------------------------------------

# v1 业务表：activate 时备份为 *_v1_backup，rollback 时恢复。
V1_FACT_TABLES: tuple[str, ...] = (
    "places",
    "stays",
    "trips",
    "anomalies",
    "route_grids",
    "grid_pois",
)

# v2 事实表（§2.2）：activate 转正后的正式表名。
V2_FACT_TABLES: tuple[str, ...] = (
    "places",
    "place_cells",
    "stays",
    "trips",
    "place_tag_conflicts",
    "anomalies",
    "route_grids",
    "grid_pois",
)

# shadow 数据源表（Task 3 由 build_location_shadow 生成）。
SHADOW_SOURCE_TABLES: dict[str, str] = {
    "places": "shadow_places_v2",
    "place_cells": "shadow_place_cells_v2",
    "stays": "shadow_stays_v2",
    "trips": "shadow_trips_v2",
}

# 有旧表、activate 时直接由 v2 表转正的（无 shadow 数据源）。
V2_DIRECT_TABLES: tuple[str, ...] = (
    "place_tag_conflicts",
    "anomalies",
    "route_grids",
    "grid_pois",
)

# 迁移审计表（§2.2）。
AUDIT_TABLES: tuple[str, ...] = (
    "location_migration_state",
    "location_place_mapping",
    "location_migration_issues",
    "location_migration_metrics",
)

MIGRATION_STATE_STATUSES: tuple[str, ...] = (
    "prepared",        # shadow 构建完成、未切换
    "pending_label_swap",  # DB 已提交、标签文件待原子替换
    "complete",        # 标签文件已替换、ETL 可运行
    "rolled_back",     # 已回滚到 v1
)


class LocationMigrationError(RuntimeError):
    """位置迁移流程错误（校验失败 / 事务异常），不会静默吞掉。"""


# ---------------------------------------------------------------------------
# schema v2（§2.2 原文，含东八区 created_at/updated_at 默认值）
# ---------------------------------------------------------------------------

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS places_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  place_id TEXT NOT NULL,
  grid_key TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  label TEXT NOT NULL DEFAULT '未知',
  first_seen INTEGER,
  last_seen INTEGER,
  point_count INTEGER NOT NULL DEFAULT 0,
  visit_count INTEGER NOT NULL DEFAULT 0,
  stay_ms INTEGER NOT NULL DEFAULT 0,
  is_primary INTEGER NOT NULL DEFAULT 0,
  source_coord_system TEXT NOT NULL DEFAULT 'unknown',
  center_method TEXT NOT NULL DEFAULT 'stay_median',
  address TEXT, poi TEXT, district TEXT, township TEXT,
  business_area TEXT, poi_type TEXT,
  poi_l1 TEXT, poi_l2 TEXT, poi_l3 TEXT,
  poi_signal TEXT, poi_fallback TEXT,
  matched_level TEXT, behavior TEXT, geocoded_at INTEGER,
  candidate_label TEXT,
  confidence_home REAL NOT NULL DEFAULT 0,
  confidence_work REAL NOT NULL DEFAULT 0,
  aoi TEXT, parent_poi TEXT, poi_distance_m REAL,
  display_granularity TEXT NOT NULL DEFAULT 'neighborhood',
  name_confidence REAL NOT NULL DEFAULT 0,
  name_evidence TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  UNIQUE(device_id, place_id),
  UNIQUE(device_id, grid_key)
);
CREATE TABLE IF NOT EXISTS place_cells_v2 (
  device_id TEXT NOT NULL,
  place_id TEXT NOT NULL,
  grid_key TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  PRIMARY KEY(device_id, grid_key),
  UNIQUE(device_id, place_id, grid_key)
);
CREATE TABLE IF NOT EXISTS stays_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  start_ts INTEGER NOT NULL,
  end_ts INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL,
  center_lat REAL NOT NULL,
  center_lon REAL NOT NULL,
  min_lat REAL NOT NULL,
  min_lon REAL NOT NULL,
  max_lat REAL NOT NULL,
  max_lon REAL NOT NULL,
  n_points INTEGER NOT NULL DEFAULT 0,
  accuracy_known_points INTEGER NOT NULL DEFAULT 0,
  avg_accuracy_m REAL,
  radius_m REAL NOT NULL DEFAULT 0,
  grid_key TEXT,
  place_id TEXT,
  source_coord_system TEXT NOT NULL DEFAULT 'unknown',
  day TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_stays_v2_device_time
ON stays_v2(device_id, start_ts, end_ts);
CREATE INDEX IF NOT EXISTS idx_stays_v2_device_place
ON stays_v2(device_id, place_id);
CREATE TABLE IF NOT EXISTS trips_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  start_ts INTEGER NOT NULL,
  end_ts INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL,
  start_lat REAL NOT NULL,
  start_lon REAL NOT NULL,
  end_lat REAL NOT NULL,
  end_lon REAL NOT NULL,
  from_place_id TEXT,
  to_place_id TEXT,
  endpoint_coord_system TEXT NOT NULL DEFAULT 'unknown',
  dist_m REAL NOT NULL,
  n_points INTEGER NOT NULL DEFAULT 0,
  day TEXT,
  polyline TEXT,
  polyline_coord_system TEXT,
  route_key TEXT,
  route_mode TEXT,
  route_encoded_at INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  UNIQUE(device_id, start_ts, end_ts)
);
CREATE INDEX IF NOT EXISTS idx_trips_v2_device_time
ON trips_v2(device_id, start_ts, end_ts);
CREATE TABLE IF NOT EXISTS place_tag_conflicts_v2 (
  device_id TEXT NOT NULL,
  new_place_id TEXT NOT NULL,
  old_place_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  reason TEXT NOT NULL,
  resolved_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  PRIMARY KEY(device_id, new_place_id, old_place_id, tag)
);
CREATE TABLE IF NOT EXISTS anomalies_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  device_id TEXT NOT NULL,
  place_id TEXT,
  grid_key TEXT,
  poi TEXT,
  detail TEXT,
  ts INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_anomalies_v2_unique
ON anomalies_v2(
  day, kind, device_id,
  COALESCE(place_id,''),
  COALESCE(grid_key,'')
);
CREATE TABLE IF NOT EXISTS route_grids_v2 (
  device_id TEXT NOT NULL,
  day TEXT NOT NULL,
  grid_lat REAL NOT NULL,
  grid_lon REAL NOT NULL,
  n_pass INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  PRIMARY KEY(device_id, day, grid_lat, grid_lon)
);
CREATE TABLE IF NOT EXISTS grid_pois_v2 (
  grid_lat REAL NOT NULL,
  grid_lon REAL NOT NULL,
  name TEXT,
  type TEXT,
  distance TEXT,
  queried_at INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  PRIMARY KEY(grid_lat, grid_lon)
);
CREATE TABLE IF NOT EXISTS location_migration_state (
  id INTEGER PRIMARY KEY CHECK(id=1),
  run_id TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  status TEXT NOT NULL,
  pending_labels_path TEXT,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
CREATE TABLE IF NOT EXISTS location_place_mapping (
  run_id TEXT NOT NULL,
  old_device_id TEXT NOT NULL,
  old_grid_key TEXT NOT NULL,
  old_place_id TEXT NOT NULL,
  new_place_id TEXT,
  match_reason TEXT NOT NULL,
  jaccard REAL,
  distance_m REAL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  PRIMARY KEY(run_id, old_device_id, old_place_id)
);
CREATE TABLE IF NOT EXISTS location_migration_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  source_payload TEXT NOT NULL,
  device_id TEXT,
  grid_key TEXT,
  tag TEXT,
  resolution_status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
CREATE TABLE IF NOT EXISTS location_migration_metrics (
  run_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  PRIMARY KEY(run_id, metric)
);
"""

# 每张 v2 表期望的唯一约束 / 主键（用于 validate 校验，§2.2 逐表核对）。
EXPECTED_UNIQUES: dict[str, tuple[str, ...]] = {
    "places_v2": ("device_id, place_id", "device_id, grid_key"),
    "place_cells_v2": ("device_id, grid_key", "device_id, place_id, grid_key"),
    "stays_v2": (),
    "trips_v2": ("device_id, start_ts, end_ts",),
    "place_tag_conflicts_v2": ("device_id, new_place_id, old_place_id, tag",),
    "anomalies_v2": (),
    "route_grids_v2": ("device_id, day, grid_lat, grid_lon",),
    "grid_pois_v2": ("grid_lat, grid_lon",),
}

# 每张 v2 表期望的显式索引（validate 校验存在性）。
EXPECTED_INDEXES: dict[str, tuple[str, ...]] = {
    "stays_v2": ("idx_stays_v2_device_time", "idx_stays_v2_device_place"),
    "trips_v2": ("idx_trips_v2_device_time",),
    "anomalies_v2": ("idx_anomalies_v2_unique",),
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return (row[0] if row else "") or ""


def _index_names(conn: sqlite3.Connection, table: str) -> set[str]:
    # PRAGMA 不接受参数占位符；table 来自固定常量集（模块内生成），无注入面。
    return {r[1] for r in conn.execute(f"PRAGMA index_list('{table}')")}


# ---------------------------------------------------------------------------
# 创建 / 校验
# ---------------------------------------------------------------------------

def create_location_v2_tables(conn: sqlite3.Connection) -> None:
    """幂等创建全部 v2 事实表 + 迁移审计表（§2.2）。不触碰任何 v1 表。"""
    conn.executescript(SCHEMA_V2)


def validate_location_v2(conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    """校验 v2 层是否满足切换条件（§2.4 步骤 9）。

    返回 (ok, errors)：
    - 全部 v2 事实表 + 审计表存在；
    - 每张 v2 表含东八区 created_at / updated_at 默认值；
    - 唯一约束 / 显式索引与 §2.2 一致；
    - 孤儿校验：stays_v2.place_id 非空且必须存在于 places_v2.place_id，
      否则（有数据时）阻止切换。
    """
    errors: list[str] = []
    names = _table_names(conn)

    all_v2 = [f"{t}_v2" for t in V2_FACT_TABLES] + list(AUDIT_TABLES)
    for t in all_v2:
        if t not in names:
            errors.append(f"missing table: {t}")

    # 时间列 + 唯一约束 + 索引（仅对已存在的表做细化校验，避免噪音堆叠）
    for t in all_v2:
        if t not in names:
            continue
        sql = _table_sql(conn, t)
        if "created_at" not in sql or "updated_at" not in sql:
            errors.append(f"{t}: missing created_at/updated_at")
        elif "+8 hours" not in sql:
            errors.append(f"{t}: created_at/updated_at must default to CST (+8 hours)")
        expected = EXPECTED_UNIQUES.get(t)
        if expected:
            for uni in expected:
                if uni not in sql:
                    errors.append(f"{t}: missing unique/pk ({uni})")

    for t, idxs in EXPECTED_INDEXES.items():
        if t not in names:
            continue
        actual = _index_names(conn, t)
        for idx in idxs:
            if idx not in actual:
                errors.append(f"{t}: missing index {idx}")

    # 孤儿 stays.place_id 校验：有数据时不允许悬空引用（阻止切换）。
    if "stays_v2" in names and "places_v2" in names:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM stays_v2 s WHERE s.place_id IS NOT NULL "
                "AND s.place_id NOT IN (SELECT place_id FROM places_v2)"
            ).fetchone()
            if row and row[0] > 0:
                errors.append(f"orphan stays_v2.place_id: {row[0]} rows reference missing places_v2")
        except sqlite3.OperationalError as e:
            errors.append(f"stays_v2 orphan check failed: {e}")

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# 迁移状态辅助（location_migration_state 单行）
# ---------------------------------------------------------------------------

def _write_state(
    conn: sqlite3.Connection,
    run_id: str,
    schema_version: int,
    status: str,
    pending_labels_path: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO location_migration_state(id, run_id, schema_version, status, "
        "pending_labels_path, error, updated_at) "
        "VALUES (1,?,?,?,?,?,datetime('now','+8 hours')) "
        "ON CONFLICT(id) DO UPDATE SET "
        "run_id=excluded.run_id, schema_version=excluded.schema_version, "
        "status=excluded.status, pending_labels_path=excluded.pending_labels_path, "
        "error=excluded.error, updated_at=datetime('now','+8 hours')",
        (run_id, schema_version, status, pending_labels_path, error),
    )


def read_migration_state(conn: sqlite3.Connection) -> dict | None:
    """读取迁移状态；不存在返回 None。"""
    row = conn.execute(
        "SELECT run_id, schema_version, status, pending_labels_path, error "
        "FROM location_migration_state WHERE id=1"
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": row[0],
        "schema_version": row[1],
        "status": row[2],
        "pending_labels_path": row[3],
        "error": row[4],
    }


def _rename_table(conn: sqlite3.Connection, old: str, new: str) -> None:
    # 标识符统一加双引号（run_id 可能含连字符，如 *-failed_v2_<run_id>）。
    conn.execute(f'ALTER TABLE "{old}" RENAME TO "{new}"')


def _missing_tables(conn: sqlite3.Connection, tables: list[str]) -> list[str]:
    """返回列表中实际不存在的表名（缺失集），供调用方报错。"""
    names = _table_names(conn)
    return [t for t in tables if t not in names]


# ---------------------------------------------------------------------------
# 切换（activate）与回滚（rollback）—— 必须在单事务内，禁止 IO 外呼
# ---------------------------------------------------------------------------

def activate_location_v2(
    conn: sqlite3.Connection,
    run_id: str,
    pending_labels_path: str | None = None,
) -> None:
    """执行位置事实 v2 切换（§2.4 步骤 7-12 的 DB 部分）。

    前置：validate_location_v2 必须通过；shadow/v2 表数据已就绪。
    流程（单个 BEGIN IMMEDIATE 事务，任一步失败整段 ROLLBACK）：
      1. 校验未通过 → 抛 LocationMigrationError，不开启事务；
      2. 旧表 places/stays/trips/anomalies/route_grids/grid_pois → *_v1_backup；
      3. shadow 数据源表（shadow_*_v2）→ 正式表名；无 shadow 时用 v2 表转正；
      4. place_tag_conflicts/anomalies/route_grids/grid_pois 的 v2 表 → 正式表名；
      5. 写 PRAGMA user_version=2 与 status=pending_label_swap + pending 路径；
      6. COMMIT。
    本函数内不进行任何文件写入或网络外呼（geocode/route 一律在事务外）。
    """
    ok, errors = validate_location_v2(conn)
    if not ok:
        raise LocationMigrationError("validate_location_v2 failed: " + "; ".join(errors))

    names = _table_names(conn)
    missing = _missing_tables(conn, [f"{t}_v2" for t in V2_FACT_TABLES] + list(AUDIT_TABLES))
    if missing:
        raise LocationMigrationError("missing v2 tables: " + ", ".join(missing))

    conn.execute("BEGIN IMMEDIATE")
    try:
        # 1) 备份旧表
        for t in V1_FACT_TABLES:
            if t not in names:
                raise LocationMigrationError(f"v1 table not found: {t}")
            _rename_table(conn, t, f"{t}_v1_backup")

        # 2) shadow 数据源转正（优先），否则用 v2 表转正
        for formal in ("places", "place_cells", "stays", "trips"):
            shadow = SHADOW_SOURCE_TABLES[formal]
            source = shadow if shadow in names else f"{formal}_v2"
            if source not in _table_names(conn):
                raise LocationMigrationError(f"source table not found: {source}")
            _rename_table(conn, source, formal)

        # 3) 无 shadow 的 v2 表直接转正
        for formal in V2_DIRECT_TABLES:
            _rename_table(conn, f"{formal}_v2", formal)

        # 4) 写版本与状态
        conn.execute("PRAGMA user_version = 2")
        _write_state(conn, run_id, 2, "pending_label_swap", pending_labels_path)

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback_location_v2(conn: sqlite3.Connection, run_id: str) -> None:
    """回滚到 v1（§2.4 rollback 清单）。

    单个 BEGIN IMMEDIATE 事务：
      1. 校验所有 *_v1_backup 存在，否则抛错（禁止半回滚）；
      2. 当前 v2 正式表 → *_failed_v2_<run_id>（六张业务表）；
      3. *_v1_backup → 正式表名；
      4. 写 PRAGMA user_version=1 与 status=rolled_back；COMMIT。
    恢复的表保留全部索引与 polyline/route_key 缓存（rename 不丢数据）。
    """
    names = _table_names(conn)
    missing = [f"{t}_v1_backup" for t in V1_FACT_TABLES if f"{t}_v1_backup" not in names]
    if missing:
        raise LocationMigrationError("missing v1 backups: " + ", ".join(missing))

    conn.execute("BEGIN IMMEDIATE")
    try:
        # 当前正式表改名 failed 快照（仅当确实处于 v2 状态）
        for t in V1_FACT_TABLES:
            if t in _table_names(conn):
                _rename_table(conn, t, f"{t}_failed_v2_{run_id}")
        # 恢复 v1
        for t in V1_FACT_TABLES:
            _rename_table(conn, f"{t}_v1_backup", t)

        conn.execute("PRAGMA user_version = 1")
        _write_state(conn, run_id, 1, "rolled_back", error=None)

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# shadow 构建骨架（Task 3 填充全量重建逻辑）
# ---------------------------------------------------------------------------

def build_location_shadow(db_path: Path | str) -> int:
    """构建只读对比表 shadow_places_v2/shadow_place_cells_v2/shadow_stays_v2/shadow_trips_v2。

    Task 3 实现全量重建；本骨架只负责建表并标记 full rebuild，不改正式表。
    返回 shadow_stays_v2 行数（0 = 骨架/空库）。
    """
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)
    try:
        names = _table_names(conn)
        # 确保 v2 结构存在（shadow 表直接复用 v2 DDL 的名称是正式 *v2，此处不建 shadow；
        # shadow 构建细节在 Task 3 通过 location_migration.SHADOW_SOURCE_TABLES 落表）。
        create_location_v2_tables(conn)
        # 记录“location v2 full rebuild”（首版只允许全量）
        if "etl_runs" in names:
            conn.execute(
                "INSERT INTO etl_runs(version, mode, status, started_at) "
                "VALUES (?, 'location_v2_full', 'running', datetime('now','+8 hours'))",
                ("2.0.0-shadow",),
            )
            conn.commit()
        return 0
    finally:
        conn.close()
