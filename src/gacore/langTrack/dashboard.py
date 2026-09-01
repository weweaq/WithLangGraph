"""langTrack Web 仪表盘：基于事实卡片（fact_card）渲染深色仪表盘 HTML（与 App 主版一致）。

用法：FastAPI 挂载 GET /dashboard 即调用 render_dashboard_html(conn, day)。
数据源：sessions / daily_stats / places（ETL 产出），并在 persona 卡片上方插入
「事实审查」块（来自 fact_card 注入的紧凑事实卡），保证数据口径与消息拼装一致。
"""

from __future__ import annotations

import datetime
import sqlite3
from html import escape
from pathlib import Path

from gacore.langTrack import fact_card

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "langTrack.db"
_TZ = datetime.timezone(datetime.timedelta(hours=8))

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
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.chip{display:inline-block;padding:3px 9px;border-radius:8px;font-size:12px;border:1px solid var(--line);background:#0D1220}
.compact-pre{white-space:pre-wrap;font-family:ui-monospace,Consolas,monospace;font-size:12px;
color:var(--cyan);background:#0B0F18;border:1px solid rgba(34,211,238,.25);border-radius:10px;
padding:12px;line-height:1.6;margin-top:8px}
.meta{font-size:11px;color:var(--ink3);line-height:1.8;margin-bottom:8px}
.meta b{color:var(--ink2);font-weight:600}
.review-note{font-size:11px;color:var(--ink3);margin:-6px 0 10px}
"""


def _fmt_dur(ms: int) -> str:
    h, rem = divmod(ms // 1000, 3600)
    m = rem // 60
    return f"{h}小时{m}分" if h else f"{m}分钟"


def _fmt_time(ms: int) -> str:
    """本地时区 HH:MM（用于原 dashboard 会话列表）。"""
    return datetime.datetime.fromtimestamp(ms / 1000, tz=_TZ).strftime("%H:%M")


def _app_color(idx: int) -> str:
    palette = ["#22D3EE", "#F26B5C", "#3FBF8F", "#F2A65A", "#8A7AD8",
               "#E58FC4", "#7FB0E6", "#4ADE80", "#F87171", "#60A5FA"]
    return palette[idx % len(palette)]


def _collect_days(conn: sqlite3.Connection) -> list[str]:
    """日期导航候选：合并 daily_stats / stays / anomalies 的 day 并集，降序。"""
    days: set[str] = set()
    for table in ("daily_stats", "stays", "anomalies"):
        try:
            for r in conn.execute(f"SELECT DISTINCT day FROM {table}"):
                if r["day"]:
                    days.add(r["day"])
        except sqlite3.OperationalError:
            continue
    return sorted(days, reverse=True)


def _hours_activity(conn: sqlite3.Connection, device_id: str, day: str) -> list[int]:
    """24h 亮屏活动分布：按 snapshot.screen=true 聚合（按设备过滤）。"""
    hours = [0] * 24
    rows = conn.execute(
        "SELECT strftime('%H', ts/1000,'unixepoch','+8 hours') h "
        "FROM events WHERE type='snapshot' AND device_id=? AND "
        "date(ts/1000,'unixepoch','+8 hours')=? AND payload LIKE '%\"screen\":true%'",
        (device_id, day),
    ).fetchall()
    for (h,) in rows:
        try:
            hours[int(h)] += 1
        except (ValueError, TypeError):
            pass
    return hours


def _render_coverage(conn: sqlite3.Connection) -> str:
    """A① 采集覆盖卡片：把 contract_coverage 全量渲染为按状态着色的 chip。"""
    rows = conn.execute(
        "SELECT type, desc, status FROM contract_coverage ORDER BY "
        "(CASE status WHEN 'missing' THEN 1 WHEN 'stale' THEN 2 "
        "WHEN 'unexpected' THEN 3 ELSE 4 END), type"
    ).fetchall()
    if not rows:
        return ""
    color_map = {
        "ok": "var(--green)", "stale": "var(--amber)",
        "missing": "var(--rose)", "unexpected": "var(--ink2)",
    }
    sym_map = {
        "ok": "OK", "stale": "STALE", "missing": "MISS", "unexpected": "UNEX",
    }
    chips = ""
    for r in rows:
        c = color_map.get(r["status"], "var(--ink2)")
        sym = sym_map.get(r["status"], "?")
        chips += (
            f'<span class="chip" title="{escape(r["desc"] or r["type"])}" '
            f'style="border-color:{c};color:{c}">{sym} {escape(r["type"])}</span>'
        )
    return (
        f'<div class="card"><h2>采集覆盖（契约 {len(rows)} 类）</h2>'
        f'<div class="chips">{chips}</div></div>'
    )


def _render_persona(p: dict) -> str:
    """C1 人物画像卡片：直接渲染 fact_card 注入的 persona（不再二次查询）。"""
    if not p or not p.get("available"):
        return '<div class="card"><h2>人物画像</h2><div class="empty">近 7 日无数据</div></div>'
    traits = p.get("traits", [])
    trait_chips = "".join(
        f'<span class="chip" style="border-color:var(--violet);color:var(--violet)">{escape(t)}</span>'
        for t in traits
    ) or '<span class="chip">数据较少</span>'
    cats = p.get("category_usage", [])[:5]
    cat_rows = "".join(
        f'<div class="row"><span class="name">{escape(c["category"])}</span>'
        f'<span class="val">{c["hours"]}h · {c["pct"]}%</span></div>'
        for c in cats
    )
    sh = p.get("screen_health", {})
    rh = p.get("rhythm", {})
    return (
        f'<div class="card"><h2>人物画像（近 7 天）</h2>'
        f'<div class="chips">{trait_chips}</div>'
        f'<div class="row"><span class="name">日均屏幕</span>'
        f'<span class="val">{sh.get("avg_hours", 0)}h · {sh.get("note", "")}</span></div>'
        f'<div class="row"><span class="name">深夜活跃</span>'
        f'<span class="val">{rh.get("night_pct", 0)}% · {"夜猫子" if rh.get("night_owl") else "正常"}</span></div>'
        f'<div style="margin-top:8px;font-size:12px;color:var(--ink2)">{escape(p.get("card", ""))}</div>'
        f'<div style="margin-top:10px"><div class="row"><span class="name" style="color:var(--ink3);font-size:11px">分类时长 Top</span></div>{cat_rows}</div>'
        f'</div>'
    )


def _render_card_meta(card: dict) -> str:
    """事实审查旁注：card_fp / 生成时刻 / 数据水位 / 裁剪明细。"""
    rows: list[str] = []
    om = card.get("compact_omitted") or {}
    om_txt = "；".join(f"{k}:{v}" for k, v in om.items()) or "-"
    meta_items = [
        ("card_fp", card.get("card_fp", "")),
        ("generated_at", card.get("generated_at", "")),
        ("etl_watermark", card.get("etl_watermark", "")),
        ("data_as_of", f'{card.get("data_as_of", "")}（{card.get("data_as_of_source", "")}）'),
        ("location_as_of", card.get("location_as_of", "")),
        ("data_age_min", card.get("data_age_min", "")),
        ("compact_lines", "、".join(card.get("compact_lines", [])) or "-"),
        ("compact_omitted", om_txt),
    ]
    for k, v in meta_items:
        rows.append(f'<div><b>{escape(k)}</b>：{escape(str(v))}</div>')
    return f'<div class="meta">数据水位{""} <br/>{"".join(rows)}</div>'


def _render_timeline(card: dict) -> str:
    """今日轨迹 / 当前已知。"""
    stays = card.get("stays") or []
    ck = card.get("current_known")
    parts: list[str] = []
    if stays:
        seq = " → ".join(f'{escape(s["label"])} {s["start_hhmm"]}-{s["end_hhmm"]}' for s in stays)
        parts.append(
            f'<div class="row"><span class="name">今日轨迹</span>'
            f'<span class="val">{seq}</span></div>'
        )
    if ck:
        txt = f'{escape(ck["label"])} {ck["since_hhmm"]}-{ck["observed_until_hhmm"]}'
        if ck.get("poi"):
            txt += f' · {escape(ck["poi"])}'
        if ck.get("district"):
            txt += f' · {escape(ck["district"])}'
        parts.append(
            f'<div class="row"><span class="name">当前已知</span>'
            f'<span class="val">{txt}</span></div>'
        )
    return "".join(parts)


def _render_stay_trip_anomaly(card: dict) -> str:
    """当日停留 / 移动 / 异常三张表。"""
    stays = card.get("stays") or []
    trips = card.get("trips") or []
    anoms = card.get("anomalies") or []

    stay_rows = "".join(
        f'<tr><td>{escape(s["label"])}</td><td>{s["start_hhmm"]}-{s["end_hhmm"]}</td></tr>'
        for s in stays
    ) or '<tr><td colspan="2" class="empty">无</td></tr>'

    trip_rows = "".join(
        f'<tr><td>{escape(t["from_label"])} → {escape(t["to_label"])}</td>'
        f'<td>{t["start_hhmm"]}-{t["end_hhmm"]}</td><td>{t["dist_m"]} m</td></tr>'
        for t in trips
    ) or '<tr><td colspan="3" class="empty">无</td></tr>'

    anom_rows = "".join(
        f'<tr><td>{escape(a["kind"])}</td><td>{escape(a["poi"])}</td>'
        f'<td>{escape(a["detail"])}</td></tr>'
        for a in anoms
    ) or '<tr><td colspan="3" class="empty">无</td></tr>'

    return f"""
    <div style="margin-top:10px;font-size:11px;color:var(--ink3)">当日停留</div>
    <table class="tbl"><tr><th>位置</th><th>时段</th></tr>{stay_rows}</table>
    <div style="margin-top:10px;font-size:11px;color:var(--ink3)">当日移动（端点直距，非导航距离）</div>
    <table class="tbl"><tr><th>区间</th><th>时段</th><th>距离</th></tr>{trip_rows}</table>
    <div style="margin-top:10px;font-size:11px;color:var(--ink3)">当日系统标记 / 异常</div>
    <table class="tbl"><tr><th>类型</th><th>地点</th><th>详情</th></tr>{anom_rows}</table>
    """


def _render_day_summary(card: dict) -> str:
    """当日汇总：停留累计 + 常驻点（全历史 location 点数，不谎称到访次数）。"""
    sm = card.get("stay_minutes") or {}
    stay_min_txt = " · ".join(
        f"{k} {v // 60}h{v % 60}m" for k, v in sorted(sm.items()) if v
    ) or "-"
    places = card.get("places") or []
    place_rows = "".join(
        f'<tr><td>{escape(p.get("label", ""))}</td><td>{escape(p.get("poi", ""))}</td>'
        f'<td>{p.get("visits", 0)}</td></tr>'
        for p in places
    ) or '<tr><td colspan="3" class="empty">无</td></tr>'
    return f"""
    <div style="margin-top:10px;font-size:11px;color:var(--ink3)">当日汇总</div>
    <div class="row"><span class="name">屏幕 / 通知</span>
      <span class="val">{card.get("screen_hours", 0):.1f}h · 通知 {card.get("notification_count", 0)}（点击 {card.get("notification_clicked", 0)}）</span></div>
    <div class="row"><span class="name">停留累计</span><span class="val">{escape(stay_min_txt)}</span></div>
    <div style="margin-top:10px;font-size:11px;color:var(--ink3)">常驻点（全历史 location 点数）</div>
    <table class="tbl"><tr><th>位置</th><th>POI</th><th>点数</th></tr>{place_rows}</table>
    """


def _render_arrivals(conn: sqlite3.Connection, device_id: str, day: str) -> str:
    """当日到达（采集体检）：events 按 type 计数（只读 SQL，不属于 FactCard）。"""
    rows = conn.execute(
        "SELECT type, COUNT(*) n FROM events "
        "WHERE device_id=? AND date(ts/1000,'unixepoch','+8 hours')=? "
        "GROUP BY type ORDER BY n DESC",
        (device_id, day),
    ).fetchall()
    if not rows:
        return ""
    body_rows = "".join(
        f'<tr><td>{escape(r["type"])}</td><td>{r["n"]}</td></tr>' for r in rows
    )
    return (
        f'<div class="card"><h2>当日到达（采集体检）</h2>'
        f'<table class="tbl"><tr><th>类型</th><th>条数</th></tr>{body_rows}</table></div>'
    )


def _render_fact_review(card: dict) -> str:
    """事实审查块：compact 预览 + 旁注 + 轨迹/当前已知 + 三表 + 当日汇总。"""
    compact = card.get("compact") or ""
    if compact:
        pre = f'<pre class="compact-pre">{escape(compact)}</pre>'
    else:
        pre = '<div class="empty">当日拼不出 compact（数据不足）</div>'
    return (
        f'<div class="card" style="border-color:rgba(63,191,143,.35)">'
        f'<h2>事实审查 · {escape(card.get("day", ""))}</h2>'
        f'<div class="review-note">只含时序事实、统计与系统 tag，不含人格句（未注入 LLM）</div>'
        f'{_render_card_meta(card)}{pre}{_render_timeline(card)}'
        f'{_render_stay_trip_anomaly(card)}{_render_day_summary(card)}'
        f'</div>'
    )


def render_dashboard_html(conn: sqlite3.Connection, day: str | None = None) -> str:
    conn.row_factory = sqlite3.Row

    today = datetime.datetime.now(tz=_TZ).strftime("%Y-%m-%d")
    requested = day or today

    # 日期导航：合并 daily_stats / stays / anomalies；显式请求日即使不在列表也保留
    days = _collect_days(conn)
    nav_days = days
    if requested not in nav_days:
        nav_days = [requested] + nav_days
    nav = "".join(
        f'<a href="/dashboard?day={d}" class="{"on" if d == requested else ""}">{d}</a>'
        for d in nav_days
    )

    # 事实卡片：一次 build，全程不触发 ETL
    try:
        card = fact_card.build(conn=conn, day=requested, detail="full", outlet="dashboard")
    except Exception:  # noqa: BLE001 - 缺库/损坏时降级为读取失败
        card = None

    if card is None:
        body = ('<div class="card"><h2>读取失败</h2>'
                '<div class="empty">数据读取失败，请稍后重试。</div></div>')
    elif card.get("ambiguous_device"):
        cands = "、".join(escape(str(c)) for c in card.get("candidate_device_ids") or [])
        body = (
            f'<div class="card"><h2>多设备</h2>'
            f'<div class="empty">检测到多台设备，请指定 device_id 后查看。候选设备：{cands}</div></div>'
        )
    else:
        dev = card.get("device_id") or ""
        body = _render_fact_review(card)

        # 唯一设备确定后才执行设备级统计查询（全部按 device_id 过滤）
        total_h = card.get("screen_hours", 0)
        ncount = card.get("notification_count", 0)
        nclick = card.get("notification_clicked", 0)
        click_rate = nclick / max(1, ncount) * 100
        stats = f"""
        <div class="grid">
          <div class="stat"><div class="v" style="color:var(--cyan)">{total_h:.2f}h</div><div class="l">屏幕时间</div></div>
          <div class="stat"><div class="v" style="color:var(--amber)">{ncount}</div><div class="l">通知 / 点击率 {click_rate:.0f}%</div></div>
          <div class="stat"><div class="v" style="color:var(--green)">{card.get("unlock_count", 0)}</div><div class="l">解锁次数</div></div>
          <div class="stat"><div class="v" style="color:var(--violet)">{card.get("switch_count", 0)}</div><div class="l">切换次数</div></div>
        </div>"""

        ranking = card.get("top_apps") or []
        max_ms = ranking[0]["ms"] if ranking else 1
        app_rows = ""
        for i, a in enumerate(ranking[:8]):
            pct = a["ms"] / max_ms * 100
            app_rows += (
                f'<div class="row"><span class="name">{escape(a["app"])}</span>'
                f'<div class="bar-wrap"><div class="bar" style="width:{pct:.0f}%;background:{_app_color(i)}"></div></div>'
                f'<span class="val" style="margin-left:12px;width:70px;text-align:right">{_fmt_dur(a["ms"])}</span></div>'
            )

        notif_apps = card.get("top_notification_apps") or []
        notif_rows = "".join(
            f'<div class="row"><span class="name">{escape(a["app"])}</span>'
            f'<span class="val">{a["n"]} 条</span></div>' for a in notif_apps
        ) or '<div class="empty">无通知</div>'

        hours = _hours_activity(conn, dev, requested)
        max_h = max(hours) if any(hours) else 1
        hcells = ""
        for hi, n in enumerate(hours):
            hgt = max(3, n / max_h * 100)
            color = "var(--cyan)" if n > 0 else "#161D2E"
            hcells += (
                f'<div class="hcell" data-n="{hi}:00 {n}条" '
                f'style="height:{hgt:.0f}%;background:{color}"></div>'
            )

        recent = conn.execute(
            "SELECT app, duration_ms, start_ms FROM sessions "
            "WHERE day=? AND device_id=? ORDER BY start_ms DESC LIMIT 8",
            (requested, dev),
        ).fetchall()
        recent_rows = "".join(
            f'<tr><td>{_fmt_time(r["start_ms"])}</td><td>{escape(r["app"])}</td>'
            f'<td>{_fmt_dur(r["duration_ms"])}</td></tr>' for r in recent
        ) or '<tr><td colspan="3">无</td></tr>'

        body += f"""
        {stats}
        <div class="card"><h2>24 小时活动</h2><div class="hbar">{hcells}</div></div>
        <div class="grid" style="grid-template-columns:1fr 1fr">
          <div class="card"><h2>App 使用排行</h2>{app_rows}</div>
          <div><div class="card"><h2>通知来源</h2>{notif_rows}</div>
            <div class="card"><h2>最近会话</h2>
              <table class="tbl"><tr><th>时间</th><th>App</th><th>时长</th></tr>{recent_rows}</table>
            </div>
          </div>
        </div>
        {_render_arrivals(conn, dev, requested)}
        {_render_persona(card.get("persona"))}
        {_render_coverage(conn)}
        """

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>langTrack · {requested}</title><style>{PAGE_CSS}</style></head>
<body>
<h1>langTrack 数据仪表盘</h1>
<div class="sub">自用数据监控 · 数据源: events + ETL 事实表</div>
<div class="nav">{nav}</div>
{body}
<div class="sub" style="margin-top:30px">© 场景标签为 ETL 逆地理编码结果</div>
</body></html>"""


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    print(render_dashboard_html(conn))
    conn.close()
