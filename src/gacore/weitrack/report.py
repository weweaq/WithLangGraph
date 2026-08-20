"""weiTrack 分析报告：基于事实表生成"今天发生了什么"的数字生活画像与决策建议。

用法：
    python -m gacore.weitrack.report            # 今日
    python -m gacore.weitrack.report --day 2026-08-17
    python -m gacore.weitrack.report --day 2026-08-17 --verbose   # 含原始明细

输出分四块（对应修订文档 R6-P2）：
1. 屏幕时间（总量/app 排行/最长会话）
2. 通知疲劳（谁轰炸/几点轰炸/点击率 → 关哪些通知）
3. 睡眠推断（深夜通知/音频/亮屏）
4. 场景分布（places 标签 + wifi）
"""
from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "weitrack.db"


def fmt_dur(ms: int) -> str:
    h, rem = divmod(ms // 1000, 3600)
    m = rem // 60
    return f"{h}小时{m}分" if h else f"{m}分钟"


# P1-4 时段划分（本地时间）：名称 / 起时 / 止时
_SEGMENTS = [
    ("凌晨", 0, 5), ("上午", 5, 11), ("午后", 11, 14),
    ("下午", 14, 18), ("晚上", 18, 23), ("深夜", 23, 24),
]


def _seg_of(hour: int) -> str:
    for name, a, b in _SEGMENTS:
        if a <= hour < b:
            return name
    return "深夜"


def _fusion(conn: sqlite3.Connection, day: str) -> None:
    """P1-4 位置语义 × 使用数据按时段对齐，支撑'宅家刷手机'类叙事描述。"""
    day_start_ms = int(datetime.datetime.fromisoformat(f"{day} 00:00").timestamp()) * 1000
    day_end_ms = day_start_ms + 86400000
    stays = conn.execute(
        # 按时间窗口匹配而非 day 字段：跨天停留（如 22:00~次日08:30）的 day 记在起始日，
        # 按 day 过滤会把次日凌晨到早上的在家段切丢
        "SELECT start_ts, end_ts, grid_key FROM stays "
        "WHERE start_ts < ? AND end_ts > ? ORDER BY start_ts",
        (day_end_ms, day_start_ms),
    ).fetchall()
    sessions = conn.execute(
        "SELECT start_ms, duration_ms, app FROM sessions WHERE day=? ORDER BY start_ms", (day,)
    ).fetchall()
    if not stays and not sessions:
        print("  当日无位置/使用数据")
        return

    per_seg_screen: dict[str, int] = defaultdict(int)
    per_seg_app: dict[str, list] = defaultdict(list)
    per_seg_place: dict[str, list] = defaultdict(list)
    for s in sessions:
        h = datetime.datetime.fromtimestamp(s["start_ms"] / 1000).hour
        seg = _seg_of(h)
        per_seg_screen[seg] += s["duration_ms"]
        per_seg_app[seg].append((s["app"], s["duration_ms"]))
    for st in stays:
        s_sec = st["start_ts"] / 1000
        e_sec = st["end_ts"] / 1000
        p = conn.execute(
            "SELECT label, poi, poi_fallback FROM places WHERE grid_key=? LIMIT 1", (st["grid_key"],)
        ).fetchone()
        name = (p["poi"] or p["poi_fallback"] or st["grid_key"]) if p else st["grid_key"]
        lbl = p["label"] if p else "未知"
        for sname, a, b in _SEGMENTS:
            seg_start = day_start_ms / 1000 + a * 3600
            seg_end = day_start_ms / 1000 + b * 3600
            if s_sec < seg_end and e_sec > seg_start:  # 停留段与该时段有交集
                per_seg_place[sname].append(
                    (max(s_sec, seg_start), min(e_sec, seg_end), name, lbl)
                )

    for name, a, b in _SEGMENTS:
        places = per_seg_place.get(name)
        apps = sorted(per_seg_app.get(name, []), key=lambda x: -x[1])
        scr = per_seg_screen.get(name, 0)
        loc = ""
        if places:
            # 取该时段覆盖时长最长的停留段
            p = max(places, key=lambda x: x[1] - x[0])
            loc = f"[{p[3]}]{p[2]}"
        if scr:
            top_app = apps[0][0] if apps else "-"
            print(f"  {a:02d}:00-{b:02d}:00 {name}  {loc:<26} 屏幕 {fmt_dur(scr)}  App {top_app}")
        else:
            print(f"  {a:02d}:00-{b:02d}:00 {name}  {loc or '—'}")

    # 叙事句：比较白天(5-18)在家与在公司/外出的屏幕使用，支撑"宅家刷手机"类描述
    day_segs = ("上午", "午后", "下午")
    day_screen = sum(per_seg_screen.get(s, 0) for s in day_segs)
    def _seg_app_ms(lbl: str) -> int:
        return int(
            sum(
                max(e - b, 0) for s in day_segs for (b, e, _n, _l) in per_seg_place.get(s, []) if _l == lbl
            ) * 1000
        )
    home_ms = _seg_app_ms("家")
    work_ms = _seg_app_ms("公司")
    if day_screen > 30 * 60 * 1000 and home_ms >= work_ms and home_ms > 0:
        tops = sorted(
            [(a, m) for seg in day_segs for a, m in per_seg_app.get(seg, [])],
            key=lambda x: -x[1],
        )
        main_app = tops[0][0] if tops else "手机"
        print(f"  → 叙事：白天宅家（{fmt_dur(home_ms)}）为主，屏幕共 {fmt_dur(day_screen)}，主要打发时间的是 {main_app}")
    elif day_screen > 0 and work_ms > home_ms:
        wtops = sorted(
            [(a, m) for seg in day_segs for a, m in per_seg_app.get(seg, [])],
            key=lambda x: -x[1],
        )
        print(f"  → 叙事：白天主要在公司（{fmt_dur(work_ms)}），屏幕共 {fmt_dur(day_screen)}，工作间隙刷 {wtops and wtops[0][0] or '手机'}")
    elif day_screen > 0:
        print(f"  → 叙事：白天屏幕 {fmt_dur(day_screen)}，主要活跃地点非家非公司（外出/通勤）")


def _outings(conn: sqlite3.Connection, day: str) -> None:
    """P2：识别正式停留(stays)之外、且非家/公司的'短暂外出'，让已落库地点语义出现在画像里。

    方法：按网格聚合当日 GPS 精度较好(acc<=150)的点；每个网格内按 >45min 空档切段，
    段内 >=2 点且时间跨度 >=2min 的网格即视为一个短暂停留候选（排除家/公司等已知网格）。
    相邻(<=20min)的外出合并为一趟；落在公司停留段时间窗内的簇提示'午休外出/中途离开公司'，
    其余输出'短暂停留'叙事。不做激进拆分，仅作叙事提示。
    """
    day_start_ms = int(datetime.datetime.fromisoformat(f"{day} 00:00").timestamp()) * 1000
    day_end_ms = day_start_ms + 86400000
    known_grids = {r["grid_key"] for r in conn.execute(
        "SELECT DISTINCT grid_key FROM stays WHERE start_ts<? AND end_ts>?", (day_end_ms, day_start_ms)
    )}
    known_grids |= {r["grid_key"] for r in conn.execute(
        "SELECT grid_key FROM places WHERE label IN ('家','公司')")}
    work_windows = []
    for st in conn.execute(
        "SELECT start_ts, end_ts, grid_key FROM stays "
        "WHERE start_ts<? AND end_ts>? ORDER BY start_ts", (day_end_ms, day_start_ms)
    ):
        p = conn.execute("SELECT label FROM places WHERE grid_key=? LIMIT 1", (st["grid_key"],)).fetchone()
        if p and p["label"] == "公司":
            work_windows.append((st["start_ts"] / 1000, st["end_ts"] / 1000))

    grid_pts: dict[str, list] = defaultdict(list)
    for r in conn.execute(
        "SELECT ts, payload FROM events WHERE type='location' AND ts>=? AND ts<? ORDER BY ts",
        (day_start_ms, day_end_ms),
    ):
        try:
            pl = json.loads(r["payload"])
        except (TypeError, ValueError):
            continue
        if pl.get("provider") != "gps" or pl.get("acc", 999) > 150:
            continue
        grid_pts[f"{pl['lat']:.3f},{pl['lon']:.3f}"].append((r["ts"] / 1000, pl["lat"], pl["lon"]))

    cands = []
    for g, plist in grid_pts.items():
        if g in known_grids:
            continue
        plist.sort()
        segs, cur = [], [plist[0]]
        for i in range(1, len(plist)):
            if plist[i][0] - plist[i - 1][0] > 2700:  # 网格内 >45min 空档视为不同到访
                segs.append(cur)
                cur = [plist[i]]
            else:
                cur.append(plist[i])
        segs.append(cur)
        for seg in segs:
            if len(seg) >= 2 and seg[-1][0] - seg[0][0] >= 120:  # 至少2点、停留>=2分钟
                cands.append({"s": seg[0][0], "e": seg[-1][0], "grid": g})
    if not cands:
        return

    cands.sort(key=lambda x: x["s"])
    groups, cur = [], [cands[0]]
    for c in cands[1:]:
        if c["s"] - cur[-1]["e"] <= 1200:  # 相邻外出间隔 <=20min 视为同一趟
            cur.append(c)
        else:
            groups.append(cur)
            cur = [c]
    groups.append(cur)

    def _name(gk: str) -> str:
        p = conn.execute(
            "SELECT poi, poi_fallback, address FROM places WHERE grid_key=? LIMIT 1", (gk,)
        ).fetchone()
        if not p:
            return gk
        return p["poi"] or p["poi_fallback"] or p["address"] or gk

    print("\n■ 短暂停留/外出")
    for grp in groups:
        gs, ge = grp[0]["s"], grp[-1]["e"]
        names, seen = [], set()
        for c in grp:
            nm = _name(c["grid"])
            if nm not in seen:
                seen.add(nm)
                names.append(nm)
        place_desc = "→".join(names)
        t0 = datetime.datetime.fromtimestamp(gs).strftime("%H:%M")
        t1 = datetime.datetime.fromtimestamp(ge).strftime("%H:%M")
        mins = round((ge - gs) / 60)
        in_work = any(w0 - 30 <= gs and ge <= w1 + 30 for w0, w1 in work_windows)
        if in_work:
            hour = datetime.datetime.fromtimestamp(gs).hour
            tag = "午休外出" if 11 <= hour < 14 else "中途离开公司"
            print(f"  → {tag}：{t0}-{t1} 短暂离开公司，至 {place_desc} 一带（约 {mins} 分钟）")
        else:
            print(f"  → 短暂停留：{t0}-{t1} {place_desc} 一带（约 {mins} 分钟）")


def report(conn: sqlite3.Connection, day: str, verbose: bool = False) -> None:
    conn.row_factory = sqlite3.Row
    print("=" * 52)
    print(f"  weiTrack 数字生活画像 · {day}")
    print("=" * 52)

    # ---------- 1. 屏幕时间 ----------
    print("\n■ 屏幕时间")
    row = conn.execute(
        "SELECT * FROM daily_stats WHERE day=?", (day,)
    ).fetchone()
    if not row:
        print("  当日无数据")
        return
    print(f"  总屏幕时间: {fmt_dur(row['total_screen_ms'])}"
          f"  · 解锁 {row['unlock_count']} 次 · 切换 {row['switch_count']} 次")
    ranking = json.loads(row["app_ranking_json"] or "[]")
    for i, app in enumerate(ranking[:8], 1):
        print(f"  {i}. {app['app']}: {fmt_dur(app['ms'])}")

    # 最长连续使用段（沉浸时段）
    top = conn.execute(
        "SELECT app, duration_ms, start_ms FROM sessions WHERE day=? "
        "ORDER BY duration_ms DESC LIMIT 3", (day,)
    ).fetchall()
    if top:
        print("  最长连续使用:")
        for s in top:
            import datetime
            t = datetime.datetime.fromtimestamp(s["start_ms"] / 1000).strftime("%H:%M")
            print(f"    {t} {s['app']} {fmt_dur(s['duration_ms'])}")

    # ---------- 2. 通知疲劳 ----------
    print("\n■ 通知疲劳")
    print(f"  通知 {row['notification_count']} 条, 点击 {row['notification_clicked']} 条"
          f" (点击率 {row['notification_clicked'] / max(1, row['notification_count']) * 100:.0f}%)")
    notif_apps = json.loads(row["top_notification_apps_json"] or "[]")
    if notif_apps:
        print("  通知来源:")
        for a in notif_apps:
            print(f"    {a['app']}: {a['n']} 条")

    # 每小时分布（找轰炸时段）
    hours = conn.execute(
        "SELECT strftime('%H', ts/1000,'unixepoch','+8 hours') h, COUNT(*) n "
        "FROM events WHERE type='notification' AND date(ts/1000,'unixepoch','+8 hours')=? "
        "GROUP BY h ORDER BY h", (day,)
    ).fetchall()
    if hours:
        peak = max(hours, key=lambda r: r["n"])
        print(f"  通知高峰: {peak['h']}:00 ({peak['n']} 条)")
        for r in hours:
            bar = "#" * min(r["n"], 30)
            print(f"    {r['h']}:00 {r['n']:3d} {bar}")

    # 高量低点击 → 建议关闭
    if notif_apps:
        print("  降噪建议:")
        for a in notif_apps:
            if a["n"] >= 3:
                print(f"    「{a['app']}」{a['n']} 条 — 若多为无关推送可考虑关通知")

    # ---------- 3. 睡眠推断 ----------
    print("\n■ 睡眠推断（粗略）")
    late_notif = conn.execute(
        "SELECT COUNT(*) n FROM events WHERE type='notification' "
        "AND date(ts/1000,'unixepoch','+8 hours')=? "
        "AND strftime('%H', ts/1000,'unixepoch','+8 hours') BETWEEN '22' AND '23'", (day,)
    ).fetchone()["n"]
    midnight_audio = conn.execute(
        "SELECT COUNT(*) n FROM events WHERE type='audio_env' "
        "AND date(ts/1000,'unixepoch','+8 hours')=? "
        "AND strftime('%H', ts/1000,'unixepoch','+8 hours') BETWEEN '00' AND '05'", (day,)
    ).fetchone()["n"]
    screen_on = row["screen_on_count"]
    if midnight_audio > 5:
        print(f"  凌晨 00-05 点仍有 {midnight_audio} 条环境音频样本 → 疑似熬夜")
    if late_notif:
        print(f"  22-23 点收到 {late_notif} 条通知 → 睡前仍被手机打扰")
    print(f"  亮屏 {screen_on} 次, 解锁 {row['unlock_count']} 次")

    # ---------- 4. 场景分布 ----------
    print("\n■ 场景分布")
    places = conn.execute(
        "SELECT label, lat, lon, visit_count, candidate_label, confidence_home, confidence_work, "
        "poi, poi_fallback "
        "FROM places ORDER BY visit_count DESC LIMIT 5"
    ).fetchall()
    for p in places:
        # P2：优先 label；未知且无候选时用已落库 poi 名展示，不再一律藏成 [未知]
        poi_name = p["poi"] or p["poi_fallback"] or ""
        disp = p["label"]
        if p["label"] == "未知":
            if p["candidate_label"]:
                disp = f"疑似{p['candidate_label']}(待确认)"
            else:
                disp = poi_name or "未知"
        print(f"  [{disp}] ({p['lat']:.4f},{p['lon']:.4f}) 访问 {p['visit_count']} 次"
              + (f" · {poi_name}" if poi_name and poi_name != disp else ""))

    # ---------- 4.5 短暂停留/外出（P2：让'去了哪'如实出现在画像里） ----------
    _outings(conn, day)

    # ---------- 5. 家/公司确认（P1-1 画像确认闭环入口） ----------
    print("\n■ 家/公司确认")
    pending = conn.execute(
        "SELECT grid_key, lat, lon, visit_count, candidate_label, "
        "confidence_home, confidence_work, poi "
        "FROM places WHERE label='未知' AND candidate_label IS NOT NULL "
        "ORDER BY visit_count DESC LIMIT 6"
    ).fetchall()
    if pending:
        for p in pending:
            conf = f"家 {p['confidence_home']:.2f} / 公司 {p['confidence_work']:.2f}"
            print(f"  [疑似{p['candidate_label']}] ({p['lat']:.4f},{p['lon']:.4f}) "
                  f"访问 {p['visit_count']} 次 · {conf} · {p['poi'] or '无POI'}")
        print("  → 运行 python -m gacore.weitrack.label_places 确认定名，"
              "确认后写入 data/place_labels.json 并在下次 ETL 正式生效")
    else:
        print("  暂无待确认候选点")

    # ---------- 6. 新地点/异常事件（P1-3 打破规律的点，作画像叙事节点） ----------
    print("\n■ 新地点/异常事件")
    has_anomalies = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='anomalies'"
    ).fetchone()
    if not has_anomalies:
        print("  （anomalies 表未建立，请先重跑 ETL）")
    else:
        anoms = conn.execute(
            "SELECT day, kind, poi, detail FROM anomalies WHERE day=? ORDER BY ts", (day,)
        ).fetchall()
        if not anoms:
            print("  今日无异常事件")
        for a in anoms:
            kind_label = {
                "new_place": "新地点",
                "late_night_out": "深夜外出",
                "off_schedule": "缺席办公",
            }.get(a["kind"], a["kind"])
            print(f"  [{kind_label}] {a['detail']}")

    # ---------- 7. 时段·位置·App 融合（P1-4 位置语义与使用数据对齐） ----------
    print("\n■ 时段·位置·App 融合")
    _fusion(conn, day)

    if verbose:
        print("\n■ 原始明细（usage 今日）")
        for s in conn.execute(
            "SELECT app, duration_ms, start_ms FROM sessions WHERE day=? "
            "ORDER BY start_ms LIMIT 20", (day,)
        ):
            import datetime
            t = datetime.datetime.fromtimestamp(s["start_ms"] / 1000).strftime("%H:%M")
            print(f"    {t} {s['app']} {fmt_dur(s['duration_ms'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="weiTrack 分析报告")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--day", default=None, help="日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    import datetime
    day = args.day or datetime.datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(args.db)
    report(conn, day, args.verbose)
    conn.close()


if __name__ == "__main__":
    main()
