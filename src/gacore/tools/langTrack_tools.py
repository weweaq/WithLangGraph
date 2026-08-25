"""langTrack data tools for gacore: surface phone usage signals into daily-report.



外挂式设计（高内聚/低耦合）：

- 只读 langTrack 服务端的 `data/langTrack.db`（ETL 产出的事实表），不触碰其他模块

- 需要最新数据时自动触发 ETL（幂等，几秒），再读事实表

- 返回结构化信号字典，由 agent 决定如何融入日报，本模块不做分析

- 不依赖 scheduler / graph / 其他 tools；注册只需在 tools/__init__.py 加一行

"""

from __future__ import annotations



import json

import sqlite3

import subprocess

import sys

from pathlib import Path

from typing import Final, TypedDict



from langchain_core.tools import tool



from gacore.jsonl_logger import get_logger
from gacore.langTrack.persona import build as build_persona



logger = get_logger("tools.langTrack_tools")



# langTrack 服务端仓库根（data/langTrack.db 所在）；通过环境变量可覆盖（测试用）

_LANGTRACK_ROOT_ENV: Final = "LANGTRACK_ROOT"

_DEFAULT_ROOT: Final = Path(__file__).resolve().parents[3]  # WithLangGraph 仓库根



_ETL_TIMEOUT_SECONDS: Final = 120





def _root() -> Path:

    override = __import__("os").environ.get(_LANGTRACK_ROOT_ENV)

    return Path(override) if override else _DEFAULT_ROOT





def _db_path() -> Path:

    return _root() / "data" / "langTrack.db"





def _ensure_etl() -> bool:

    """ETL 幂等重建事实表（新数据落库后调用，保证读的是最新）。失败不阻塞。"""

    try:

        subprocess.run(

            [sys.executable, "-m", "gacore.langTrack.etl"],

            cwd=str(_root()),

            capture_output=True,

            timeout=_ETL_TIMEOUT_SECONDS,

            check=False,

        )

        return True

    except Exception as e:  # noqa: BLE001 - 工具错误路径：返回失败不抛

        logger.warning("langTrack ETL failed", error_type=type(e).__name__, error=str(e))

        return False





class LangTrackDayStats(TypedDict):

    """某一天的结构化使用画像（来自 daily_stats + sessions + places 事实表）。"""



    day: str

    available: bool

    screen_ms: int

    screen_hours: float

    top_apps: list[dict]

    notification_count: int

    notification_clicked: int

    unlock_count: int

    switch_count: int

    location_count: int

    places: list[dict]

    sleep_signal: str

    # P0 拟人语义字段（v2 设计文档附录 A；旧库未跑派生时为 None/[]，下游需做空值防御）
    # 作息节律三标量来自 daily_stats P0 列；sleep_signal 为遗留兼容信号，保留但以本组为准
    sleep_start_hhmm: int | None
    sleep_end_hhmm: int | None
    sleep_duration_min: int | None
    time_app: list[dict]  # 时段×应用矩阵（daily_stats.time_app_json 解析后）

    # A① 契约覆盖：非 ok 的期望事件类型清单（缺失/停滞/未登记）
    coverage: list[dict]
    persona: dict


def _row_val(row, col: str, default=None):
    """安全读取 sqlite3.Row 列；旧库缺列（P0 派生未跑）时返回 default，不抛异常。"""
    if row is None:
        return default
    try:
        v = row[col]
        return default if v is None else v
    except (KeyError, IndexError):
        return default


def _today() -> str:

    import datetime

    return datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y-%m-%d")





@tool

