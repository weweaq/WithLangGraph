"""langTrack Web 仪表盘：基于事实表生成深色仪表盘 HTML（与 App 主题一致）。

用法：FastAPI 挂载 GET /dashboard → render_dashboard_html(conn, day)
不引第三方框架，纯内联 CSS/JS。数据来源：sessions / daily_stats / places（ETL 产出）。
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from html import escape
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "langTrack.db"

PAGE_CSS = """
:root{--bg:#05060A;--card:#11151F;--line:#1C2333;--ink:#E9EDF6;--ink2:#96A0B4;
--ink3:#5A6378;--cyan:#22D3EE;--green:#3FBF8F;--rose:#F26B5C;--amber:#F2A65A;--violet:#8A7AD8}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding:24px;max-width:860px;margin:0 auto}
h1{font-size:22px;font-weight:800;margin-bottom:4px}
.sub{color:var(--ink3);font-size:13px;margin-bottom:20px}
.nav{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}
.nav a{color:var(--ink2);text-decoration:none;font-size:13px;padding:6px 14px;border-radius:9px;
background:var(--card);border:1px solid var(--line)}
.nav a.on{color:var(--cyan);border-color:rgba(34,211,238,.4);background:rgba(34,211,238,.08)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.stat .v{font-size:26px;font-weight:800;font-variant-numeric:tabular-nums}
.stat .l{font-size:11px;color:var(--ink3);margin-top:2px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:16px}
.card h2{font-size:13px;color:var(--ink2);letter-spacing:.05em;margin-bottom:12px;font-weight:700}
.row{display:flex;align-items:center;padding:6px 0;font-size:13px}
.row .name{flex:1;color:var(--ink)}
.row .val{color:var(--ink2);font-variant-numeric:tabular-nums}
.bar-wrap{flex:1;height:8px;background:#0D1220;border-radius:4px;margin-left:12px;overflow:hidden}
.bar{height:100%;border-radius:4px}
.hbar{display:flex;gap:2px;height:44px;align-items:flex-end;margin-top:8px}
.hcell{flex:1;border-radius:3px 3px 0 0;position:relative}
.hcell:hover::after{content:attr(data-n);position:absolute;top:-20px;left:50%;transform:translateX(-50%);
background:#1A2234;padding:2px 6px;border-radius:6px;font-size:10px;color:var(--ink);white-space:nowrap}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-size:11px;color:var(--ink3)}
.legend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{color:var(--ink3);text-align:left;font-weight:600;padding:6px 8px;border-bottom:1px solid var(--line);font-size:11px}
.tbl td{padding:7px 8px;border-bottom:1px solid #161D2E}
.empty{color:var(--ink3);font-size:13px;padding:20px;text-align:center}
.badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px}
"""


def _fmt_dur(ms: int) -> str:
    h, rem = divmod(ms // 1000, 3600)
    m = rem // 60
    return f"{h}小时{m}分" if h else f"{m}分钟"


def _fmt_time(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def _app_color(idx: int) -> str:
    palette = ["#22D3EE", "#F26B5C", "#3FBF8F", "#F2A65A", "#8A7AD8",
               "#E58FC4", "#7FB0E6", "#4ADE80", "#F87171", "#60A5FA"]
    return palette[idx % len(palette)]


def _hours_activity(conn: sqlite3.Connection, day: str) -> list[int]:
    """24h 亮屏活动分布：按 snapshot.screen=true 聚合。"""
    hours = [0] * 24
    rows = conn.execute(
        "SELECT strftime('%H', ts/1000,'unixepoch','+8 hours') h "
        "FROM events WHERE type='snapshot' AND "
        "date(ts/1000,'unixepoch','+8 hours')=? AND payload LIKE '%\"screen\":true%'",
        (day,),
    ).fetchall()
    for (h,) in rows:
        try:
            hours[int(h)] += 1
        except (ValueError, TypeError):
            pass
    return hours


def render_dashboard_html(conn: sqlite3.Connection, day: str | None = None) -> str:
    conn.row_factory = sqlite3.Row
    day = day or datetime.datetime.now().strftime("%Y-%m-%d")

    # 可用日期列表（daily_stats）
    days = [r["day"] for r in conn.execute("SELECT day FROM daily_stats ORDER BY day DESC")]
    if not days:
        return "<html><body><h1>暂无数据</h1><p>先运行 ETL: python -m gacore.langTrack.etl</p></body></html>"
    if day not in days:
        day = days[0]

    # 日期导航
    nav = "".join(
        f'<a href="/dashboard?day={d}" class="{"on" if d == day else ""}">{d}</a>' for d in days
    )

    stat = conn.execute("SELECT * FROM daily_stats WHERE day=?", (day,)).fetchone()
    if not stat:
        body = f'<div class="empty">当日无数据</div>'
    else:
        ranking = json.loads(stat["app_ranking_json"] or "[]")
        notif_apps = json.loads(stat["top_notification_apps_json"] or "[]")
        total_h = stat["total_screen_ms"] / 3600000

        # 统计卡
        click_rate = stat["notification_clicked"] / max(1, stat["notification_count"]) * 100
        stats = f"""
        <div class="grid">
          <div class="stat"><div class="v" style="color:var(--cyan)">{total_h:.2f}h</div><div class="l">屏幕时间</div></div>
          <div class="stat"><div class="v" style="color:var(--amber)">{stat['notification_count']}</div><div class="l">通知 / 点击率 {click_rate:.0f}%</div></div>
          <div class="stat"><div class="v" style="color:var(--green)">{stat['unlock_count']}</div><div class="l">解锁次数</div></div>
          <div class="stat"><div class="v" style="color:var(--violet)">{stat['switch_count']}</div><div class="l">切换次数</div></div>
        </div>"""

        # App 排行
        max_ms = ranking[0]["ms"] if ranking else 1
        app_rows = ""
        for i, a in enumerate(ranking[:8]):
            pct = a["ms"] / max_ms * 100
            app_rows += (
                f'<div class="row"><span class="name">{escape(a["app"])}</span>'
                f'<div class="bar-wrap"><div class="bar" style="width:{pct:.0f}%;background:{_app_color(i)}"></div></div>'
                f'<span class="val" style="margin-left:12px;width:70px;text-align:right">{_fmt_dur(a["ms"])}</span></div>'
            )

        # 通知来源
        notif_rows = "".join(
            f'<div class="row"><span class="name">{escape(a["app"])}</span>'
            f'<span class="val">{a["n"]} 条</span></div>' for a in notif_apps
        ) or '<div class="empty">无通知</div>'

        # 场景分布
        places = conn.execute(
            "SELECT label, visit_count FROM places ORDER BY visit_count DESC LIMIT 4"
        ).fetchall()
        place_rows = "".join(
            f'<tr><td><span class="badge" style="background:rgba(34,211,238,.1);color:var(--cyan)">{escape(r["label"])}</span></td>'
            f'<td>{r["visit_count"]} 次</td></tr>' for r in places
        )

        # 24h 活动热力
        hours = _hours_activity(conn, day)
        max_h = max(hours) if any(hours) else 1
        hcells = ""
        for hi, n in enumerate(hours):
            hgt = max(3, n / max_h * 100)
            color = "var(--cyan)" if n > 0 else "#161D2E"
            hcells += (
                f'<div class="hcell" data-n="{hi}:00 {n}条" '
                f'style="height:{hgt:.0f}%;background:{color}"></div>'
            )

        # 最近会话
        recent = conn.execute(
            "SELECT app, duration_ms, start_ms FROM sessions WHERE day=? ORDER BY start_ms DESC LIMIT 8",
            (day,),
        ).fetchall()
        recent_rows = "".join(
            f'<tr><td>{_fmt_time(r["start_ms"])}</td><td>{escape(r["app"])}</td>'
            f'<td>{_fmt_dur(r["duration_ms"])}</td></tr>' for r in recent
        ) or '<tr><td colspan="3">无</td></tr>'

        body = f"""
        {stats}
        <div class="card">
          <h2>24 小时活动</h2>
          <div class="hbar">{hcells}</div>
        </div>
        <div class="grid" style="grid-template-columns:1fr 1fr">
          <div class="card"><h2>App 使用排行</h2>{app_rows}</div>
          <div>
            <div class="card"><h2>通知来源</h2>{notif_rows}</div>
            <div class="card"><h2>场景分布</h2>
              <table class="tbl">{place_rows}</table>
            </div>
          </div>
        </div>
        <div class="card"><h2>最近会话</h2>
          <table class="tbl"><tr><th>时间</th><th>App</th><th>时长</th></tr>{recent_rows}</table>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>langTrack · {day}</title><style>{PAGE_CSS}</style></head>
<body>
<h1>langTrack 数据仪表盘</h1>
<div class="sub">自用数据监控 · 数据源: events + ETL 事实表</div>
<div class="nav">{nav}</div>
{body}
<div class="sub" style="margin-top:30px">© 高德地图 · 场景标签为逆地理编码结果</div>
</body></html>"""


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    print(render_dashboard_html(conn))
    conn.close()
