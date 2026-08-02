"""Tests for gacore.tools.memory_tools: working checkpoint and long-term memory persistence."""

from __future__ import annotations

import re
from pathlib import Path

from gacore.config import Config
from gacore.tools.memory_tools import start_long_term_update, update_working_checkpoint


def test_update_working_checkpoint_returns_exact_dict() -> None:
    """Given a key_info and a related SOP, When invoked, Then the exact dict is returned."""
    result = update_working_checkpoint.invoke({"key_info": "user onboarding plan", "related_sop": "onboarding_sop"})
    assert result == {
        "key_info": "user onboarding plan",
        "related_sop": "onboarding_sop",
        "result": "working key_info updated",
    }


def test_update_working_checkpoint_defaults_related_sop_to_empty_string() -> None:
    """Given only a key_info, When invoked, Then related_sop defaults to an empty string."""
    result = update_working_checkpoint.invoke({"key_info": "just a note"})
    assert result == {
        "key_info": "just a note",
        "related_sop": "",
        "result": "working key_info updated",
    }


def test_start_long_term_update_writes_l1_insight_and_l2_fact_files(tmp_path: Path) -> None:
    """Given a tmp cfg, When a topic is distilled, Then both memory files contain a dated line."""
    cfg = Config.for_tests(tmp_path)
    start_long_term_update.func(topic="user prefers fast iteration", _cfg=cfg)
    facts = (cfg.memory_dir / "global_mem.txt").read_text(encoding="utf-8")
    insights = (cfg.memory_dir / "global_mem_insight.txt").read_text(encoding="utf-8")
    assert "user prefers fast iteration" in facts
    assert re.search(r"\[\d{4}-\d{2}-\d{2}", facts)
    assert "insight: user prefers fast iteration" in insights
    assert re.search(r"\[\d{4}-\d{2}-\d{2}", insights)


def test_start_long_term_update_creates_missing_memory_dir(tmp_path: Path) -> None:
    """Given a cfg whose memory_dir does not exist, When a topic is distilled, Then the dir and files exist."""
    cfg = Config.for_tests(tmp_path)
    start_long_term_update.func(topic="fact", _cfg=cfg)
    assert cfg.memory_dir.is_dir()
    assert (cfg.memory_dir / "global_mem.txt").is_file()
    assert (cfg.memory_dir / "global_mem_insight.txt").is_file()


def test_start_long_term_update_returns_updated_topic_and_paths(tmp_path: Path) -> None:
    """Given a tmp cfg, When a topic is distilled, Then the result reports updated status, topic, and paths."""
    cfg = Config.for_tests(tmp_path)
    result = start_long_term_update.func(topic="fact", _cfg=cfg)
    assert result["updated"] == "global_mem+insight"
    assert result["topic"] == "fact"
    assert result["paths"] == [
        str(cfg.memory_dir / "global_mem.txt"),
        str(cfg.memory_dir / "global_mem_insight.txt"),
    ]


def test_start_long_term_update_returns_error_dict_on_io_failure(tmp_path: Path) -> None:
    """Given a memory_dir blocked by an existing file, When a topic is distilled, Then an error dict is returned."""
    blocker = tmp_path / "memory"
    blocker.write_text("not a directory", encoding="utf-8")
    cfg = Config.for_tests(tmp_path)
    result = start_long_term_update.func(topic="boom", _cfg=cfg)
    assert set(result) == {"error"}
    assert result["error"]


def test_tool_args_schemas_expose_fields_but_exclude_underscore_cfg() -> None:
    """Given the @tool decorators, When the args schemas are inspected, Then public fields are present and _cfg is not."""
    wc_props = update_working_checkpoint.args_schema.model_json_schema()["properties"]
    assert "key_info" in wc_props
    assert "related_sop" in wc_props
    ltm_props = start_long_term_update.args_schema.model_json_schema()["properties"]
    assert "topic" in ltm_props
    assert "_cfg" not in ltm_props
