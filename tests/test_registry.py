"""Tests for the gacore.tools registry: the canonical 9-tool list and its contract.

The registry is the seam between the LLM and the graph: TOOL_NAMES must agree with both
config/assets/tools_schema.json (minus the deliberately-removed social tools) and with
what build_tool_list() returns, so binding the model and executing tools never disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

from gacore.config import Config
from gacore.tools import TOOL_NAMES, build_tool_list

_SCHEMA_PATH: Path = Path(__file__).resolve().parents[1] / "config" / "assets" / "tools_schema.json"
_SOCIAL_TOOLS: frozenset[str] = frozenset({"send_group_message", "send_social_greeting"})


def _tools_by_name(tmp_cfg: Config) -> dict[str, object]:
    return {tool.name: tool for tool in build_tool_list(tmp_cfg)}


def test_build_tool_list_returns_exactly_the_nine_registered_names(tmp_cfg: Config) -> None:
    given = build_tool_list(tmp_cfg)

    assert len(given) == 9
    assert sorted(tool.name for tool in given) == sorted(TOOL_NAMES)


def test_every_tool_exposes_a_valid_args_schema(tmp_cfg: Config) -> None:
    for tool in build_tool_list(tmp_cfg):
        schema = tool.args_schema.model_json_schema()
        assert "properties" in schema


def test_schema_asset_non_social_tools_are_all_registered(tmp_cfg: Config) -> None:
    schema_tools = [
        entry["function"]["name"]
        for entry in json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    ]

    assert set(TOOL_NAMES) == set(schema_tools) - _SOCIAL_TOOLS
    for name in schema_tools:
        if name not in _SOCIAL_TOOLS:
            assert name in TOOL_NAMES
    assert "send_group_message" not in TOOL_NAMES
    assert "send_social_greeting" not in TOOL_NAMES


def test_every_tool_name_attribute_matches_registry_names(tmp_cfg: Config) -> None:
    given = _tools_by_name(tmp_cfg)

    assert set(given) == set(TOOL_NAMES)
    for name in TOOL_NAMES:
        assert given[name].name == name


def test_smoke_file_read_missing_file_returns_error_dict(tmp_cfg: Config) -> None:
    given = _tools_by_name(tmp_cfg)

    result = given["file_read"].invoke({"path": str(tmp_cfg.root / "nope.txt")})

    assert isinstance(result, dict)
    assert result["error"] == "not_found"


def test_smoke_web_execute_js_returns_stub_error_dict(tmp_cfg: Config) -> None:
    given = _tools_by_name(tmp_cfg)

    result = given["web_execute_js"].invoke({"script": "document.title"})

    assert result == {
        "error": "web_execute_js is not supported in this reimplementation (TMWebDriver removed)"
    }
