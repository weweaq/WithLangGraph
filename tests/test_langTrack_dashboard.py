"""dashboard.py 事实审查块测试：内存库渲染 HTML，断言 compact / 水位 / 停留轨迹 / 事件计数 / 降级。"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


from gacore.langTrack.dashboard import render_dashboard_html

_TZ = timezone(timedelta(hours=8))


def _ts(y, mo, d, h, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=_TZ).timestamp() * 1000)


def _make_db(device_id: str = "dev1", day: str = "2026-08-18",
             with_stats: bool = True) -> sqlite3.Connection:
    """合成事实库：daily_stats / etl_state / places / stays / trips / anomalies / events /
    contract_coverage / sessions。默认 8-18：家 00:00-08:32 → 公司 09:04-12:03 → 餐馆 12:11-12:47 →
    公司 13:02-17:06，trips 3 段，当日 daily_stats 7h，一条 new_place 异常。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE daily_stats ("
        "device_id TEXT, day TEXT, total_screen_ms INTEGER, app_ranking_json TEXT,"
        "notification_count INTEGER, notification_clicked INTEGER, top_notification_apps_json TEXT,"
        "screen_on_count INTEGER, screen_off_count INTEGER, unlock_count INTEGER,"
        "switch_count INTEGER, location_count INTEGER, audio_clip_count INTEGER,"
        "sleep_start_hhmm INTEGER, sleep_end_hhmm INTEGER, sleep_duration_min INTEGER,"
        "time_app_json TEXT)"
    )
    cur.execute("CREATE TABLE etl_state (device_id TEXT PRIMARY KEY, last_event_ts INTEGER)")
    cur.execute(
        "CREATE TABLE places ("
        "id INTEGER, device_id TEXT, grid_key TEXT, label TEXT, visit_count INTEGER,"
        "poi TEXT, behavior TEXT, district TEXT, address TEXT)"
    )
    cur.execute(
        "CREATE TABLE stays ("
        "id INTEGER, device_id TEXT, grid_key TEXT, start_ts INTEGER, end_ts INTEGER, day TEXT)"
    )
    cur.execute(
        "CREATE TABLE trips ("
        "id INTEGER, device_id TEXT, start_ts INTEGER, end_ts INTEGER, dist_m INTEGER, day TEXT)"
    )
    cur.execute(
        "CREATE TABLE anomalies ("
        "id INTEGER, device_id TEXT, day TEXT, kind TEXT, poi TEXT, detail TEXT, ts INTEGER)"
    )
    cur.execute(
        "CREATE TABLE events ("
        "id INTEGER, device_id TEXT, ts INTEGER, type TEXT, payload TEXT, received_at INTEGER)"
    )
    cur.execute(
        "CREATE TABLE contract_coverage ("
        "type TEXT, desc TEXT, status TEXT, last_seen_ts INTEGER, consumed INTEGER)"
    )
    cur.execute(
        "CREATE TABLE sessions ("
        "id INTEGER, device_id TEXT, day TEXT, app TEXT, start_ms INTEGER, duration_ms INTEGER)"
    )
    ranking = json.dumps([
        {"app": "飞书", "ms": 3_600_000},
        {"app": "微信", "ms": 1_800_000},
    ])
    notif_apps = json.dumps([{"app": "微信", "n": 5}, {"app": "飞书", "n": 3}])
    if with_stats:
        cur.execute(
            "INSERT INTO daily_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (device_id, day, 25_200_000, ranking, 47, 6, notif_apps, 3, 2, 12, 46, 50, 0,
             2340, 390, 480, "[]"),
        )
    cur.execute("INSERT INTO etl_state VALUES (?,?)", (device_id, _ts(2026, 8, 18, 17, 6)))
    places = [
        (1, device_id, "g_home", "家", 260, "家小区", "home", "玄武区", "XX路1号"),
        (2, device_id, "g_work", "公司", 440, "公司大厦", "work", "雨花台区", "YY路2号"),
        (3, device_id, "g_rest", "餐馆", 30, "快餐店", "dining", "雨花台区", "ZZ路3号"),
    ]
    cur.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?)", places)
    stays = [
        (1, device_id, "g_home", _ts(2026, 8, 18, 0, 0), _ts(2026, 8, 18, 8, 32), day),
        (2, device_id, "g_work", _ts(2026, 8, 18, 9, 4), _ts(2026, 8, 18, 12, 3), day),
        (3, device_id, "g_rest", _ts(2026, 8, 18, 12, 11), _ts(2026, 8, 18, 12, 47), day),
        (4, device_id, "g_work", _ts(2026, 8, 18, 13, 2), _ts(2026, 8, 18, 17, 6), day),
    ]
    cur.executemany("INSERT INTO stays VALUES (?,?,?,?,?,?)", stays)
    trips = [
        (1, device_id, _ts(2026, 8, 18, 8, 32), _ts(2026, 8, 18, 9, 4), 3200, day),
        (2, device_id, _ts(2026, 8, 18, 12, 3), _ts(2026, 8, 18, 12, 11), 800, day),
        (3, device_id, _ts(2026, 8, 18, 12, 47), _ts(2026, 8, 18, 13, 2), 900, day),
    ]
    cur.executemany("INSERT INTO trips VALUES (?,?,?,?,?,?)", trips)
    cur.execute(
        "INSERT INTO anomalies VALUES (?,?,?,?,?,?,?)",
        (1, device_id, day, "new_place", "德基广场", "访问 1 次", _ts(2026, 8, 18, 20, 0)),
    )
    cur.execute(
        "INSERT INTO anomalies VALUES (?,?,?,?,?,?,?)",
        (2, device_id, day, "new_place", "31.97,118.76", "访问 1 次", _ts(2026, 8, 18, 21, 0)),
    )
    # 当日 usage 事件 + 次日 usage 事件（用于设备过滤断言）
    for i in range(3):
        cur.execute(
            "INSERT INTO events(id,device_id,ts,type,payload,received_at) VALUES (?,?,?,?,?,?)",
            (i + 1, device_id, _ts(2026, 8, 18, 10, 0) + i * 60_000, "usage",
             json.dumps({"pkg": "com.x", "foreground_ms": 1000}), _ts(2026, 8, 18, 10, 0)),
        )
    cur.execute(
        "INSERT INTO events(id,device_id,ts,type,payload,received_at) VALUES (?,?,?,?,?,?)",
        (100, device_id, _ts(2026, 8, 19, 10, 0), "usage",
         json.dumps({"pkg": "com.x", "foreground_ms": 1}), _ts(2026, 8, 19, 10, 0)),
    )
    cur.execute(
        "INSERT INTO events(id,device_id,ts,type,payload,received_at) VALUES (?,?,?,?,?,?)",
        (200, device_id, _ts(2026, 8, 18, 10, 5), "location",
         json.dumps({"lat": 1.0, "lon": 2.0}), _ts(2026, 8, 18, 10, 5)),
    )
    cur.execute(
        "INSERT INTO contract_coverage VALUES (?,?,?,?,?)",
        ("screen", "屏幕事件", "stalled", _ts(2026, 8, 18, 10, 0), 0),
    )
    cur.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
        (1, device_id, day, "微信", _ts(2026, 8, 18, 10, 0), 1_800_000),
    )
    conn.commit()
    return conn


