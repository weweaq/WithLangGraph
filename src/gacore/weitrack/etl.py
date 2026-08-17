"""weiTrack ETL：把原始事件流清洗并加工成可分析的事实表。

产出三张表：
- sessions    前台会话（usage + session 事件拼接，含 app/起止/时长/activity）
- daily_stats 按天汇总（总时长/app 排行/通知统计/亮屏与锁屏次数）
- places      常驻点（位置网格聚类 + wifi/bt 佐证）

清洗规则（针对实测脏数据）：
- ts < 1e12 的事件丢弃（Android AccessibilityEvent 偶发 timeStamp=0）
- 系统 App 丢弃（launcher/systemui/输入法/自家 App 等，避免噪音污染排行）
- usage foreground_ms <= 0 丢弃
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "weitrack.db"

# 与客户端 UsageRepository.SYSTEM_PACKAGES 对齐 + 实测新增噪音
SYSTEM_PACKAGES = {
    "android",
    "com.android.systemui",
    "com.android.launcher3",
    "com.android.launcher",  # OPPO/ColorOS 实际桌面包名（实测漏网）
    "com.oppo.launcher",
    "com.oplus.launcher",
    "com.oppo.safe",
    "com.coloros.safecenter",
    "com.google.android.inputmethod.latin",
    "com.sohu.inputmethod.sogou",
    "com.baidu.input",
    "com.iflytek.inputmethod",
    "com.android.settings",
    "com.oplus.settings",
    "com.coloros.phonemanager",
    "com.heytap.mcs",
    "com.oplus.battery",
    # 实测系统组件（OPPO/ColorOS/Android 平台），全部为噪音
    "com.android.permissioncontroller",
    "com.android.packageinstaller",
    "com.android.intentresolver",
    "com.android.photopicker",
    "com.android.wifi.dialog",
    "com.android.mms",
    "com.oplus.securitypermission",
    "com.oplus.wirelesssettings",
    "com.oplus.camera",
    "com.oplus.aiwriter",
    "com.oplus.appdetail",
    "com.oplus.melody",
    "com.oplus.screenrecorder",
    "com.oplus.notificationmanager",
    "com.oplus.viewtalk",
    "com.oplus.trafficmonitor",
    "com.oplus.safecenter",
    "com.oplus.linker",
    "com.coloros.digitalwellbeing",
    "com.coloros.gallery3d",
    "com.coloros.codebook",
    "com.coloros.familyguard",
    "com.heytap.browser",
    "com.heytap.market",
    "com.heytap.quicksearchbox",
    "com.heytap.quicksearchbox",
}
# 自家 App 不进使用统计
OWN_PACKAGES = {"com.wei.checkapp"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  day TEXT NOT NULL,
  pkg TEXT NOT NULL,
  app TEXT NOT NULL,
  activity TEXT,
  start_ms INTEGER NOT NULL,
  end_ms INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day);
CREATE INDEX IF NOT EXISTS idx_sessions_pkg ON sessions(pkg);

CREATE TABLE IF NOT EXISTS daily_stats (
  day TEXT PRIMARY KEY,
  total_screen_ms INTEGER NOT NULL DEFAULT 0,
  app_ranking_json TEXT,
  notification_count INTEGER NOT NULL DEFAULT 0,
  notification_clicked INTEGER NOT NULL DEFAULT 0,
  top_notification_apps_json TEXT,
  screen_on_count INTEGER NOT NULL DEFAULT 0,
  screen_off_count INTEGER NOT NULL DEFAULT 0,
  unlock_count INTEGER NOT NULL DEFAULT 0,
  switch_count INTEGER NOT NULL DEFAULT 0,
  location_count INTEGER NOT NULL DEFAULT 0,
  audio_clip_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS places (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  grid_key TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  label TEXT DEFAULT '未知',
  first_seen INTEGER,
  last_seen INTEGER,
  visit_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(device_id, grid_key)
);
CREATE INDEX IF NOT EXISTS idx_places_device ON places(device_id);
"""


def clean_ts(ts: int) -> bool:
    """时间戳有效性：毫秒级且不为 0/负（过滤无障碍偶发脏数据）。"""
    return ts > 1_000_000_000_000


def load_events(conn: sqlite3.Connection) -> list[tuple[int, str, dict]]:
    """读取全部事件（device_id, ts, type, payload dict），跳过坏 ts。"""
    rows = conn.execute("SELECT device_id, ts, type, payload FROM events").fetchall()
    out = []
    for device_id, ts, type_, payload_raw in rows:
        if not clean_ts(ts):
            continue
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            continue
        out.append((device_id, ts, type_, payload))
    return out


def day_of(ts: int) -> str:
    """时间戳 → 本地日期字符串（东八区）。"""
    import datetime
    return datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")


def is_noise(pkg: str) -> bool:
    return pkg in SYSTEM_PACKAGES or pkg in OWN_PACKAGES


