"""高德逆地理编码：把 places 表中的常驻点坐标 → 地址/场景标签（家/公司/未知）。

用法：
    python -m gacore.weitrack.geocode            # 对全部无标签常驻点编码
    python -m gacore.weitrack.geocode --label 家  # 给编码结果统一标"家"

依赖环境变量 AMAP_KEY（.env 中配置，高德 Web 服务 Key）。
版权合规：结果展示需标注"高德地图"。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "weitrack.db"
AMAP_URL = "https://restapi.amap.com/v3/geocode/regeo"


def _amap_key() -> str:
    key = os.environ.get("AMAP_KEY", "")
    if not key:
        # 尝试从 .env 读取（简单解析，不引第三方库）
        env_path = Path(__file__).resolve().parents[3] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("AMAP_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not key:
        raise SystemExit("[geocode] 未配置 AMAP_KEY（.env 中设置，或环境变量）")
    return key


def reverse_geocode(lat: float, lon: float, key: str) -> dict | None:
    """调用高德 regeo 接口，返回地址/兴趣点信息。"""
    params = urllib.parse.urlencode({
        "location": f"{lon},{lat}",  # 高德要求 经度,纬度
        "key": key,
        "extensions": "base",
        "radius": "500",
    })
    try:
        with urllib.request.urlopen(f"{AMAP_URL}?{params}", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") != "1":
            return None
        regeocode = data.get("regeocode", {})
        formatted = regeocode.get("formatted_address", "")
        address = regeocode.get("addressComponent", {})
        # 语义标签：优先用 poi 名称（公司/小区/商场），回退行政区
        poi_name = ""
        pois = regeocode.get("pois") or []
        if pois:
            poi_name = pois[0].get("name", "")
        return {
            "formatted": formatted,
            "poi": poi_name,
            "province": address.get("province", ""),
            "city": address.get("city", "") or address.get("province", ""),
            "district": address.get("district", ""),
            "township": address.get("township", ""),
        }
    except Exception as e:
        print(f"[geocode] 请求失败 ({lat},{lon}): {e}")
        return None


def infer_label(info: dict | None) -> str:
    """根据逆编码结果推断常驻点语义：家 / 公司 / 未知。"""
    if not info:
        return "未知"
    text = " ".join([info.get("poi", ""), info.get("formatted", "")])
    home_kw = ["小区", "家园", "公寓", "新村", "住宅", "栋"]
    work_kw = ["科技园", "大厦", "中心", "产业园", "写字楼", "广场", "软件园", "金融城"]
    if any(k in text for k in work_kw):
        return "公司"
    if any(k in text for k in home_kw):
        return "家"
    return "未知"


def run(db_path: Path = DB_PATH, label: str | None = None) -> None:
    key = _amap_key()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, lat, lon, label, visit_count FROM places ORDER BY visit_count DESC"
    ).fetchall()
    print(f"[geocode] 待编码常驻点: {len(rows)} 个")

    for r in rows:
        # 已有明确标签（家/公司）且非本次强制覆盖，跳过
        if r["label"] in ("家", "公司") and label is None:
            continue
        info = reverse_geocode(r["lat"], r["lon"], key)
        new_label = label or (infer_label(info) if info else "未知")
        addr = info.get("formatted", "") if info else ""
        poi = info.get("poi", "") if info else ""
        print(f"  ({r['lat']:.4f},{r['lon']:.4f}) 访问{r['visit_count']}次 → "
              f"[{new_label}] {poi} | {addr}")
        conn.execute(
            "UPDATE places SET label=? WHERE id=?",
            (new_label, r["id"]),
        )
    conn.commit()
    conn.close()
    print("[geocode] 完成")


def main() -> None:
    parser = argparse.ArgumentParser(description="高德逆地理编码")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--label", type=str, default=None,
                        help="强制指定标签（如 家/公司），不传则自动推断")
    args = parser.parse_args()
    run(args.db, args.label)


if __name__ == "__main__":
    main()
