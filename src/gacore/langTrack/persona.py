"""人物画像（C1 · 甲·外挂式，纯读）：聚合 B 阶段事实表，输出行为模式画像。

只读 daily_stats / sessions / stays / trips / places，不改 ETL、不加表。

这是"完全懂主人"的落地层：应用分类聚合 / 屏幕健康度 / 使用时段分布 /
生活规律 / 综合画像卡片。所有计算纯读，device 维度按 device_id 过滤
（B2 后 daily_stats 主键为 (device_id, day)；旧库无 device_id 列时退化为全量读）。

"""

from __future__ import annotations



import datetime

import json

import sqlite3

from collections import defaultdict

from pathlib import Path

from typing import Any



_CATEGORIES_FILE = Path(__file__).resolve().parents[3] / "data" / "app_categories.json"



# 内置默认分类映射（app 显示名 -> 大类）。data/app_categories.json 被 .gitignore 排除，

# 克隆仓库后该文件可能不存在；此处提供兜底，保证模块在无文件时仍产出有意义的画像。

# 若 data/app_categories.json 存在，其内容会覆盖以下默认值。

_DEFAULT_CATEGORIES: dict[str, str] = {

    "微信": "社交", "QQ": "社交", "小红书": "社交",

    "抖音": "视频", "哔哩哔哩": "视频",

    "淘宝": "购物",

    "飞书": "通讯", "WeLink": "通讯",

    "Edge": "工具", "夸克": "工具", "时钟": "工具", "天气": "工具",

    "便签": "工具", "华为乾崑": "工具",

    "网易云音乐": "其他", "Marvis": "其他",

}



# 时段划分（与 report.py _SEGMENTS 一致）：名称 / 起时 / 止时

_SEGMENTS = [

    ("凌晨", 0, 5), ("上午", 5, 11), ("午后", 11, 14),

    ("下午", 14, 18), ("晚上", 18, 23), ("深夜", 23, 24),

]

_NIGHT_SEGS = ("深夜", "凌晨")  # 23:00-05:00 归为"夜"



# 屏幕健康阈值（单日 >5h 视为重度；窗口内 >=60% 天数重度 → 重度屏幕使用者）

_DEFAULT_HEAVY_MS = 5 * 3600 * 1000

_DEFAULT_HEAVY_FRAC = 0.6



_TZ = datetime.timezone(datetime.timedelta(hours=8))





def _load_categories() -> dict:

    cats = dict(_DEFAULT_CATEGORIES)

    try:

        file_cats = json.loads(_CATEGORIES_FILE.read_text(encoding="utf-8"))

    except (FileNotFoundError, json.JSONDecodeError):

        return cats

    cats.update(file_cats)

    return cats





def _seg_of(hour: int) -> str:

    for name, a, b in _SEGMENTS:

        if a <= hour < b:

            return name

    return "深夜"





def _has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:

    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]

    return col in cols





def _avg_hhmm(ts_list: list) -> str | None:

    if not ts_list:

        return None

    dts = [datetime.datetime.fromtimestamp(t / 1000, tz=_TZ) for (t,) in ts_list]

    mins = sum(d.hour * 60 + d.minute for d in dts) / len(dts)

    return f"{int(mins // 60):02d}:{int(mins % 60):02d}"





def build(conn: sqlite3.Connection | None = None, device_id: str | None = None,

          days: int = 7, db_path: str | Path | None = None) -> dict[str, Any]:

    """构建人物画像。conn 与 db_path 二选一；device_id 为空则全量（单设备兼容）。"""

    own = False

    if conn is None:

        if db_path is None:

            db_path = Path(__file__).resolve().parents[3] / "data" / "langTrack.db"

        conn = sqlite3.connect(str(db_path))

        own = True

    try:

        return _build(conn, device_id, days)

    finally:

        if own:

            conn.close()





