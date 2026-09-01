"""langTrack ETL 配置加载：阈值 / 回看窗口 / 默认设备等外置到 data/etl_config.json。

默认配置集中在 DEFAULTS；若 data/etl_config.json 存在且可解析，则按层级合并用户配置，
任一节点解析失败都回退到默认（不会因坏配置崩 ETL）。
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[3] / "data" / "etl_config.json"

DEFAULTS: dict = {
    "location": {
        # §3.1 坐标质量过滤：首版只观测（filter=False），dashboard 实测分布后由用户确认开启
        "max_accuracy_m": 150.0,
        "accept_missing_accuracy": True,
        "apply_accuracy_filter": False,
        # §2.5 geocode 失效阈值：新旧中心偏移超过该值时清空派生字段待重编
        "regeo_shift_m": 50.0,
    },
    "stays": {
        "large_radius_m": 120.0,
        "small_radius_m": 60.0,
        "min_duration_ms": 600000,
        "merge_gap_ms": 300000,
        "merge_radius_m": 150.0,
        "max_jump_m": 500.0,
        "max_speed_mps": 40.0,
    },
    "trips": {
        "min_duration_ms": 60000,
        "min_dist_m": 300.0,
        # 相邻 stay 间隙超过该值不推断为 trip（§3.1）
        "max_infer_gap_ms": 7200000,
    },
    "incremental": {
        "lookback_days": 2,
    },
    "anomaly": {
        "night_start_h": 23,
        "night_end_h": 5,
        "new_place_lookback_days": 7,
    },
    "device": {
        # 默认设备覆盖（留空则取 events 中最近活跃设备）；dashboard / report / 工具层共用
        "default_device": None,
    },
}


def _deep_update(base: dict, override: dict) -> dict:
    """层级合并 override 到 base（仅 dict 节点递归，其他直接覆盖）。"""
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def load_etl_config() -> dict:
    """读取 ETL 配置；文件缺失 / 损坏 → 返回 DEFAULTS 深拷贝。"""
    cfg = json.loads(json.dumps(DEFAULTS))  # 深拷贝，避免修改默认单例
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            _deep_update(cfg, user)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            cfg = json.loads(json.dumps(DEFAULTS))
    return cfg