def build_sessions(events: list[tuple[int, str, int, dict]]) -> list[tuple]:
    """把 usage 事件拼成前台会话，用 app_switch/screen_off 做边界。

    简化模型：usage 事件本身自带 start(ts)/end(endMs)/duration，按 (device, app 连续)
    合并同 app 相邻片段，跨 app_switch 断开。不做复杂状态机，够用且稳。
    """
    # 按设备分组、按时间排序
    by_device: dict[str, list[tuple[int, str, dict]]] = defaultdict(list)
    for device_id, ts, type_, payload in events:
        by_device[device_id].append((ts, type_, payload))

    sessions = []
    for device_id, evs in by_device.items():
        evs.sort(key=lambda e: e[0])
        cur = None  # (pkg, app, activity, start_ms, end_ms, dur)
        for ts, type_, p in evs:
            if type_ == "usage":
                pkg = p.get("pkg", "")
                if is_noise(pkg):
                    continue
                app = p.get("app", pkg)
                fg_ms = p.get("foreground_ms", 0) or 0
                end_ms = p.get("endMs", ts)
                # 丢弃 ≤30 秒碎片会话（实测大量 0-5 秒的系统/切换噪音）
                if fg_ms < 30_000:
                    continue
                activity = p.get("activity", "")
                # 同 app 连续 → 合并（end 取 max，时长累加）
                if cur and cur[0] == pkg and abs(ts - cur[4]) < 2 * 60_000:
                    cur = (cur[0], cur[1], cur[2], cur[3], max(cur[4], end_ms), cur[5] + fg_ms)
                else:
                    if cur:
                        sessions.append((device_id, day_of(cur[3]), *cur))
                    cur = (pkg, app, activity, ts, end_ms, fg_ms)
            elif type_ == "session" and cur is not None:
                kind = p.get("kind")
                if kind == "app_switch" or kind == "screen_off":
                    sessions.append((device_id, day_of(cur[3]), *cur))
                    cur = None
        if cur:
            sessions.append((device_id, day_of(cur[3]), *cur))
    return sessions


def build_daily_stats(events, sessions) -> list[tuple]:
    """按天汇总。"""
    stats: dict[str, dict] = defaultdict(lambda: {
        "total_screen_ms": 0, "app_usage": defaultdict(int), "notif_count": 0,
        "notif_clicked": 0, "notif_apps": defaultdict(int), "screen_on": 0,
        "screen_off": 0, "unlock": 0, "switch": 0, "location": 0, "audio_clip": 0,
    })
    for device_id, day, pkg, app, activity, start_ms, end_ms, dur in sessions:
        s = stats[day]
        s["total_screen_ms"] += dur
        s["app_usage"][app] += dur
    for _, ts, type_, p in events:
        d = day_of(ts)
        s = stats[d]
        if type_ == "notification":
            s["notif_count"] += 1
            if p.get("clicked"):
                s["notif_clicked"] += 1
            pkg = p.get("pkg", "unknown")
            if not is_noise(pkg):
                s["notif_apps"][p.get("app", pkg)] += 1
        elif type_ == "session":
            kind = p.get("kind")
            if kind == "screen_on":
                s["screen_on"] += 1
            elif kind == "screen_off":
                s["screen_off"] += 1
            elif kind == "unlock":
                s["unlock"] += 1
            elif kind == "app_switch":
                s["switch"] += 1
        elif type_ == "location":
            s["location"] += 1
        elif type_ == "audio_clip":
            s["audio_clip"] += 1

    rows = []
    for day, s in sorted(stats.items()):
        top_apps = sorted(s["app_usage"].items(), key=lambda kv: -kv[1])[:10]
        top_notif = sorted(s["notif_apps"].items(), key=lambda kv: -kv[1])[:5]
        rows.append((
            day, s["total_screen_ms"],
            json.dumps([{"app": a, "ms": m} for a, m in top_apps], ensure_ascii=False),
            s["notif_count"], s["notif_clicked"],
            json.dumps([{"app": a, "n": n} for a, n in top_notif], ensure_ascii=False),
            s["screen_on"], s["screen_off"], s["unlock"], s["switch"],
            s["location"], s["audio_clip"],
        ))
    return rows


def build_places(events) -> list[tuple]:
    """位置网格聚类：0.001° (~110m) 网格，聚合停留次数与时间范围。"""
    grid: dict[tuple, dict] = {}
    for _, ts, type_, p in events:
        if type_ != "location":
            continue
        lat = p.get("lat")
        lon = p.get("lon")
        if lat is None or lon is None:
            continue
        gk = (round(lat * 1000) / 1000, round(lon * 1000) / 1000)
        cell = grid.setdefault(gk, {"lat": lat, "lon": lon, "first": ts, "last": ts, "n": 0})
        cell["first"] = min(cell["first"], ts)
        cell["last"] = max(cell["last"], ts)
        cell["n"] += 1
    rows = []
    for (glat, glon), c in grid.items():
        rows.append(("", f"{glat:.3f},{glon:.3f}", c["lat"], c["lon"],
                     "未知", c["first"], c["last"], c["n"]))
    return rows


def run(db_path: Path = DB_PATH, device_id: str | None = None) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)

    events = load_events(conn)
    print(f"[etl] 事件总数(清洗后): {len(events)}")

    # 重置表（全量重建，简单可靠）
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM daily_stats")
    conn.execute("DELETE FROM places")

    sessions = build_sessions(events)
    conn.executemany(
        "INSERT INTO sessions(device_id, day, pkg, app, activity, start_ms, end_ms, duration_ms) "
        "VALUES (?,?,?,?,?,?,?,?)",
        sessions,
    )
    print(f"[etl] sessions: {len(sessions)}")

    daily = build_daily_stats(events, sessions)
    conn.executemany(
        "INSERT INTO daily_stats(day, total_screen_ms, app_ranking_json, notification_count, "
        "notification_clicked, top_notification_apps_json, screen_on_count, screen_off_count, "
        "unlock_count, switch_count, location_count, audio_clip_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        daily,
    )
    print(f"[etl] daily_stats: {len(daily)}")

    places = build_places(events)
    conn.executemany(
        "INSERT INTO places(device_id, grid_key, lat, lon, label, first_seen, last_seen, visit_count) "
        "VALUES (?,?,?,?,?,?,?,?)",
        places,
    )
    print(f"[etl] places: {len(places)}")

    conn.commit()
    conn.close()
    print("[etl] 完成")


def main() -> None:
    parser = argparse.ArgumentParser(description="weiTrack ETL")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()
    run(args.db)


if __name__ == "__main__":
    main()
