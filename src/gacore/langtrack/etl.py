"""langTrack ETL：把原始事件流清洗并加工成可分析的事实表。

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
import datetime
import json
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "langtrack.db"

# 同设备重装前后 device_id 别名映射（data/device_aliases.json）：别名 → 主设备。
# app 端已改为基于 ANDROID_ID 的稳定标识，但历史数据里重装前的旧 device_id 仍存在，
# ETL 每次运行先把别名数据归并到主设备，避免同一台手机被劈成两半。
DEVICE_ALIASES_PATH = Path(__file__).resolve().parents[3] / "data" / "device_aliases.json"

# 与客户端 UsageRepository.SYSTEM_PACKAGES 对齐 + 实测新增噪音（42 个去重后）
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
  is_primary INTEGER NOT NULL DEFAULT 0,
  -- L2 语义落库字段（geocode 增量编码写入）
  address TEXT,
  poi TEXT,
  district TEXT,
  township TEXT,
  business_area TEXT,
  poi_type TEXT,
  -- P1-2 POI 三级语义（高德 type 拆"大类;中类;细类"）+ 名称硬信号 + 无POI兜底描述
  poi_l1 TEXT,
  poi_l2 TEXT,
  poi_l3 TEXT,
  poi_signal TEXT,
  poi_fallback TEXT,
  matched_level TEXT,
  behavior TEXT,
  geocoded_at INTEGER,
  -- L1 家/公司置信度候选（未确认前 label 保持中性表述，候选单独存）
  candidate_label TEXT,
  confidence_home REAL DEFAULT 0,
  confidence_work REAL DEFAULT 0,
  UNIQUE(device_id, grid_key)
);
CREATE INDEX IF NOT EXISTS idx_places_device ON places(device_id);

-- P1-3 新地点/异常事件（打破规律的点，作画像叙事节点）
CREATE TABLE IF NOT EXISTS anomalies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  device_id TEXT NOT NULL,
  grid_key TEXT,
  poi TEXT,
  detail TEXT,
  ts INTEGER,
  UNIQUE(day, kind, grid_key)
);
CREATE INDEX IF NOT EXISTS idx_anomalies_day ON anomalies(day);

CREATE TABLE IF NOT EXISTS stays (
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
  radius_m REAL NOT NULL DEFAULT 0,
  grid_key TEXT,
  day TEXT
);
CREATE INDEX IF NOT EXISTS idx_stays_device ON stays(device_id);
CREATE INDEX IF NOT EXISTS idx_stays_day ON stays(day);

-- L3 移动轨迹段：相邻停驻点之间的移动区间 + 高德路径规划补路
CREATE TABLE IF NOT EXISTS trips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  start_ts INTEGER NOT NULL,
  end_ts INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL,
  start_lat REAL NOT NULL,
  start_lon REAL NOT NULL,
  end_lat REAL NOT NULL,
  end_lon REAL NOT NULL,
  dist_m REAL NOT NULL,
  n_points INTEGER NOT NULL DEFAULT 0,
  day TEXT,
  polyline TEXT,
  route_key TEXT,
  route_mode TEXT,
  route_encoded_at INTEGER,
  UNIQUE(device_id, start_ts, end_ts)
);
CREATE INDEX IF NOT EXISTS idx_trips_device ON trips(device_id);
CREATE INDEX IF NOT EXISTS idx_trips_day ON trips(day);

-- P1 路过网格统计（通勤带）：trips.polyline 网格量化后的高频经过网格（纯本地，零配额）
CREATE TABLE IF NOT EXISTS route_grids (
  device_id TEXT NOT NULL,
  day TEXT NOT NULL,
  grid_lat REAL NOT NULL,
  grid_lon REAL NOT NULL,
  n_pass INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER,
  PRIMARY KEY(device_id, day, grid_lat, grid_lon)
);
CREATE INDEX IF NOT EXISTS idx_route_grids_grid ON route_grids(grid_lat, grid_lon);

-- P2 沿途 POI（网格级缓存）：每个网格最多一条周边 POI（around 100 次/日，低频克制）
CREATE TABLE IF NOT EXISTS grid_pois (
  grid_lat REAL NOT NULL,
  grid_lon REAL NOT NULL,
  name TEXT,
  type TEXT,
  distance TEXT,
  queried_at INTEGER,
  PRIMARY KEY(grid_lat, grid_lon)
);
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
                # 丢弃 ≤5 秒碎片会话（实测大量 0-5 秒的系统/切换噪音）
                if fg_ms < 5_000:
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


# ---------------------------------------------------------------------------
# L1 停驻点检测（stays）
# ---------------------------------------------------------------------------

_EARTH_R = 6371000.0


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点球面距离（米）。"""
    import math
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R * math.asin(math.sqrt(a))


