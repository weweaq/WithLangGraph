"""家/公司标签确认工具：把常驻点坐标映射持久化，ETL 重跑后标签不丢。

用法（首次确认）：
    python -m gacore.weitrack.label_places
    # 输出 top 主点 + 高德地址，按提示输入每个点的标签（家/公司/未知）

配置文件：data/place_labels.json（gitignore 之外的用户数据）
ETL 每次重跑后读取该配置恢复标签。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "weitrack.db"
CONFIG_PATH = Path(__file__).resolve().parents[3] / "data" / "place_labels.json"


def load_labels() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
    return {}


def save_labels(labels: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_labels(db_path: Path = DB_PATH) -> int:
    """ETL 后调用：按配置文件恢复标签。返回更新的点数。"""
    labels = load_labels()
    if not labels:
        return 0
    conn = sqlite3.connect(db_path)
    n = 0
    for grid_key, label in labels.items():
        cur = conn.execute(
            "UPDATE places SET label=? WHERE grid_key=?", (label, grid_key)
        )
        n += cur.rowcount
    conn.commit()
    conn.close()
    return n


def confirm() -> None:
    """交互式确认：打印主点地址，让用户输入标签。"""
    from gacore.weitrack.geocode import reverse_geocode, _amap_key

    key = _amap_key()
    labels = load_labels()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT grid_key, lat, lon, visit_count FROM places "
        "ORDER BY visit_count DESC LIMIT 6"
    ).fetchall()
    conn.close()

    print("=== 常驻点标签确认 ===")
    print("输入标签: 家 / 公司 / 未知（直接回车=未知）\n")
    for r in rows:
        info = reverse_geocode(r["lat"], r["lon"], key)
        addr = info.get("formatted", "") if info else ""
        poi = info.get("poi", "") if info else ""
        cur = labels.get(r["grid_key"], "未知")
        print(f"  ({r['lat']:.4f},{r['lon']:.4f}) 访问{r['visit_count']}次")
        print(f"    {poi} | {addr}")
        answer = input(f"    标签 [{cur}]: ").strip()
        if answer in ("家", "公司", "未知"):
            labels[r["grid_key"]] = answer
    save_labels(labels)
    n = apply_labels()
    print(f"\n已保存 {len(labels)} 个标签, 更新 {n} 个常驻点")


def main() -> None:
    confirm()


if __name__ == "__main__":
    main()
