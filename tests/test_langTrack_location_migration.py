"""langTrack 位置事实 v2：schema 冻结与事务迁移骨架测试（Task 1）。

运行需 PYTHONPATH=src。不依赖真实 data/langTrack.db，全部使用 :memory: 合成库。

覆盖（§2.2 / §2.4 Task1 清单）：
- 用当前 v1 schema（places/stays/trips/anomalies/route_grids/grid_pois）建旧库 fixture；
  迁移中任意 SQL 异常后，旧表、索引、user_version 和数据完全不变。
- 所有 v2 事实表及 migration state/mapping/issues/metrics 均含东八区 created_at/updated_at。
- 所有唯一键和索引与 §2.2 一致；孤儿 stays.place_id 校验会阻止切换。
- activate 使用 BEGIN IMMEDIATE，只接受已验证数据，成功后保留全部 *_v1_backup 并写 user_version=2。
- DB 已提交、标签文件替换前崩溃时，下一次启动能从 pending 状态 / DB 投影恢复。
- rollback 恢复六张业务表及索引，路线 polyline/route_key 缓存不得丢失。
- activate/rollback 内部无 geocode 外呼与文件写入。
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from gacore.langTrack import etl
from gacore.langTrack import location_migration as lm


def _func_body_source(func) -> str:
    """返回函数源码（剔除 docstring），用于验证事务函数内部无 IO 外呼。"""
    tree = ast.parse(inspect.getsource(func))
    fn = tree.body[0]
    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(fn):
        fn.body = fn.body[1:]
    return ast.unparse(fn)


# ---------------------------------------------------------------------------
# fixture：按当前 v1 schema 建旧库并填充六张业务表数据
# ---------------------------------------------------------------------------

def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    # PRAGMA 不接受参数占位符，table 来自固定常量集。
    return {r[1] for r in conn.execute(f"PRAGMA index_list('{table}')")}


@pytest.fixture
def v1_db():
    """基于 etl._SCHEMA 建完整 v1 库，插入六张业务表样本数据。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(etl._SCHEMA)
    cur = conn.cursor()

    # places：两设备各一个点 + 家/公司标签
    cur.executemany(
        "INSERT INTO places(device_id, grid_key, lat, lon, label, first_seen, last_seen, visit_count, is_primary) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("dev1", "31.992,118.783", 31.992, 118.783, "家", 1000, 2000, 30, 1),
            ("dev1", "31.998,118.790", 31.998, 118.790, "公司", 1000, 2000, 25, 1),
            ("dev2", "31.992,118.783", 31.992, 118.783, "未知", 1000, 2000, 5, 0),
            ("dev1", "31.980,118.800", 31.980, 118.800, "未知", 1000, 2000, 3, 0),
        ],
    )
    # stays：含跨午夜段（验证不按 day 截断的 schema 承载）
    cur.executemany(
        "INSERT INTO stays(device_id, start_ts, end_ts, duration_ms, center_lat, center_lon, "
        "min_lat, min_lon, max_lat, max_lon, n_points, radius_m, grid_key, day) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("dev1", 1000, 8000, 7000, 31.992, 118.783, 31.991, 118.782, 31.993, 118.784, 20, 50.0, "31.992,118.783", "2026-08-17"),
            ("dev1", 9000, 20000, 11000, 31.998, 118.790, 31.997, 118.789, 31.999, 118.791, 30, 60.0, "31.998,118.790", "2026-08-17"),
            ("dev1", 21000, 90000, 69000, 31.992, 118.783, 31.991, 118.782, 31.993, 118.784, 40, 50.0, "31.992,118.783", "2026-08-18"),
            ("dev2", 1000, 6000, 5000, 31.992, 118.783, 31.991, 118.782, 31.993, 118.784, 15, 45.0, "31.992,118.783", "2026-08-17"),
        ],
    )
    # trips：带 polyline/route_key 缓存（rollback 不得丢失）
    cur.executemany(
        "INSERT INTO trips(device_id, start_ts, end_ts, duration_ms, start_lat, start_lon, "
        "end_lat, end_lon, dist_m, n_points, day, polyline, route_key, route_mode, route_encoded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("dev1", 8000, 9000, 1000, 31.992, 118.783, 31.998, 118.790, 900.0, 3, "2026-08-17",
             "enc:abc123", "rk_home_work", "driving", 5000),
            ("dev1", 20000, 21000, 1000, 31.998, 118.790, 31.992, 118.783, 900.0, 3, "2026-08-18",
             "enc:def456", "rk_work_home", "driving", 6000),
        ],
    )
    # anomalies
    cur.executemany(
        "INSERT INTO anomalies(day, kind, device_id, grid_key, poi, detail, ts) VALUES (?,?,?,?,?,?,?)",
        [
            ("2026-08-17", "new_place", "dev1", "31.980,118.800", "某新点", "访问 1 次", 12000),
            ("2026-08-17", "new_place", "dev2", "31.992,118.783", "同网格", "访问 1 次", 12000),
        ],
    )
    # route_grids
    cur.execute(
        "INSERT INTO route_grids(device_id, day, grid_lat, grid_lon, n_pass) VALUES (?,?,?,?,?)",
        ("dev1", "2026-08-17", 31.992, 118.783, 5),
    )
    # grid_pois
    cur.execute(
        "INSERT INTO grid_pois(grid_lat, grid_lon, name, type, distance, queried_at) VALUES (?,?,?,?,?,?)",
        (31.992, 118.783, "某商圈", "business", "100m", 4000),
    )
    conn.commit()
    yield conn
    conn.close()


