"""weiTrack L3 移动轨迹段（trips）与路线变化事件。

给相邻停驻点（stays）之间的移动段用高德路径规划 API 补路线坐标（polyline），
生成无向规范化路线指纹 route_key，比对相邻出行识别通勤路线变化。

配额节流（个人认证开发者免费档）：
- 路径规划 walking 5000 次/日：仅对未编码新段增量调用，单次 ETL 默认上限 100 段
- 周边搜索 around 仅 100 次/日：本模块 P0 不调用（沿途 POI 留待 P2 网格缓存低频）
- 逆地理 regeo 5000 次/日：不动

用法：
    python -m gacore.weitrack.routes            # 增量补路（仅未编码新段）
    python -m gacore.weitrack.routes --all      # 强制全部重编（慎用，烧配额）
依赖环境变量 AMAP_KEY（.env 中配置，复用 geocode 的 Key）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "weitrack.db"
# 步行路径规划（上限 100km，最宽）；骑行/驾车可经 WEITRACK_ROUTE_MODE 切换
DIRECTION_URL = "https://restapi.amap.com/v3/direction/walking"

# 移动段识别阈值
TRIP_MIN_DURATION_MS = 60_000          # 移动段最短时长 60s
TRIP_MIN_DIST_M = 300.0                # 起终点直线距离 >= 300m 才算移动段
# 配额节流：单次 ETL 增量补路上限（远低于 5000 次/日）
_MAX_ENCODE_PER_RUN = int(os.environ.get("WEITRACK_ROUTE_MAX_PER_RUN", "100"))
_ROUTE_MODE = os.environ.get("WEITRACK_ROUTE_MODE", "walking")


def _amap_key() -> str:
    """复用 geocode 的 Key 读取逻辑（环境变量 AMAP_KEY 或 .env）。"""
    from gacore.weitrack import geocode
    return geocode._amap_key()


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_trips(events, stays) -> list[tuple]:
    """识别移动段：相邻两个停留段之间的区间。

    events: [(device_id, ts, type, payload)]；stays: build_stays 的 tuple 列表。
    规则：
    - 对每设备按 start_ts 排序 stays，相邻对 (A, B) 的 gap=[A.end_ts, B.start_ts]
    - gap 内的 location 点即移动采样点；起终点坐标取 gap 内首/末点（无采样点则用 A/B 中心兜底）
    - 双阈值过滤：duration >= 60s 且起终点直线距离 >= 300m（滤小区内挪动与 GPS 抖动）
    返回: [(device_id, start_ts, end_ts, duration_ms, start_lat, start_lon,
            end_lat, end_lon, dist_m, n_points, day)]
    """
    # location 点按设备、按时间索引
    by_dev: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for device_id, ts, type_, p in events:
        if type_ != "location":
            continue
        lat, lon = p.get("lat"), p.get("lon")
        if lat is None or lon is None:
            continue
        by_dev[device_id].append((ts, lat, lon))
    for evs in by_dev.values():
        evs.sort(key=lambda e: e[0])

    # stays 按设备、按 start_ts 排序
    stays_by_dev: dict[str, list[tuple]] = defaultdict(list)
    for s in stays:
        stays_by_dev[s[0]].append(s)
    for lst in stays_by_dev.values():
        lst.sort(key=lambda s: s[1])

    import datetime
    trips: list[tuple] = []
    for device_id, segs in stays_by_dev.items():
        pts = by_dev.get(device_id, [])
        # 指针式取 gap 内点（stays 有序、pts 有序）
        for i in range(len(segs) - 1):
            a, b = segs[i], segs[i + 1]
            gap_start, gap_end = a[2], b[1]  # a.end_ts, b.start_ts
            duration = gap_end - gap_start
            if duration < TRIP_MIN_DURATION_MS:
                continue
            # gap 内采样点
            lo = 0
            in_gap: list[tuple[int, float, float]] = []
            for j in range(lo, len(pts)):
                if pts[j][0] >= gap_end:
                    break
                if pts[j][0] > gap_start:
                    in_gap.append(pts[j])
                lo = j
            if in_gap:
                start_ts, start_lat, start_lon = in_gap[0]
                end_ts, end_lat, end_lon = in_gap[-1]
            else:
                # 无采样点：用前后停留中心兜底
                start_ts, start_lat, start_lon = gap_start, a[4], a[5]
                end_ts, end_lat, end_lon = gap_end, b[4], b[5]
            dist = _haversine(start_lat, start_lon, end_lat, end_lon)
            if dist < TRIP_MIN_DIST_M:
                continue
            day = datetime.datetime.fromtimestamp(start_ts / 1000).strftime("%Y-%m-%d")
            trips.append((
                device_id, start_ts, end_ts, duration,
                round(start_lat, 6), round(start_lon, 6),
                round(end_lat, 6), round(end_lon, 6),
                round(dist, 1), len(in_gap), day,
            ))
    trips.sort(key=lambda t: (t[0], t[1]))
    return trips


def _normalize_polyline(points: list[tuple[float, float]], max_points: int = 20, grid: float = 0.003) -> str:
    """等距抽稀 + 网格量化，返回无向无序网格集合点串。

    - 等距抽稀到最多 max_points 个点（消除步长相位噪声，抽稀结果与采样密度无关）
    - 网格量化 round(*1000)/1000 → 0.003° ≈ 330m。粗网格吸收端点微差
      （每次出行起终点是停留中心/最后采样点，可能差几百米；0.003° 网格下
      同一条通勤路往返、不同天重复走，网格集合基本一致）
    - 无序集合：去重后排序。与方向（去/回）、途经顺序无关——
      只有真正绕路换线（经过的网格集合显著变化）才会产生不同指纹。
    """
    if not points:
        return ""
    pts = points
    if len(pts) > max_points:
        idx = [round(i * (len(pts) - 1) / (max_points - 1)) for i in range(max_points)]
        pts = [pts[i] for i in idx]
    cells = set()
    for la, lo in pts:
        gk_la = round(la / grid) * grid
        gk_lo = round(lo / grid) * grid
        cells.add(f"{gk_la:.3f},{gk_lo:.3f}")
    return ";".join(sorted(cells))


def route_key_of(polyline_points: list[tuple[float, float]]) -> str:
    """由 polyline 点列生成 route_key（sha256 前 16 位）。无点返回空串。"""
    norm = _normalize_polyline(polyline_points)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def direction_polyline(
    lat1: float, lon1: float, lat2: float, lon2: float,
    key: str, mode: str = "walking",
) -> list[tuple[float, float]] | None:
    """高德路径规划：返回路线坐标点列表 [(lat, lon), ...]；失败返回 None。"""
    url = DIRECTION_URL if mode == "walking" else DIRECTION_URL.replace("/walking", f"/{mode}")
    params = urllib.parse.urlencode({
        "origin": f"{lon1},{lat1}",
        "destination": f"{lon2},{lat2}",
        "key": key,
    })
    try:
        with urllib.request.urlopen(f"{url}?{params}", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[routes] 路径规划失败 ({lat1},{lon1})->({lat2},{lon2}): {e}")
        return None
    if data.get("status") != "1":
        print(f"[routes] 路径规划业务失败: {data.get('info')}")
        return None
    try:
        steps = data["route"]["paths"][0]["steps"]
    except (KeyError, IndexError):
        print(f"[routes] 路径规划响应无路线: {data.get('info')}")
        return None
    points: list[tuple[float, float]] = []
    for st in steps:
        seg = (st.get("polyline") or "").strip()
        if not seg:
            continue
        for token in seg.split(";"):
            if not token:
                continue
            try:
                lon_s, lat_s = token.split(",")
                points.append((float(lat_s), float(lon_s)))
            except (ValueError, AttributeError):
                continue
    return points or None


def incremental_encode_trips(db_path: Path = DB_PATH, max_n: int = _MAX_ENCODE_PER_RUN) -> int:
    """增量补路：仅对 route_encoded_at IS NULL 的新移动段调高德路径规划。

    成功才写 polyline/route_key/route_mode/route_encoded_at；失败跳过留待下次重试。
    单次默认最多 max_n 段（配额节流，远低于 5000 次/日）。返回成功编码数。
    """
    key = _amap_key()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, start_lat, start_lon, end_lat, end_lon FROM trips "
        "WHERE route_encoded_at IS NULL ORDER BY start_ts LIMIT ?", (max_n,)
    ).fetchall()
    if not rows:
        print("[routes] 无新增待补路移动段（增量缓存命中，零调用）")
        conn.close()
        return 0
    print(f"[routes] 待补路移动段: {len(rows)} 段（本次上限 {max_n}）")
    now = int(time.time() * 1000)
    n = 0
    for r in rows:
        # 高德个人档 QPS 较低，连续快速调用会触发 CUQPS 限流；每段间隔 0.5s
        time.sleep(0.5)
        pts = direction_polyline(
            r["start_lat"], r["start_lon"], r["end_lat"], r["end_lon"], key, _ROUTE_MODE
        )
        if not pts:
            print(f"[routes] 跳过段 id={r['id']}（规划失败，留待下次）")
            continue
        rk = route_key_of(pts)
        conn.execute(
            "UPDATE trips SET polyline=?, route_key=?, route_mode=?, route_encoded_at=? WHERE id=?",
            (json.dumps(pts, ensure_ascii=False), rk, _ROUTE_MODE, now, r["id"]),
        )
        n += 1
    conn.commit()
    conn.close()
    print(f"[routes] 补路完成: {n} 段")
    return n


def run(db_path: Path = DB_PATH, force_all: bool = False) -> None:
    _amap_key()  # 提前校验 Key
    if force_all:
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE trips SET route_encoded_at=NULL, polyline=NULL, route_key=NULL, route_mode=NULL")
        conn.commit()
        conn.close()
    incremental_encode_trips(db_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="高德路径规划补路（L3 移动轨迹段增量编码）")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--all", action="store_true", help="强制全部重编（慎用，烧配额）")
    args = parser.parse_args()
    run(args.db, force_all=args.all)


if __name__ == "__main__":
    main()