def _median_center(points: list[tuple[float, float]]) -> tuple[float, float]:
    """中位数中心（对 GPS 抖动比均值更鲁棒）。points: [(lat, lon)]。"""
    n = len(points)
    lats = sorted(p[0] for p in points)
    lons = sorted(p[1] for p in points)

    def med(a: list) -> float:
        m = n // 2
        return a[m] if n % 2 else (a[m - 1] + a[m]) / 2.0

    return med(lats), med(lons)


# 停驻检测阈值（可配分档）：家/公司用大圈，密集商圈用小圈。
STAY_LARGE_RADIUS_M = 120.0   # 大圈：家/公司/大空间（方案 100m+，略放宽抗抖动）
STAY_SMALL_RADIUS_M = 60.0    # 小圈：密集商圈参考值（作为停留覆盖半径的分档标记）
STAY_MIN_DURATION_MS = 10 * 60 * 1000   # 停留最短时长：连续 10 分钟
STAY_MERGE_GAP_MS = 5 * 60 * 1000       # 边界合并：相邻停留间隔 < 5 分钟视为同一段
STAY_MERGE_RADIUS_M = 150.0             # 边界合并：相邻段中心距 < 150m 才合并（防通勤过渡段误并入）
STAY_MAX_JUMP_M = 500.0                 # GPS 漂移尖刺：相对上一点突跳 > 500m
STAY_MAX_SPEED_MPS = 40.0               # 漂移尖刺：瞬时速度 > 40m/s（144km/h）


def build_stays(
    events,
    large_radius_m: float = STAY_LARGE_RADIUS_M,
    small_radius_m: float = STAY_SMALL_RADIUS_M,
    min_stay_ms: int = STAY_MIN_DURATION_MS,
    merge_gap_ms: int = STAY_MERGE_GAP_MS,
    merge_radius_m: float = STAY_MERGE_RADIUS_M,
    max_jump_m: float = STAY_MAX_JUMP_M,
    max_speed_mps: float = STAY_MAX_SPEED_MPS,
) -> list[tuple]:
    """L1 停驻点检测：滑动窗口 + 中位数中心。

    - 连续停留：新点与当前停留锚点（前 3 点中位数，固定不漂移）距离 <= 大圈半径 → 归入停留。
    - 漂移剔除：瞬时速度 > max_speed 且突跳 > max_jump 的 GPS 飞点直接丢弃；
      远离锚点但仅 1 点的"尖刺弹回"先挂起，下一点回到锚点则判为尖刺丢弃。
    - 边界合并：相邻两段停留 间隔 < merge_gap_ms 且 中心距 < merge_radius_m → 合并为同一段
      （双条件：移动中采样间隔通常 <5min，仅按时间合并且会把通勤过渡段误并入停留段）。
    - 阈值分档：检测统一用大圈（家/公司不劈碎）；停留的实际覆盖半径 <= 小圈
      则记为小圈（商圈/密集区），供画像区分空间粒度。阈值均可配置。
    - 输出元组：(device_id, start_ts, end_ts, duration_ms, center_lat, center_lon,
      min_lat, min_lon, max_lat, max_lon, n_points, radius_m, grid_key, day)
    """
    # 按设备分组、按时间排序
    by_device: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for device_id, ts, type_, p in events:
        if type_ != "location":
            continue
        lat = p.get("lat")
        lon = p.get("lon")
        if lat is None or lon is None:
            continue
        by_device[device_id].append((ts, lat, lon))
    for evs in by_device.values():
        evs.sort(key=lambda e: e[0])

    stays: list[tuple] = []
    for device_id, evs in by_device.items():
        if not evs:
            continue
        # ---- 粗停留段检测（固定锚点，防中位数中心随点加入漂移导致半径膨胀）----
        raw_segs: list[list[tuple[int, float, float]]] = []
        cur: list[tuple[int, float, float]] = [evs[0]]
        anchor = (evs[0][1], evs[0][2])  # 锚点：前 3 点中位数定锚后固定
        pending: tuple[int, float, float] | None = None
        prev = evs[0]
        for e in evs[1:]:
            ts, lat, lon = e
            dt = (ts - prev[0]) / 1000.0
            dist_prev = _haversine(prev[1], prev[2], lat, lon)
            speed = dist_prev / dt if dt > 0 else 0.0
            # 超速 + 突跳 → GPS 飞点，直接丢弃（不断开停留）
            if speed > max_speed_mps and dist_prev > max_jump_m:
                prev = e
                continue
            dist_anchor = _haversine(anchor[0], anchor[1], lat, lon)
            if dist_anchor <= large_radius_m:
                # 回到停留内；若有挂起点说明那是尖刺，丢弃
                pending = None
                cur.append(e)
                # 仅前 3 点用中位数重定锚（抗首点抖动），之后锚点固定不漂移
                if len(cur) <= 3:
                    anchor = _median_center([(x[1], x[2]) for x in cur])
            else:
                if pending is None:
                    # 首个远离点：挂起观察（可能是尖刺弹回，也可能是真离开）
                    pending = e
                else:
                    # 连续两个远离锚点 → 确认离开，切段；新段以挂起点为锚
                    raw_segs.append(cur)
                    cur = [pending, e]
                    anchor = (pending[1], pending[2])
                    pending = None
            prev = e
        if pending is not None:
            raw_segs.append(cur)
            cur = [pending]
        if cur:
            raw_segs.append(cur)

        # ---- 边界合并（间隔 < merge_gap 且 相邻段中心距离 < merge_radius → 同一段） ----
        # 双条件：仅按时间间隔合并且移动中采样点间隔通常 <5min，会把通勤过渡段
        # 误并入停留段导致半径膨胀（实测家段被拉到 587m）；空间相近才合并。
        segs: list[list[tuple[int, float, float]]] = []
        for seg in raw_segs:
            if segs and (seg[0][0] - segs[-1][-1][0]) < merge_gap_ms:
                c1 = _median_center([(x[1], x[2]) for x in segs[-1]])
                c2 = _median_center([(x[1], x[2]) for x in seg])
                if _haversine(c1[0], c1[1], c2[0], c2[1]) <= merge_radius_m:
                    segs[-1].extend(seg)
                    continue
            segs.append(list(seg))

        # ---- 生成停留记录（时长达标才保留） ----
        for seg in segs:
            start_ts = seg[0][0]
            end_ts = seg[-1][0]
            duration = end_ts - start_ts
            if duration < min_stay_ms:
                continue
            pts = [(x[1], x[2]) for x in seg]
            clat, clon = _median_center(pts)
            radius = max(_haversine(clat, clon, la, lo) for la, lo in pts)
            # 阈值分档：覆盖半径 <= 小圈 → 记小圈（商圈/密集区），否则大圈（家/公司）
            radius_m = min(radius, small_radius_m) if radius <= small_radius_m else max(radius, large_radius_m)
            lats = [la for la, _ in pts]
            lons = [lo for _, lo in pts]
            gk = f"{round(clat * 1000) / 1000:.3f},{round(clon * 1000) / 1000:.3f}"
            stays.append((
                device_id, start_ts, end_ts, duration,
                clat, clon, min(lats), min(lons), max(lats), max(lons),
                len(pts), round(radius_m, 1), gk, day_of(start_ts),
            ))
    stays.sort(key=lambda s: (s[0], s[1]))
    return stays