def _seed_v2(conn: sqlite3.Connection) -> None:
    """向 v2 表填充有效数据（stays.place_id 全部指向存在的 places_v2.place_id）。"""
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO places_v2(device_id, place_id, grid_key, lat, lon, label, first_seen, last_seen, "
        "point_count, visit_count, stay_ms, is_primary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("dev1", "pid_home", "31.992,118.783", 31.992, 118.783, "家", 1000, 90000, 120, 3, 86000, 1),
            ("dev1", "pid_work", "31.998,118.790", 31.998, 118.790, "公司", 1000, 20000, 80, 1, 11000, 1),
            ("dev2", "pid_home2", "31.992,118.783", 31.992, 118.783, "未知", 1000, 6000, 20, 1, 5000, 0),
        ],
    )
    cur.executemany(
        "INSERT INTO place_cells_v2(device_id, place_id, grid_key) VALUES (?,?,?)",
        [
            ("dev1", "pid_home", "31.992,118.783"),
            ("dev1", "pid_work", "31.998,118.790"),
            ("dev2", "pid_home2", "31.992,118.783"),
        ],
    )
    cur.executemany(
        "INSERT INTO stays_v2(device_id, start_ts, end_ts, duration_ms, center_lat, center_lon, "
        "min_lat, min_lon, max_lat, max_lon, n_points, accuracy_known_points, avg_accuracy_m, radius_m, "
        "grid_key, place_id, source_coord_system, day) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("dev1", 1000, 8000, 7000, 31.992, 118.783, 31.991, 118.782, 31.993, 118.784,
             20, 18, 30.0, 50.0, "31.992,118.783", "pid_home", "wgs84", "2026-08-17"),
            ("dev1", 9000, 20000, 11000, 31.998, 118.790, 31.997, 118.789, 31.999, 118.791,
             30, 28, 25.0, 60.0, "31.998,118.790", "pid_work", "wgs84", "2026-08-17"),
            ("dev1", 21000, 90000, 69000, 31.992, 118.783, 31.991, 118.782, 31.993, 118.784,
             40, 35, 20.0, 50.0, "31.992,118.783", "pid_home", "wgs84", "2026-08-18"),
            ("dev2", 1000, 6000, 5000, 31.992, 118.783, 31.991, 118.782, 31.993, 118.784,
             15, 12, 40.0, 45.0, "31.992,118.783", "pid_home2", "wgs84", "2026-08-17"),
        ],
    )
    cur.executemany(
        "INSERT INTO trips_v2(device_id, start_ts, end_ts, duration_ms, start_lat, start_lon, "
        "end_lat, end_lon, from_place_id, to_place_id, endpoint_coord_system, dist_m, n_points, "
        "day, polyline, route_key, route_mode, route_encoded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("dev1", 8000, 9000, 1000, 31.992, 118.783, 31.998, 118.790, "pid_home", "pid_work",
             "wgs84", 900.0, 3, "2026-08-17", "enc:abc123", "rk_home_work", "driving", 5000),
            ("dev1", 20000, 21000, 1000, 31.998, 118.790, 31.992, 118.783, "pid_work", "pid_home",
             "wgs84", 900.0, 3, "2026-08-18", "enc:def456", "rk_work_home", "driving", 6000),
        ],
    )
    cur.executemany(
        "INSERT INTO place_tag_conflicts_v2(device_id, new_place_id, old_place_id, tag, reason) VALUES (?,?,?,?,?)",
        [("dev1", "pid_home", "31.992,118.783", "家", "merge_survivor")],
    )
    cur.executemany(
        "INSERT INTO anomalies_v2(day, kind, device_id, place_id, grid_key, poi, detail, ts) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("2026-08-17", "new_place", "dev1", "pid_unknown", "31.980,118.800", "某新点", "访问 1 次", 12000),
            ("2026-08-17", "new_place", "dev2", "pid_home2", "31.992,118.783", "同网格", "访问 1 次", 12000),
        ],
    )
    cur.executemany(
        "INSERT INTO route_grids_v2(device_id, day, grid_lat, grid_lon, n_pass) VALUES (?,?,?,?,?)",
        [("dev1", "2026-08-17", 31.992, 118.783, 5)],
    )
    cur.executemany(
        "INSERT INTO grid_pois_v2(grid_lat, grid_lon, name, type, distance, queried_at) VALUES (?,?,?,?,?,?)",
        [(31.992, 118.783, "某商圈", "business", "100m", 4000)],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1) 迁移中 SQL 异常后旧库完全不变
