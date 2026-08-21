"""Tests for gacore.tools.langTrack_tools — fully mocked, no real DB / ETL / network."""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path

import gacore.tools.langTrack_tools as langTrack_mod

# Ensure we have the real module (not the StructuredTool shadowed in __init__.py).
if not isinstance(langTrack_mod, types.ModuleType):
    langTrack_mod = sys.modules["gacore.tools.langTrack_tools"]

from gacore.tools.langTrack_tools import langTrack_stats


def _make_db(root: Path, day: str, with_stats: bool = True) -> None:
    """Build a minimal langTrack.db with daily_stats / places / events for a day."""
    db = root / "data" / "langTrack.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE daily_stats (
          day TEXT PRIMARY KEY,
          total_screen_ms INTEGER, app_ranking_json TEXT,
          notification_count INTEGER, notification_clicked INTEGER,
          top_notification_apps_json TEXT,
          screen_on_count INTEGER, screen_off_count INTEGER,
          unlock_count INTEGER, switch_count INTEGER,
          location_count INTEGER, audio_clip_count INTEGER
        );
        CREATE TABLE places (
          id INTEGER PRIMARY KEY, device_id TEXT, grid_key TEXT,
          lat REAL, lon REAL, label TEXT,
          first_seen INTEGER, last_seen INTEGER, visit_count INTEGER,
          is_primary INTEGER
        );
        CREATE TABLE events (
          id INTEGER PRIMARY KEY, device_id TEXT, ts INTEGER,
          type TEXT, payload TEXT, received_at INTEGER,
          created_at TEXT, updated_at TEXT
        );
        """
    )
    if with_stats:
        conn.execute(
            "INSERT INTO daily_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                day, 7_000_000,
                json.dumps([{"app": "微信", "ms": 2_000_000},
                            {"app": "飞书", "ms": 1_800_000}], ensure_ascii=False),
                10, 1, json.dumps([{"app": "微信", "n": 5}], ensure_ascii=False),
                3, 2, 3, 100, 50, 0,
            ),
        )
    conn.execute(
        "INSERT INTO places VALUES (1,'d','31.975,118.767',31.975,118.767,'公司',0,0,440,1)"
    )
    conn.execute(
        "INSERT INTO places VALUES (2,'d','31.993,118.783',31.993,118.783,'家',0,0,260,1)"
    )
    # 凌晨音频样本：6 条 → 触发"疑似熬夜"信号（2026-08-18 00:00 起，东八区）
    import datetime
    tz = datetime.timezone(datetime.timedelta(hours=8))
    day_start = int(datetime.datetime(2026, 8, 18, 0, 0, tzinfo=tz).timestamp() * 1000)
    for i in range(6):
        conn.execute(
            "INSERT INTO events(id,device_id,ts,type,payload,received_at) VALUES (?,?,?,?,?,?)",
            (i + 1, "d", day_start + i * 60_000, "audio_env",
             json.dumps({"is_silent": True}), day_start),
        )
    conn.commit()
    conn.close()


def test_registered_in_tool_names():
    """工具必须在 registry 中注册（外挂式：import + TOOL_NAMES + _TOOLS 三处）。"""
    from gacore.tools import TOOL_NAMES, build_tool_list
    assert "langTrack_stats" in TOOL_NAMES
    names = [t.name for t in build_tool_list(None)]
    assert "langTrack_stats" in names


def test_stats_returns_day_profile(monkeypatch, tmp_path):
    """正常路径：返回结构化画像。"""
    _make_db(tmp_path, "2026-08-18", with_stats=True)
    monkeypatch.setattr(langTrack_mod, "_DEFAULT_ROOT", tmp_path)
    # 跳过真实 ETL（子进程）
    monkeypatch.setattr(langTrack_mod, "_ensure_etl", lambda: True)

    r = langTrack_stats.invoke({"day": "2026-08-18"})
    assert r["available"] is True
    assert r["day"] == "2026-08-18"
    assert r["screen_hours"] == round(7_000_000 / 3600000, 2)
    assert r["top_apps"][0]["app"] == "微信"
    assert r["notification_count"] == 10
    assert "疑似熬夜" in r["sleep_signal"]  # 6 条凌晨音频样本触发
    assert r["places"][0]["label"] == "公司"


def test_stats_no_data_returns_unavailable(monkeypatch, tmp_path):
    """无当日数据：available=False + 说明。"""
    _make_db(tmp_path, "2026-08-18", with_stats=False)
    monkeypatch.setattr(langTrack_mod, "_DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(langTrack_mod, "_ensure_etl", lambda: True)

    r = langTrack_stats.invoke({"day": "2026-08-18"})
    assert r["available"] is False
    assert "无数据" in r["sleep_signal"]


def test_stats_missing_db_returns_unavailable(monkeypatch, tmp_path):
    """数据库不存在：available=False，不抛异常。"""
    monkeypatch.setattr(langTrack_mod, "_DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(langTrack_mod, "_ensure_etl", lambda: True)

    r = langTrack_stats.invoke({"day": "2026-08-18"})
    assert r["available"] is False
    assert "不存在" in r["sleep_signal"]


def test_stats_etl_failure_does_not_block(monkeypatch, tmp_path):
    """ETL 失败不阻塞：返回旧数据或 unavailable。"""
    _make_db(tmp_path, "2026-08-18", with_stats=True)
    monkeypatch.setattr(langTrack_mod, "_DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(langTrack_mod, "_ensure_etl", lambda: False)

    r = langTrack_stats.invoke({"day": "2026-08-18"})
    assert r["available"] is True  # ETL 失败但仍读到旧事实表


def test_stats_db_error_returns_message(monkeypatch, tmp_path):
    """DB 读取异常：返回错误信息而非抛出。"""
    monkeypatch.setattr(langTrack_mod, "_DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(langTrack_mod, "_ensure_etl", lambda: True)
    # 建一个损坏的 db 文件
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "langTrack.db").write_bytes(b"not a database")

    r = langTrack_stats.invoke({"day": "2026-08-18"})
    assert r["available"] is False
    assert "读取失败" in r["sleep_signal"]