def _build(conn: sqlite3.Connection, device_id: str | None, days: int) -> dict[str, Any]:

    cur = conn.cursor()

    cats = _load_categories()



    dev_filter = ""

    dparams: list = []

    if device_id and _has_col(conn, "daily_stats", "device_id"):

        dev_filter = "WHERE device_id=?"

        dparams = [device_id]



    rows = cur.execute(

        f"SELECT day, total_screen_ms, app_ranking_json FROM daily_stats {dev_filter} "

        f"ORDER BY day DESC LIMIT ?",

        dparams + [days],

    ).fetchall()



    result: dict[str, Any] = {

        "device_id": device_id,

        "days": days,

        "available": False,

        "category_usage": [],

        "uncategorized": [],

        "screen_health": {},

        "rhythm": {},

        "routine": {},

        "traits": [],

        "card": "",

    }

    if not rows:

        return result

    result["available"] = True

    min_day = rows[-1][0]



    # ---- 1. 应用分类聚合 ----

    cat_ms = defaultdict(int)

    uncat: set[str] = set()

    total_app_ms = 0

    for _day, _total, ranking_json in rows:

        for a in (json.loads(ranking_json or "[]") or []):

            name = a.get("app")

            ms = a.get("ms", 0)

            if not name:

                continue

            total_app_ms += ms

            cat = cats.get(name)

            if cat is None:

                uncat.add(name)

                cat = "其他"

            cat_ms[cat] += ms

    cat_list = sorted(

        (

            {

                "category": c,

                "ms": m,

                "hours": round(m / 3600000, 2),

                "pct": round(m / total_app_ms * 100, 1) if total_app_ms else 0,

            }

            for c, m in cat_ms.items()

        ),

        key=lambda x: -x["ms"],

    )

    result["category_usage"] = cat_list

    result["uncategorized"] = sorted(uncat)



    # ---- 2. 屏幕健康度 ----

    totals = [r[1] for r in rows]

    avg = sum(totals) // len(totals)

    if len(totals) >= 2:

        last = totals[0]

        rest_avg = sum(totals[1:]) // len(totals[1:])

        if rest_avg and abs(last - rest_avg) / rest_avg > 0.1:

            trend = "up" if last > rest_avg else "down"

        else:

            trend = "flat"

    else:

        trend = "flat"

    heavy_days = sum(1 for t in totals if t > _DEFAULT_HEAVY_MS)

    heavy_user = (heavy_days / len(totals)) >= _DEFAULT_HEAVY_FRAC

    result["screen_health"] = {

        "avg_total_ms": avg,

        "avg_hours": round(avg / 3600000, 2),

        "trend": trend,

        "heavy_user": heavy_user,

        "heavy_days": heavy_days,

        "note": "重度屏幕使用者" if heavy_user else "屏幕使用在正常区间",

    }



    # ---- 3. 使用时段分布（rhythm）----

    sparams: list = [min_day]

    sfilter = ""

    if device_id:

        sfilter = "AND device_id=?"

        sparams = [min_day, device_id]

    seg_ms = defaultdict(int)
    try:
        _sess_rows = cur.execute(
            f"SELECT start_ms, duration_ms FROM sessions WHERE day>=? {sfilter}",
            sparams,
        ).fetchall()
    except sqlite3.OperationalError:
        # 极简库/测试夹具可能无 sessions 表 -> 节奏维度退化为空
        _sess_rows = []
    for start_ms, dur in _sess_rows:
        if not start_ms:
            continue
        dt = datetime.datetime.fromtimestamp(start_ms / 1000, tz=_TZ)
        seg_ms[_seg_of(dt.hour)] += dur

    total_seg = sum(seg_ms.values()) or 1

    night_ms = sum(seg_ms[s] for s in _NIGHT_SEGS)

    night_pct = round(night_ms / total_seg * 100, 1)

    night_owl = night_pct >= 25

    peak_seg = max(seg_ms, key=seg_ms.get) if seg_ms else None

    result["rhythm"] = {

        "segments": dict(seg_ms),

        "night_pct": night_pct,

        "night_owl": night_owl,

        "peak_segment": peak_seg,

    }



    # ---- 4. 生活规律（routine）----

    pfilter = ""

    pparams: list = []

    if device_id:

        pfilter = "AND s.device_id=?"

        pparams = [device_id]

    try:
        home = cur.execute(
            "SELECT s.start_ts FROM stays s JOIN places p ON s.grid_key=p.grid_key "
            "AND s.device_id=p.device_id WHERE p.label='家' " + pfilter,
            pparams,
        ).fetchall()
        work = cur.execute(
            "SELECT s.start_ts FROM stays s JOIN places p ON s.grid_key=p.grid_key "
            "AND s.device_id=p.device_id WHERE p.label='公司' " + pfilter,
            pparams,
        ).fetchall()
    except sqlite3.OperationalError:
        # 极简库/测试夹具可能无 stays/places 表 -> 规律维度退化为空
        home, work = [], []
    work_start = _avg_hhmm(work)

    home_n, work_n = len(home), len(work)

    regular = home_n >= 3 and work_n >= 3

    tparams: list = [min_day]

    tfilter = ""

    if device_id:

        tfilter = "AND device_id=?"

        tparams = [min_day, device_id]

    try:
        trips = cur.execute(
            f"SELECT start_ts FROM trips WHERE day>=? {tfilter}", tparams
        ).fetchall()
    except sqlite3.OperationalError:
        # 极简库/测试夹具可能无 trips 表 -> 通勤稳定性退化为 False
        trips = []
    commute_stable = len(trips) >= 3

    note = []

    if regular:

        note.append("作息规律")

    if work_start:

        note.append(f"约 {work_start} 出门上班" if regular else f"约 {work_start} 开始活动")

    result["routine"] = {

        "regular": regular,

        "work_start": work_start,

        "commute_stable": commute_stable,

        "home_days": home_n,

        "work_days": work_n,

        "note": "；".join(note) if note else "作息数据不足",

    }



    # ---- 5. 特征 + 画像卡片 ----

    traits: list[str] = []

    video = next((c for c in cat_list if c["category"] == "视频"), None)

    if video and (video["pct"] >= 25 or video is cat_list[0]):

        traits.append(f"重度视频消费者（日均约 {video['hours']}h）")

    social = next((c for c in cat_list if c["category"] == "社交"), None)

    if social and social is cat_list[0]:

        traits.append("社交重度用户")

    if heavy_user:

        traits.append("重度屏幕使用者")

    if night_owl:

        traits.append(f"夜猫子（深夜活跃占比 {night_pct}%）")

    if regular:

        traits.append("作息规律")



    parts = []

    if regular:

        parts.append("作息规律的通勤上班族")

    elif work_start:

        parts.append(f"约 {work_start} 开始一天活动")

    else:

        parts.append("生活节奏数据尚少")

    if video:

        parts.append(

            f"视频消费者（日均 {video['hours']}h{'，居首' if video is cat_list[0] else ''}）"

        )

    if social:

        parts.append(f"社交{'重度' if social is cat_list[0] else '轻度'}")

    if night_owl:

        parts.append("典型夜猫子")

    card = "；".join(parts) + "。"



    result["traits"] = traits

    result["card"] = card

    return result