def build_places(events) -> list[tuple]:
    """位置网格聚类：0.001° (~110m) 网格，聚合停留次数与时间范围。

    修复：device_id 按事件实际值填充（不再写死空串），多设备按 (device_id, grid) 隔离。
    """
    grid: dict[tuple, dict] = {}
    for device_id, ts, type_, p in events:
        if type_ != "location":
            continue
        lat = p.get("lat")
        lon = p.get("lon")
        if lat is None or lon is None:
            continue
        gk = (round(lat * 1000) / 1000, round(lon * 1000) / 1000)
        cell = grid.setdefault((device_id, gk), {"lat": lat, "lon": lon, "first": ts, "last": ts, "n": 0})
        cell["first"] = min(cell["first"], ts)
        cell["last"] = max(cell["last"], ts)
        cell["n"] += 1
    rows = []
    for (device_id, (glat, glon)), c in grid.items():
        rows.append((device_id, f"{glat:.3f},{glon:.3f}", c["lat"], c["lon"],
                     "未知", c["first"], c["last"], c["n"]))
    return rows


def infer_home_work_candidates(conn: sqlite3.Connection) -> int:
    """L1 家/公司置信度推断（候选制 + 双榜并行 + 已确认点置信度回填）。

    - 家：凌晨 00:00-05:00 停留天数 >=3 → 高置信（home 榜）。
    - 公司：工作日白天 09:00-18:00 高频停留（>=15 次）→ 高置信（work 榜）。
    - 双榜并行：评估网格 = 凌晨停留榜 ∪ 工作日白天高频榜；纯白天高频、
      凌晨无停留的办公网格也能进入公司候选（修复验收盲区③）。
    - 已确认点（label 家/公司）不再跳过，同样计算并回填 confidence_home /
      confidence_work，candidate_label 记为其确认标签（修复验收盲区②）；
      未确认点 candidate_label 记推断候选，label 保持中性由用户确认。
    """
    home_days: dict[str, set[str]] = defaultdict(set)
    work_count: dict[str, int] = defaultdict(int)
    rows = conn.execute(
        "SELECT ts, payload FROM events WHERE type='location'"
    ).fetchall()
    import datetime
    for ts, raw in rows:
        try:
            p = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        lat, lon = p.get("lat"), p.get("lon")
        if lat is None or lon is None:
            continue
        gk = f"{round(lat * 1000) / 1000:.3f},{round(lon * 1000) / 1000:.3f}"
        dt = datetime.datetime.fromtimestamp(ts / 1000)
        hour = dt.hour
        if hour < 5:
            home_days[gk].add(dt.strftime("%Y-%m-%d"))
        if 9 <= hour < 18 and dt.weekday() < 5:
            work_count[gk] += 1

    # 已确认标签（label 家/公司）按网格索引，用于回填 candidate_label
    confirmed: dict[str, str] = {}
    for gk, lab in conn.execute(
        "SELECT grid_key, label FROM places WHERE label IN ('家','公司')"
    ):
        confirmed.setdefault(gk, lab)

    WORK_THRESHOLD = 15  # 工作日白天高频阈值：>=15 次进入公司候选评估
    # 评估网格 = 凌晨停留榜 ∪ 工作日白天高频榜 ∪ 已确认 label 网格。
    # 已确认点强制纳入，保证置信度无条件回填（即使不在任何高频榜）。
    grids = set(home_days) | {gk for gk, n in work_count.items() if n >= WORK_THRESHOLD} | set(confirmed)

    n_updated = 0
    for gk in grids:
        home_conf = min(1.0, len(home_days.get(gk, ())) / 3.0)
        work_conf = min(1.0, work_count.get(gk, 0) / WORK_THRESHOLD)
        candidate = None
        if home_conf >= 0.67 and home_conf > work_conf:
            candidate = "家"
        elif work_conf >= 0.67 and work_conf > home_conf:
            candidate = "公司"
        # 已确认点回填确认标签；未确认点写入推断候选
        cand_final = confirmed.get(gk, candidate)
        cur = conn.execute(
            "UPDATE places SET candidate_label=?, confidence_home=?, confidence_work=? "
            "WHERE grid_key=?",
            (cand_final, round(home_conf, 2), round(work_conf, 2), gk),
        )
        n_updated += cur.rowcount
    return n_updated