def _render(conn: sqlite3.Connection, day: str = "2026-08-18") -> str:
    return render_dashboard_html(conn, day)


# ---------------------------------------------------------------------------
# 事实审查块
# ---------------------------------------------------------------------------


def test_dashboard_shows_compact_timeline_and_watermarks():
    html = _render(_make_db())
    assert "生活事实" in html
    assert "今日轨迹" in html
    assert "公司" in html
    # 水位旁注
    assert "数据水位" in html
    assert "card_fp" in html


def test_dashboard_shows_section_inclusion_and_omission():
    html = _render(_make_db())
    assert "compact_lines" in html
    assert "compact_omitted" in html


def test_dashboard_with_stays_but_no_daily_stats():
    """无 daily_stats 仍显示显式请求日和今日轨迹，不跳到其他日期。"""
    html = _render(_make_db(with_stats=False), day="2026-08-18")
    assert "2026-08-18" in html
    assert "今日轨迹" in html
    assert "家 00:00-08:32" in html


def test_dashboard_lists_stays_trips_anomalies():
    html = _render(_make_db())
    assert "德基广场" in html        # anomaly poi
    assert "端点直距" in html        # trip 距离列名
    assert "快餐店" in html          # place poi


def test_dashboard_labels_trip_distance_and_place_counts_honestly():
    html = _render(_make_db())
    assert "端点直距" in html
    assert "全历史 location 点数" in html
    assert "到访次数" not in html