# ---------------------------------------------------------------------------

class TestMigrationAtomicity:
    def test_rollback_on_sql_error_keeps_v1_intact(self, v1_db, monkeypatch):
        lm.create_location_v2_tables(v1_db)
        _seed_v2(v1_db)

        # 备份索引与数据快照
        before_idx = {t: _indexes(v1_db, t) for t in ("places", "stays", "trips", "anomalies", "route_grids", "grid_pois")}
        before_user_version = v1_db.execute("PRAGMA user_version").fetchone()[0]
        before_rows = {
            t: [tuple(r) for r in v1_db.execute(f"SELECT * FROM {t}")]
            for t in ("places", "stays", "trips", "anomalies", "route_grids", "grid_pois")
        }

        real_rename = lm._rename_table

        def boom(conn, old, new):
            if old == "stays" and new == "stays_v1_backup":
                raise sqlite3.OperationalError("simulated SQL failure mid-migration")
            return real_rename(conn, old, new)

        monkeypatch.setattr(lm, "_rename_table", boom)

        with pytest.raises(sqlite3.OperationalError, match="simulated"):
            lm.activate_location_v2(v1_db, "run-atomic")

        # 旧表全部保留原名、数据与索引不变
        for t in ("places", "stays", "trips", "anomalies", "route_grids", "grid_pois"):
            assert t in _tables(v1_db), f"{t} should still exist after rollback"
            assert _indexes(v1_db, t) == before_idx[t], f"{t} indexes changed"
            assert [tuple(r) for r in v1_db.execute(f"SELECT * FROM {t}")] == before_rows[t]
        # 没有残留 backup 表（事务整体回滚）
        assert not any("_v1_backup" in t for t in _tables(v1_db))
        assert v1_db.execute("PRAGMA user_version").fetchone()[0] == before_user_version

    def test_activate_rejects_unvalidated_orphan(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        _seed_v2(v1_db)
        # 制造孤儿：插入一条指向不存在 place 的 stays_v2
        v1_db.execute(
            "INSERT INTO stays_v2(device_id, start_ts, end_ts, duration_ms, center_lat, center_lon, "
            "min_lat, min_lon, max_lat, max_lon, n_points, place_id, source_coord_system) "
            "VALUES ('dev1', 500000, 600000, 100000, 31.9, 118.7, 31.9, 118.7, 31.9, 118.7, 2, 'pid_ghost', 'wgs84')"
        )
        v1_db.commit()
        ok, errors = lm.validate_location_v2(v1_db)
        assert not ok
        assert any("orphan" in e for e in errors)

        with pytest.raises(lm.LocationMigrationError, match="validate_location_v2 failed"):
            lm.activate_location_v2(v1_db, "run-orphan")
        # 旧表未被触碰，user_version 保持原值（新库为 0，未升级到 2）
        assert "places" in _tables(v1_db)
        assert not any("_v1_backup" in t for t in _tables(v1_db))
        assert v1_db.execute("PRAGMA user_version").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 2) 全部 v2 表 + 审计表含东八区时间列