def detect_anomalies(conn: sqlite3.Connection, lookback_days: int = 7) -> int:
    """P1-3 新地点/异常事件探测：识别打破规律的点，写入 anomalies 表。

    三类异常（作画像叙事节点）：
    - new_place      首次到访新地点：近 lookback_days 天内 first_seen 且访问次数 <= 3，
                     且非已确认家/公司（如新出现的医院、商场、陌生住宅区）。
    - late_night_out 深夜/凌晨在外：停驻点开始时间落在 23:00-05:00 且不在家网格
                     （深夜还待在公司/外出场所，规律打破）。
    - off_schedule   工作日白天缺席公司：当天有停驻但 10:00-17:00 无公司网格停留
                     （居家办公/请假/翘班，与"白天在公司"惯例相悖）。
    """
    conn.row_factory = sqlite3.Row
    conn.execute("DELETE FROM anomalies")
    now_ms = int(time.time() * 1000)
    home = {r[0] for r in conn.execute("SELECT grid_key FROM places WHERE label='家'")}
    work = {r[0] for r in conn.execute("SELECT grid_key FROM places WHERE label='公司'")}

    def place_name(gk: str) -> str:
        r = conn.execute(
            "SELECT poi, poi_fallback FROM places WHERE grid_key=? LIMIT 1", (gk,)
        ).fetchone()
        if r:
            return r["poi"] or r["poi_fallback"] or gk
        return gk

    rows: list[tuple] = []

    # 1) 新地点
    for r in conn.execute("SELECT * FROM places"):
        if r["label"] in ("家", "公司"):
            continue
        if r["first_seen"] and r["first_seen"] >= now_ms - lookback_days * 86400000 and r["visit_count"] <= 3:
            day = datetime.datetime.fromtimestamp(r["first_seen"] / 1000).strftime("%Y-%m-%d")
            name = r["poi"] or r["poi_fallback"] or r["grid_key"]
            rows.append((
                day, "new_place", r["device_id"], r["grid_key"], name,
                f"首次到访新地点：{name}（访问 {r['visit_count']} 次）", r["first_seen"],
            ))

    # 2) 深夜/凌晨在外（23:00-05:00 停留且不在家网格）
    for r in conn.execute("SELECT * FROM stays"):
        h = datetime.datetime.fromtimestamp(r["start_ts"] / 1000).hour
        if not (h >= 23 or h < 5):
            continue
        if r["grid_key"] in home:
            continue
        day = r["day"]
        dur = (r["end_ts"] - r["start_ts"]) / 60000.0
        name = place_name(r["grid_key"])
        rows.append((
            day, "late_night_out", r["device_id"], r["grid_key"], name,
            f"深夜在外停留 {dur:.0f} 分钟：{name}", r["start_ts"],
        ))

    # 3) 工作日白天缺席公司（当天有停驻但 10:00-17:00 无公司网格停留）
    for day, day_stays in _group_stays_by_day(conn):
        if datetime.date.fromisoformat(day).weekday() >= 5:
            continue
        if not day_stays:
            continue
        # 正午 13:00 作为"白天在公司"的代表时刻：停留段覆盖 13:00 且落在公司网格
        # （不能用 start_ts 落在窗口内判断——公司停留段常开始于 08:40/09:47，会被误判缺席）
        noon = int(datetime.datetime.fromisoformat(f"{day} 13:00").timestamp() * 1000)
        in_office = any(
            s[2] in work and s[0] <= noon <= s[1]
            for s in day_stays
        )
        if not in_office:
            rows.append((
                day, "off_schedule", day_stays[0][4], "", "",
                "工作日白天未到公司（正午 13:00 无公司网格停驻）", day_stays[0][0],
            ))

    conn.executemany(
        "INSERT OR IGNORE INTO anomalies(day, kind, device_id, grid_key, poi, detail, ts) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def detect_route_changes(conn: sqlite3.Connection) -> int:
    """L3 路线变化事件：同日相邻出行的 route_key 不同 → route_change（复用 anomalies 叙事表）。

    前置：trips 已完成高德补路（route_key 非空）。anomalies 表 UNIQUE(day, kind, grid_key)，
    故 grid_key 存 'rc:'+route_key[:8] 承载指纹保唯一。返回新增事件数。
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT device_id, day, start_ts, start_lat, start_lon, end_lat, end_lon, route_key "
        "FROM trips WHERE route_key IS NOT NULL AND route_key != '' "
        "ORDER BY device_id, start_ts"
    ).fetchall()

    def hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        import math
        r = 6371000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))

    by_dev: dict[str, list] = defaultdict(list)
    for r in rows:
        by_dev[r["device_id"]].append(r)
    changes: list[tuple] = []
    PAIR_DIST = 400.0  # 往返对判距：A起点≈B终点 且 A终点≈B起点
    for _device_id, trips in by_dev.items():
        for i in range(1, len(trips)):
            prev, cur = trips[i - 1], trips[i]
            if prev["day"] != cur["day"] or prev["route_key"] == cur["route_key"]:
                continue
            # 往返对（去程+回程）：同一通勤路往返不报"路线变化"
            round_trip = (
                hav(prev["start_lat"], prev["start_lon"], cur["end_lat"], cur["end_lon"]) < PAIR_DIST
                and hav(prev["end_lat"], prev["end_lon"], cur["start_lat"], cur["start_lon"]) < PAIR_DIST
            )
            if round_trip:
                continue
            gk = "rc:" + (cur["route_key"][:8] or "?")
            detail = (
                f"通勤路线变化（同日相邻出行指纹不同）: "
                f"{prev['start_lat']:.4f},{prev['start_lon']:.4f} → "
                f"{cur['end_lat']:.4f},{cur['end_lon']:.4f}"
            )
            changes.append((
                cur["day"], "route_change", _device_id, gk, "", detail, cur["start_ts"],
            ))
    conn.executemany(
        "INSERT OR IGNORE INTO anomalies(day, kind, device_id, grid_key, poi, detail, ts) "
        "VALUES (?,?,?,?,?,?,?)",
        changes,
    )
    conn.commit()
    return len(changes)


def _group_stays_by_day(conn: sqlite3.Connection) -> list[tuple[str, list[tuple]]]:
    """按天分组停驻点：返回 [(day, [(start_ts, end_ts, grid_key, in_work_hours, device_id), ...])]。"""
    by_day: dict[str, list] = defaultdict(list)
    for r in conn.execute("SELECT * FROM stays"):
        by_day[r["day"]].append((r["start_ts"], r["end_ts"], r["grid_key"], False, r["device_id"]))
    return sorted(by_day.items())


def migrate_places(conn: sqlite3.Connection) -> None:
    """places 表迁移：补语义/候选列；修复旧库 device_id='' 归并到实际设备。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(places)")}
    for col, ddl in {
        "address": "TEXT",
        "poi": "TEXT",
        "district": "TEXT",
        "township": "TEXT",
        "business_area": "TEXT",
        "poi_type": "TEXT",
        "poi_l1": "TEXT",
        "poi_l2": "TEXT",
        "poi_l3": "TEXT",
        "poi_signal": "TEXT",
        "poi_fallback": "TEXT",
        "matched_level": "TEXT",
        "behavior": "TEXT",
        "geocoded_at": "INTEGER",
        "candidate_label": "TEXT",
        "confidence_home": "REAL DEFAULT 0",
        "confidence_work": "REAL DEFAULT 0",
    }.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE places ADD COLUMN {col} {ddl}")

    # 修复旧库空 device_id：归并到 events 中实际设备（多设备时取最新活跃）
    empty = conn.execute("SELECT id, grid_key, lat, lon, label, first_seen, last_seen, visit_count, is_primary "
                         "FROM places WHERE device_id='' OR device_id IS NULL").fetchall()
    if empty:
        real = conn.execute(
            "SELECT device_id FROM events WHERE type='location' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        target = real[0] if real else "unknown"
        for (pid, gk, lat, lon, label, first, last, vc, isp) in empty:
            exists = conn.execute(
                "SELECT id FROM places WHERE device_id=? AND grid_key=?", (target, gk)
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE places SET first_seen=MIN(first_seen,?), last_seen=MAX(last_seen,?), "
                    "visit_count=visit_count+?, is_primary=MAX(is_primary,?) WHERE id=?",
                    (first, last, vc, isp, exists[0]),
                )
                conn.execute("DELETE FROM places WHERE id=?", (pid,))
            else:
                conn.execute("UPDATE places SET device_id=? WHERE id=?", (target, pid))
        print(f"[etl] 修复旧库空 device_id 常驻点: {len(empty)} 条 → {target}")


def _load_device_aliases() -> dict[str, str]:
    """读取设备别名映射（别名 → 主设备）。"""
    if not DEVICE_ALIASES_PATH.exists():
        return {}
    try:
        data = json.loads(DEVICE_ALIASES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in (data or {}).items() if k and v}


def merge_device_aliases(conn: sqlite3.Connection) -> int:
    """同设备重装前后 device_id 归一：把别名设备的数据并入主设备。

    - events：别名 → 主设备（device_id 直接改写）
    - places：同一 (device, grid) 合并统计（首末时间取并集、访问数累加、
      非"未知"label 优先、语义字段主设备缺失时补别名），别名行删除
    - devices：主设备时间范围吸收别名，删除别名行
    返回归并的别名设备数。必须在 load_events 之前调用（build 系列以 device_id 分组）。
    """
    aliases = _load_device_aliases()
    if not aliases:
        return 0
    merged = 0
    for alias, primary in aliases.items():
        if alias == primary:
            continue
        # events 归一
        cur = conn.execute(
            "UPDATE events SET device_id=? WHERE device_id=?", (primary, alias)
        )
        # places 归一（先合并统计再删别名行）
        alias_rows = conn.execute(
            "SELECT id, grid_key, lat, lon, label, first_seen, last_seen, visit_count, "
            "is_primary, address, poi, poi_type, behavior, matched_level "
            "FROM places WHERE device_id=?", (alias,)
        ).fetchall()
        for r in alias_rows:
            # 元组索引: 0=id 1=grid_key 2=lat 3=lon 4=label 5=first_seen
            #           6=last_seen 7=visit_count 8=is_primary 9=address 10=poi
            r_id, r_gk, r_lat, r_lon, r_label, r_first, r_last, r_vc, r_isp = r[:9]
            r_addr, r_poi = r[9], r[10]
            exists = conn.execute(
                "SELECT id, label, address, poi FROM places "
                "WHERE device_id=? AND grid_key=?", (primary, r_gk)
            ).fetchone()
            if exists:
                pid, plabel, paddr, ppoi = exists
                label = plabel if plabel and plabel != "未知" else (r_label or "未知")
                conn.execute(
                    "UPDATE places SET first_seen=MIN(first_seen,?), last_seen=MAX(last_seen,?), "
                    "visit_count=visit_count+?, is_primary=MAX(is_primary,?), label=?, "
                    "address=COALESCE(NULLIF(?, ''), address), "
                    "poi=COALESCE(NULLIF(?, ''), poi) WHERE id=?",
                    (r_first, r_last, r_vc, r_isp, label, r_addr, r_poi, pid),
                )
                conn.execute("DELETE FROM places WHERE id=?", (r_id,))
            else:
                conn.execute("UPDATE places SET device_id=? WHERE id=?", (primary, r_id))
        # devices 时间范围吸收 + 删除别名行
        conn.execute(
            "UPDATE devices SET first_seen=MIN(first_seen, (SELECT first_seen FROM devices WHERE device_id=?)), "
            "last_seen=MAX(last_seen, (SELECT last_seen FROM devices WHERE device_id=?)), "
            "updated_at=datetime('now','+8 hours') WHERE device_id=?",
            (alias, alias, primary),
        )
        conn.execute("DELETE FROM devices WHERE device_id=?", (alias,))
        merged += 1
        print(f"[etl] 设备归一: {alias} → {primary} (events {cur.rowcount} 条, places {len(alias_rows)} 条)")
    conn.commit()
    return merged


def purge_dirty(db_path: Path) -> None:
    """删除 events 表中的异常数据：ts 脏数据 + payload 非 JSON。幂等，可重复执行。"""
    conn = sqlite3.connect(db_path)
    c1 = conn.execute("DELETE FROM events WHERE ts < 1000000000000")
    # payload 非 JSON 的（防御）
    rows = conn.execute("SELECT id, payload FROM events").fetchall()
    bad_ids = []
    for eid, raw in rows:
        try:
            json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            bad_ids.append(eid)
    if bad_ids:
        conn.executemany("DELETE FROM events WHERE id=?", [(i,) for i in bad_ids])
    conn.commit()
    print(f"[purge] 删除 ts 脏数据 {c1.rowcount} 条, 非 JSON payload {len(bad_ids)} 条")
    conn.close()


# P2 沿途 POI 每轮 ETL 查询网格上限（around 免费配额仅 100 次/日，克制）
_POI_MAX_PER_RUN = int(os.environ.get("LANGTRACK_POI_MAX_PER_RUN", "5"))


def run(db_path: Path = DB_PATH, device_id: str | None = None, run_geocode: bool = True, run_route: bool = True, run_poi: bool = True) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    # 迁移：旧 places 表补 is_primary 列（新库已含）
    cols = {r[1] for r in conn.execute("PRAGMA table_info(places)")}
    if "is_primary" not in cols:
        conn.execute("ALTER TABLE places ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0")
    # 迁移：places 语义/候选列 + 旧库空 device_id 归并
    migrate_places(conn)
    # 同设备重装前后 device_id 归一（必须在 load_events 之前，build 系列按 device_id 分组）
    n_alias = merge_device_aliases(conn)
    if n_alias:
        print(f"[etl] 设备别名归并完成: {n_alias} 个")

    events = load_events(conn)
    print(f"[etl] 事件总数(清洗后): {len(events)}")

    # 重置表（全量重建，简单可靠）；places 用 upsert 保留已标注标签（家/公司）
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM daily_stats")

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

    # L1：停驻点检测 → stays 表（全量重建）
    conn.execute("DELETE FROM stays")
    stays = build_stays(events)
    conn.executemany(
        "INSERT INTO stays(device_id, start_ts, end_ts, duration_ms, center_lat, center_lon, "
        "min_lat, min_lon, max_lat, max_lon, n_points, radius_m, grid_key, day) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        stays,
    )
    print(f"[etl] stays(L1 停驻点): {len(stays)}")

    # L3：移动轨迹段（trips）——全量重建结构，已编码路线按 (device,start_ts,end_ts) 带回，
    # 已编码列保留（不烧高德配额）；新移动段编码列为空，由下方增量补路补齐。
    from gacore.langtrack.routes import build_trips
    old_route: dict[tuple, tuple] = {}
    for r in conn.execute(
        "SELECT device_id, start_ts, end_ts, polyline, route_key, route_mode, route_encoded_at FROM trips"
    ):
        old_route[(r[0], r[1], r[2])] = (r[3], r[4], r[5], r[6])
    conn.execute("DELETE FROM trips")
    trips = build_trips(events, stays)
    for t in trips:
        poly, rk, mode, enc = old_route.get((t[0], t[1], t[2]), (None, None, None, None))
        conn.execute(
            "INSERT INTO trips(device_id, start_ts, end_ts, duration_ms, start_lat, start_lon, "
            "end_lat, end_lon, dist_m, n_points, day, polyline, route_key, route_mode, route_encoded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8], t[9], t[10],
             poly, rk, mode, enc),
        )
    print(f"[etl] trips(L3 移动段): {len(trips)}")

    # places：保留已标注 label（家/公司/未知），只更新统计；新点以"未知"插入
    places = build_places(events)
    for p in places:
        conn.execute(
            """
            INSERT INTO places(device_id, grid_key, lat, lon, label, first_seen, last_seen, visit_count)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(device_id, grid_key) DO UPDATE SET
              lat=excluded.lat, lon=excluded.lon,
              first_seen=MIN(places.first_seen, excluded.first_seen),
              last_seen=MAX(places.last_seen, excluded.last_seen),
              visit_count=places.visit_count + excluded.visit_count
            """,
            p,
        )
    print(f"[etl] places: {len(places)}")

    # L1：家/公司置信度候选（不覆盖用户已确认标签）
    n_cand = infer_home_work_candidates(conn)
    print(f"[etl] 家/公司置信度候选更新: {n_cand} 个")

    # 自动标记 top2 主常驻点（按访问次数），不覆盖已确认的家/公司标签
    top2 = conn.execute(
        "SELECT id, grid_key, label FROM places ORDER BY visit_count DESC LIMIT 2"
    ).fetchall()
    for tid, _, tlabel in top2:
        # 仅当该点尚未被人工确认（label 仍为未知）时才提示，主标记始终置位
        conn.execute("UPDATE places SET is_primary=1 WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    # 恢复持久化的家/公司标签（data/place_labels.json）
    try:
        from gacore.langtrack.label_places import apply_labels
        n = apply_labels(db_path)
        if n:
            print(f"[etl] 恢复持久化地点标签: {n} 个")
    except ImportError:
        pass
    # P1-3：新地点/异常事件探测（依赖 places 标签已恢复，家/公司网格集合准确）
    conn = sqlite3.connect(db_path)
    n_anom = detect_anomalies(conn)
    conn.close()
    print(f"[etl] anomalies 异常事件: {n_anom} 条")
    # L2：增量 regeo 编码（仅未编码常驻点，ETL 重跑零新增调用）
    if run_geocode:
        try:
            from gacore.langtrack import geocode
            n = geocode.incremental_encode(db_path)
            print(f"[etl] 增量 regeo 编码: {n} 个常驻点")
        except SystemExit as e:
            print(f"[etl] 跳过增量 regeo 编码: {e}")
        except Exception as e:
            print(f"[etl] 增量 regeo 编码失败(不影响 ETL): {e}")
    # L3：增量路径规划补路（仅未编码新移动段，单次上限节流；失败跳过不阻塞）
    if run_route:
        try:
            from gacore.langtrack import routes
            n = routes.incremental_encode_trips(db_path)
            print(f"[etl] 增量补路(路径规划): {n} 段")
        except SystemExit as e:
            print(f"[etl] 跳过增量补路: {e}")
        except Exception as e:
            print(f"[etl] 增量补路失败(不影响 ETL): {e}")
    # L3：路线变化事件（复用 anomalies 叙事表，依赖 trips 已完成补路）
    conn = sqlite3.connect(db_path)
    n_rc = detect_route_changes(conn)
    conn.close()
    print(f"[etl] route_change 路线变化事件: {n_rc} 条")
    # P1：路过网格统计（通勤带）——纯本地零配额，全量重建
    try:
        from gacore.langtrack import routes as _routes_p1
        n_g = _routes_p1.build_route_grids(db_path)
        print(f"[etl] 路过网格统计(通勤带): {n_g} 行")
    except Exception as e:
        print(f"[etl] 路过网格统计失败(不影响 ETL): {e}")
    # P2：沿途 POI（around 100 次/日受限，默认每轮最多 5 网格，网格级缓存去重）
    if run_poi:
        try:
            n_p = routes.encode_belt_pois(db_path, limit=_POI_MAX_PER_RUN)
            print(f"[etl] 沿途 POI 编码: {n_p} 个网格")
        except SystemExit as e:
            print(f"[etl] 跳过沿途 POI 编码: {e}")
        except Exception as e:
            print(f"[etl] 沿途 POI 编码失败(不影响 ETL): {e}")
    print("[etl] 完成")


def main() -> None:
    parser = argparse.ArgumentParser(description="langTrack ETL")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--purge", action="store_true", help="先清理异常事件再重建事实表")
    parser.add_argument("--no-geocode", action="store_true", help="跳过高德增量 regeo 编码")
    parser.add_argument("--no-route", action="store_true", help="跳过高德路径规划补路(L3)")
    parser.add_argument("--no-poi", action="store_true", help="跳过沿途 POI 编码(P2)")
    args = parser.parse_args()
    if args.purge:
        purge_dirty(args.db)
    run(args.db, run_geocode=not args.no_geocode, run_route=not args.no_route, run_poi=not args.no_poi)


if __name__ == "__main__":
    main()