def langTrack_stats(day: str = "") -> dict:

    """读取某天（默认今天）的手机使用数据画像：屏幕时长、App 排行、通知、睡眠信号、场景。



    数据来自 langTrack 采集链路（weiCheckApp 手机端 → /ingest → ETL 事实表）。

    返回结构化信号供日报交叉分析；无数据时 available=False。

    """

    day = day or _today()

    # 确保事实表最新（新数据落库后自动重建）

    _ensure_etl()



    db = _db_path()

    if not db.exists():
        return LangTrackDayStats(day=day, available=False, screen_ms=0, screen_hours=0.0,
                                top_apps=[], notification_count=0, notification_clicked=0,
                                unlock_count=0, switch_count=0, location_count=0,
                                places=[], sleep_signal="langTrack 数据库不存在",
                                sleep_start_hhmm=None, sleep_end_hhmm=None,
                                sleep_duration_min=None, time_app=[],
                                coverage=[], persona={})


    try:

        conn = sqlite3.connect(db)

        conn.row_factory = sqlite3.Row

        stat = conn.execute(

            "SELECT * FROM daily_stats WHERE day=?", (day,)

        ).fetchone()

        if not stat:
            conn.close()
            return LangTrackDayStats(day=day, available=False, screen_ms=0, screen_hours=0.0,
                                    top_apps=[], notification_count=0, notification_clicked=0,
                                    unlock_count=0, switch_count=0, location_count=0,
                                    places=[], sleep_signal="当日无数据（可能未采集或未同步）",
                                    sleep_start_hhmm=None, sleep_end_hhmm=None,
                                    sleep_duration_min=None, time_app=[],
                                    coverage=[], persona={})


        ranking = json.loads(stat["app_ranking_json"] or "[]")

        places = conn.execute(

            "SELECT label, visit_count FROM places ORDER BY visit_count DESC LIMIT 4"

        ).fetchall()



        # 睡眠信号：凌晨 00-05 点环境音频样本数（粗略，沿用 report.py 逻辑）

        midnight_audio = conn.execute(

            "SELECT COUNT(*) c FROM events WHERE type='audio_env' "

            "AND date(ts/1000,'unixepoch','+8 hours')=? "

            "AND strftime('%H', ts/1000,'unixepoch','+8 hours') BETWEEN '00' AND '05'",

            (day,),

        ).fetchone()["c"]

        sleep_signal = "凌晨 00-05 点仍有环境音频样本，疑似熬夜" if midnight_audio > 5 else "未见熬夜信号"

        # A① 契约覆盖：取非 ok 的类型（缺失/停滞/未登记），供日报输出采集缺口
        coverage: list[dict] = []
        try:
            cov_rows = conn.execute(
                "SELECT type, desc, status, last_seen_ts, consumed FROM contract_coverage "
                "WHERE status != 'ok' ORDER BY status, type"
            ).fetchall()
            coverage = [
                {
                    "type": r["type"],
                    "desc": r["desc"],
                    "status": r["status"],
                    "last_seen": r["last_seen_ts"],
                    "consumed": r["consumed"],
                }
                for r in cov_rows
            ]
        except sqlite3.OperationalError:
            # contract_coverage 表尚未建立（旧库未重跑 ETL）时不阻塞
            coverage = []

        # P0 时段×应用矩阵：daily_stats.time_app_json（TEXT JSON）解析为 list；
        # 旧库未跑 P0 派生（无该列）或值为空 → []
        _time_app = []
        _raw_ta = _row_val(stat, "time_app_json")
        if _raw_ta:
            try:
                _time_app = json.loads(_raw_ta)
            except (TypeError, ValueError):
                _time_app = []
        if not isinstance(_time_app, list):
            _time_app = []

        conn.close()

        return LangTrackDayStats(
            day=day,

            available=True,

            screen_ms=stat["total_screen_ms"],

            screen_hours=round(stat["total_screen_ms"] / 3600000, 2),

            top_apps=ranking[:8],

            notification_count=stat["notification_count"],

            notification_clicked=stat["notification_clicked"],

            unlock_count=stat["unlock_count"],

            switch_count=stat["switch_count"],

            location_count=stat["location_count"],

            places=[{"label": p["label"], "visits": p["visit_count"]} for p in places],

            sleep_signal=sleep_signal,

            sleep_start_hhmm=_row_val(stat, "sleep_start_hhmm"),
            sleep_end_hhmm=_row_val(stat, "sleep_end_hhmm"),
            sleep_duration_min=_row_val(stat, "sleep_duration_min"),
            time_app=_time_app,

            coverage=coverage,
            persona=build_persona(db_path=str(db), days=7),

        )

    except Exception as e:  # noqa: BLE001

        logger.warning("langTrack_stats failed", error_type=type(e).__name__, error=str(e))

        return LangTrackDayStats(day=day, available=False, screen_ms=0, screen_hours=0.0,

                                top_apps=[], notification_count=0, notification_clicked=0,

                                unlock_count=0, switch_count=0, location_count=0,

                                places=[], sleep_signal=f"读取失败: {e}",
                                sleep_start_hhmm=None, sleep_end_hhmm=None,
                                sleep_duration_min=None, time_app=[],
                                coverage=[], persona={})

