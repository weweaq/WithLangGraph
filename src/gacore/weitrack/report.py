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
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "weitrack.db"


def fmt_dur(ms: int) -> str:
    h, rem = divmod(ms // 1000, 3600)
    m = rem // 60
    return f"{h}小时{m}分" if h else f"{m}分钟"


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
        "SELECT label, lat, lon, visit_count FROM places ORDER BY visit_count DESC LIMIT 5"
    ).fetchall()
    for p in places:
        print(f"  [{p['label']}] ({p['lat']:.4f},{p['lon']:.4f}) 访问 {p['visit_count']} 次")

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