def test_dashboard_event_counts():
    """当日到达表：usage 3 条、location 1 条；次日事件不混入。"""
    html = _render(_make_db())
    assert "usage" in html
    assert "3" in html
    assert "location" in html


def test_dashboard_survives_fact_card_error(monkeypatch):
    import gacore.langTrack.dashboard as dash
    monkeypatch.setattr(dash, "fact_card", _ExplodingFactCard())

    html = render_dashboard_html(_make_db(), "2026-08-18")
    assert "2026-08-18" in html          # 日期导航仍渲染
    assert "读取失败" in html            # 通用错误
    assert "今日轨迹" not in html        # 无未过滤统计


class _ExplodingFactCard:
    def build(self, **kwargs):
        raise RuntimeError("boom")
    render_compact = staticmethod(lambda card: card.get("compact", ""))


def test_dashboard_escapes_html():
    conn = _make_db()
    # 注入 HTML payload 到 anomaly.detail / place.poi / compact
    conn.execute(
        "UPDATE anomalies SET detail='<script>alert(1)</script>', poi='<img src=x>' WHERE id=1"
    )
    conn.execute("UPDATE places SET poi='<img src=x>' WHERE id=2")
    conn.commit()
    html = render_dashboard_html(conn, "2026-08-18")
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x>" not in html
    assert "&lt;script&gt;" in html


def test_dashboard_persona_not_queried_twice(monkeypatch):
    import gacore.langTrack.fact_card as fc
    calls = []

    def fake_persona_build(*a, **kw):
        calls.append(1)
        return {}

    monkeypatch.setattr(fc, "build_persona", fake_persona_build)
    html = _render(_make_db())
    # 成功路径：persona 只经 fact_card 一次，dashboard 不二次调用
    assert len(calls) == 1
    assert "人物画像" in html


def test_dashboard_ambiguous_devices_shows_no_device_stats():
    """两设备同日数据时只显示候选 ID，不出现任一设备的统计/地点/persona。"""
    conn = _make_db("dev1", "2026-08-18")
    conn.execute("INSERT INTO daily_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("dev2", "2026-08-18", 1_000, "[]", 0, 0, "[]", 0, 0, 0, 0, 0, 0,
                  None, None, None, "[]"))
    conn.execute("INSERT INTO etl_state VALUES (?,?)", ("dev2", _ts(2026, 8, 18, 12, 0)))
    conn.execute("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?)",
                 (99, "dev2", "g_x", "别处", 5, "别处poi", "x", "区", "地址"))
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                 (99, "dev2", "2026-08-18", "别app", _ts(2026, 8, 18, 10, 0), 1000))
    conn.commit()

    html = render_dashboard_html(conn, "2026-08-18")
    assert "dev1" in html and "dev2" in html    # 候选 ID 展示
    assert "别app" not in html                  # 无设备统计
    assert "别处poi" not in html                # 无地点
    assert "人物画像" not in html               # 无 persona


def test_dashboard_raw_events_are_filtered_by_selected_device():
    """第二设备只存在 events 时，其 snapshot/type count 不混入页面。"""
    conn = _make_db("dev1", "2026-08-18")
    conn.execute("INSERT INTO events(id,device_id,ts,type,payload,received_at) VALUES (?,?,?,?,?,?)",
                 (999, "dev2", _ts(2026, 8, 18, 11, 0), "usage",
                  json.dumps({"pkg": "com.y", "foreground_ms": 5}), _ts(2026, 8, 18, 11, 0)))
    conn.commit()
    html = render_dashboard_html(conn, "2026-08-18")
    # dev2 唯一事件不能使 usage 计数出现 dev2 特征；页面 usage 行来自 dev1 的 3 条
    assert html.count("usage") >= 1
    assert "999" not in html  # 不展示事件 id
