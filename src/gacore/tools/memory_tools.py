"""LangChain tools for memory: short-term working checkpoint and long-term persistence.

Mirrors GA's do_update_working_checkpoint / do_start_long_term_update: global_mem.txt holds
L2 global facts, global_mem_insight.txt holds the L1 insight index. The tools are pure —
they return dicts; the GAStatefulToolNode extracts them into graph state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from langchain_core.tools import tool

from gacore.config import Config

_FACTS_FILE: Final = "global_mem.txt"
_INSIGHTS_FILE: Final = "global_mem_insight.txt"


@tool
def update_working_checkpoint(key_info: str, related_sop: str | None = None) -> dict:
    """Update the short-term working checkpoint with current task state.

    Returns the key_info for the state node to apply into state.working; this tool is
    pure and never mutates state itself.
    """
    return {
        "key_info": key_info,
        "related_sop": related_sop or "",
        "result": "working key_info updated",
    }


@tool
def start_long_term_update(topic: str, _cfg: Config | None = None) -> dict:
    """Distill a topic into long-term memory: append to the L2 fact store and the L1 insight index.

    _cfg is an injection seam excluded from the tool's args schema; production calls fall
    back to Config.default() and tests inject Config.for_tests(tmp_path).
    """
    cfg = _cfg or Config.default()
    facts_path = cfg.memory_dir / _FACTS_FILE
    insights_path = cfg.memory_dir / _INSIGHTS_FILE
    now = datetime.now(UTC).astimezone()
    timestamp = now.isoformat(timespec="seconds")
    day = now.date().isoformat()
    try:
        cfg.memory_dir.mkdir(parents=True, exist_ok=True)
        with facts_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{timestamp}] {topic}\n")
        with insights_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{day}] insight: {topic}\n")
    except OSError as e:
        return {"error": str(e)}
    return {"updated": "global_mem+insight", "topic": topic, "paths": [str(facts_path), str(insights_path)]}