# ---------------------------------------------------------------------------

class TestCstTimestamps:
    def test_all_v2_tables_have_cst_created_updated(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        v2_and_audit = [f"{t}_v2" for t in lm.V2_FACT_TABLES] + list(lm.AUDIT_TABLES)
        for t in v2_and_audit:
            assert t in _tables(v1_db), f"{t} should exist"
            sql = lm._table_sql(v1_db, t)
            assert "created_at" in sql, f"{t} missing created_at"
            assert "updated_at" in sql, f"{t} missing updated_at"
            assert "+8 hours" in sql, f"{t} created_at/updated_at must default to CST (+8 hours)"

    def test_missing_cst_default_is_rejected(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        # 用无东八区默认值的 DDL 重建 places_v2
        v1_db.execute("DROP TABLE places_v2")
        v1_db.execute(
            "CREATE TABLE places_v2 ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, place_id TEXT NOT NULL, "
            "grid_key TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, label TEXT NOT NULL DEFAULT '未知', "
            "created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')), "
            "UNIQUE(device_id, place_id), UNIQUE(device_id, grid_key))"
        )
        v1_db.commit()
        ok, errors = lm.validate_location_v2(v1_db)
        assert not ok
        assert any("+8 hours" in e and "places_v2" in e for e in errors)


# ---------------------------------------------------------------------------
# 3) 唯一键 / 索引一致性 + 孤儿校验
# ---------------------------------------------------------------------------

class TestSchemaConsistency:
    def test_uniques_match_section_2_2(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        ok, errors = lm.validate_location_v2(v1_db)
        assert ok, errors
        for t, uniques in lm.EXPECTED_UNIQUES.items():
            sql = lm._table_sql(v1_db, t)
            for u in uniques:
                assert u in sql, f"{t} missing unique/pk ({u})"

    def test_indexes_match_section_2_2(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        for t, idxs in lm.EXPECTED_INDEXES.items():
            actual = _indexes(v1_db, t)
            for idx in idxs:
                assert idx in actual, f"{t} missing index {idx}"

    def test_validate_passes_on_clean_v2(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        _seed_v2(v1_db)
        ok, errors = lm.validate_location_v2(v1_db)
        assert ok, errors

    def test_orphan_stays_blocks_switch(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        _seed_v2(v1_db)
        v1_db.execute(
            "INSERT INTO stays_v2(device_id, start_ts, end_ts, duration_ms, center_lat, center_lon, "
            "min_lat, min_lon, max_lat, max_lon, n_points, place_id, source_coord_system) "
            "VALUES ('dev1', 700000, 800000, 100000, 31.9, 118.7, 31.9, 118.7, 31.9, 118.7, 2, 'nope', 'wgs84')"
        )
        v1_db.commit()
        ok, errors = lm.validate_location_v2(v1_db)
        assert not ok
        assert any("orphan" in e for e in errors)


# ---------------------------------------------------------------------------
# 4) activate 成功：v1 备份齐全 + user_version=2 + pending 状态
# ---------------------------------------------------------------------------

class TestActivate:
    def test_activate_success(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        _seed_v2(v1_db)
        lm.activate_location_v2(v1_db, "run-ok", pending_labels_path="data/place_labels.json.v3.pending")

        assert v1_db.execute("PRAGMA user_version").fetchone()[0] == 2
        # 六张 v1 业务表备份齐全
        for t in lm.V1_FACT_TABLES:
            assert f"{t}_v1_backup" in _tables(v1_db), f"missing {t}_v1_backup"
        # 正式表为 v2 结构（含 place_id）
        cols = {r[1] for r in v1_db.execute("PRAGMA table_info(places)")}
        assert "place_id" in cols
        assert "place_cells" in _tables(v1_db)
        # pending 状态落库
        st = lm.read_migration_state(v1_db)
        assert st is not None
        assert st["status"] == "pending_label_swap"
        assert st["schema_version"] == 2
        assert st["pending_labels_path"] == "data/place_labels.json.v3.pending"

    def test_activate_uses_begin_immediate(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        _seed_v2(v1_db)
        calls: list[str] = []
        # sqlite3.Connection.execute 为只读属性，无法 monkeypatch；用 trace callback 捕获 SQL。
        v1_db.set_trace_callback(lambda sql: calls.append(sql or ""))
        lm.activate_location_v2(v1_db, "run-begin")
        v1_db.set_trace_callback(None)
        assert any(c.strip().upper().startswith("BEGIN IMMEDIATE") for c in calls)

    def test_recover_from_pending_after_crash(self, v1_db):
        """DB 已提交（user_version=2 / pending_label_swap），但标签文件替换前“崩溃”。

        重新打开连接后，可从 migration_state 读到 pending 路径恢复（Task 4 落地文件替换）。
        """
        lm.create_location_v2_tables(v1_db)
        _seed_v2(v1_db)
        lm.activate_location_v2(v1_db, "run-crash", pending_labels_path="data/place_labels.json.v3.pending")
        # 模拟崩溃：不写标签文件直接关连接
        v1_db.commit()
        # 重新打开（此处内存库重开一个连接看状态，模拟重启）
        conn2 = sqlite3.connect(":memory:")
        conn2.row_factory = sqlite3.Row
        conn2.executescript(lm.SCHEMA_V2)
        # 从备份库投影恢复：将 pending 状态拷贝到新连接
        src = v1_db.execute(
            "SELECT run_id, schema_version, status, pending_labels_path, error FROM location_migration_state WHERE id=1"
        ).fetchone()
        conn2.execute(
            "INSERT INTO location_migration_state(id, run_id, schema_version, status, pending_labels_path, error) "
            "VALUES (1,?,?,?,?,?)",
            (src[0], src[1], src[2], src[3], src[4]),
        )
        conn2.commit()
        st = lm.read_migration_state(conn2)
        assert st is not None
        assert st["status"] == "pending_label_swap"
        assert st["pending_labels_path"] == "data/place_labels.json.v3.pending"
        conn2.close()


# ---------------------------------------------------------------------------
# 5) rollback：六张业务表 + 索引 + polyline 缓存全部恢复
# ---------------------------------------------------------------------------

class TestRollback:
    def test_rollback_restores_tables_indexes_and_route_cache(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        _seed_v2(v1_db)
        lm.activate_location_v2(v1_db, "run-rb")
        assert v1_db.execute("PRAGMA user_version").fetchone()[0] == 2
        # 记录 v2 下 trips 缓存（应保留在 failed 快照，不回正式表）
        lm.rollback_location_v2(v1_db, "run-rb")

        assert v1_db.execute("PRAGMA user_version").fetchone()[0] == 1
        st = lm.read_migration_state(v1_db)
        assert st["status"] == "rolled_back"
        # 六张业务表恢复正式名
        for t in lm.V1_FACT_TABLES:
            assert t in _tables(v1_db), f"{t} not restored"
            assert f"{t}_v1_backup" not in _tables(v1_db)
            assert f"{t}_failed_v2_run-rb" in _tables(v1_db), f"{t}_failed_v2_run-rb missing"
        # 索引恢复（v1 原生索引仍存在）
        assert "idx_places_device" in _indexes(v1_db, "places")
        assert "idx_stays_device" in _indexes(v1_db, "stays")
        assert "idx_trips_device" in _indexes(v1_db, "trips")
        # polyline/route_key 缓存不丢失
        rows = [tuple(r) for r in v1_db.execute("SELECT device_id, start_ts, polyline, route_key FROM trips")]
        assert len(rows) == 2
        assert {r[2] for r in rows} == {"enc:abc123", "enc:def456"}
        assert {r[3] for r in rows} == {"rk_home_work", "rk_work_home"}
        # 原始 v1 数据还原
        places = [tuple(r) for r in v1_db.execute("SELECT grid_key, label FROM places WHERE device_id='dev1'")]
        assert ("31.992,118.783", "家") in places
        assert ("31.998,118.790", "公司") in places

    def test_rollback_requires_backups(self, v1_db):
        lm.create_location_v2_tables(v1_db)
        _seed_v2(v1_db)
        with pytest.raises(lm.LocationMigrationError, match="missing v1 backups"):
            lm.rollback_location_v2(v1_db, "run-nobackup")


# ---------------------------------------------------------------------------
# 6) activate/rollback 内部无 geocode 外呼 / 文件写入
# ---------------------------------------------------------------------------

class TestNoIoInsideTransaction:
    def test_no_geocode_or_file_io_in_transaction_functions(self):
        src = _func_body_source(lm.activate_location_v2) + _func_body_source(lm.rollback_location_v2)
        assert "geocode" not in src
        assert "open(" not in src
        assert ".write" not in src
        assert "requests" not in src
