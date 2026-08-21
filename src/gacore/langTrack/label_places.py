"""家/公司标签确认工具：把常驻点坐标映射持久化，ETL 重跑后标签不丢。

用法（首次确认）：
    python -m gacore.langTrack.label_places
    # 输出 top 主点 + 高德地址，按提示输入每个点的标签（家/公司/未知）

配置文件：data/place_labels.json（gitignore 之外的用户数据）
ETL 每次重跑后读取该配置恢复标签。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "langTrack.db"
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
    """交互式确认：打印主点地址 + 家/公司置信度候选，让用户输入标签。

    候选制：ETL 推断的 candidate_label 仅作建议展示，用户确认前 label 保持中性
    （未知/常驻点），确认后写入 place_labels.json 持久化。

    P1-1 确认闭环：优先展示【待确认候选点】（label=未知 且 candidate_label 非空），
    用户确认后正式定名；随后展示已确认点供复核修改。
    """
    from gacore.langTrack.geocode import reverse_geocode, _amap_key

    key = _amap_key()
    labels = load_labels()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 待确认候选点优先（P1-1 画像确认入口），再列已确认点供复核
    rows = conn.execute(
        "SELECT grid_key, lat, lon, visit_count, candidate_label, "
        "confidence_home, confidence_work, poi, address, matched_level, label "
        "FROM places "
        "WHERE label='未知' AND candidate_label IS NOT NULL "
        "ORDER BY visit_count DESC LIMIT 6"
    ).fetchall()
    confirmed_rows = conn.execute(
        "SELECT grid_key, lat, lon, visit_count, candidate_label, "
        "confidence_home, confidence_work, poi, address, matched_level, label "
        "FROM places WHERE label IN ('家','公司') "
        "ORDER BY visit_count DESC LIMIT 4"
    ).fetchall()
    conn.close()

    print("=== 常驻点标签确认 ===")
    print("输入标签: 家 / 公司 / 未知（直接回车=保持不变）")
    print("候选为 ETL 置信度推断，确认后正式写入画像\n")

    if not rows and not confirmed_rows:
        print("（无常驻点可确认）")
        return

    if rows:
        print("-- 待确认候选（ETL 推断，请确认是否定名） --")
        for r in rows:
            info = reverse_geocode(r["lat"], r["lon"], key)
            addr = info.get("formatted", "") if info else (r["address"] or "")
            poi = info.get("poi", "") if info else (r["poi"] or "")
            cur = labels.get(r["grid_key"], "未知")
            cand = r["candidate_label"] or "-"
            conf = f"(家 {r['confidence_home']:.2f} / 公司 {r['confidence_work']:.2f})"
            print(f"  ({r['lat']:.4f},{r['lon']:.4f}) 访问{r['visit_count']}次 候选:{cand} {conf}")
            print(f"    {poi} | {addr}")
            answer = input(f"    标签 [{cur}] (家/公司/未知/回车): ").strip()
            if answer in ("家", "公司", "未知"):
                labels[r["grid_key"]] = answer

    if confirmed_rows:
        print("\n-- 已确认点（可复核修改） --")
        for r in confirmed_rows:
            poi = r["poi"] or ""
            cur = labels.get(r["grid_key"], r["label"])
            conf = f"(家 {r['confidence_home']:.2f} / 公司 {r['confidence_work']:.2f})"
            print(f"  [{r['label']}] ({r['lat']:.4f},{r['lon']:.4f}) 访问{r['visit_count']}次 {conf} {poi}")
            answer = input(f"    标签 [{cur}] (家/公司/未知/回车): ").strip()
            if answer in ("家", "公司", "未知"):
                labels[r["grid_key"]] = answer

    save_labels(labels)
    n = apply_labels()
    print(f"\n已保存 {len(labels)} 个标签, 更新 {n} 个常驻点")


def main() -> None:
    confirm()


if __name__ == "__main__":
    main()
