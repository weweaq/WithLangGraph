"""Tests for gacore.nodes.tools: the stateful tool-execution node and its routing.

The ask_user and update_working_checkpoint tools are replaced with stub doubles in the
tool list (via monkeypatching gacore.nodes.tools.build_tool_list) so the control
extraction logic is tested without a compiled graph or a checkpointer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from gacore.config import Config
from gacore.nodes.tools import make_tools_node, route_after_tools
from gacore.state import GAState
from gacore.tools import build_tool_list


def _tool_call(name: str, args: dict[str, object], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


class _StubTool:
    """Minimal tool double exposing name + invoke for control-extraction tests."""

    def __init__(self, name: str, result: object) -> None:
        self.name = name
        self._result = result

    def invoke(self, args: dict[str, object]) -> object:
        return self._result


def _stub_tool_list(cfg: Config, stub: _StubTool) -> list[object]:
    return [stub if tool.name == stub.name else tool for tool in build_tool_list(cfg)]


def test_tools_node_file_write_executes_and_pairs_message(tmp_cfg: Config, tmp_path: Path) -> None:
    """Given a file_write tool_call, When the node runs, Then the file is written and a paired ToolMessage returned."""
    target = tmp_path / "out.txt"
    state: GAState = {"messages": [_tool_call("file_write", {"path": str(target), "content": "hi"}, "call_1")]}
    node = make_tools_node(tmp_cfg)

    result = node(state)

    (msg,) = result["messages"]
    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call_1"
    assert '"status": "ok"' in msg.content
    assert target.read_text(encoding="utf-8") == "hi"


def test_tools_node_unknown_tool_returns_error_message(tmp_cfg: Config) -> None:
    """Given a tool_call for an unregistered name, When the node runs, Then it returns an unknown-tool error."""
    state: GAState = {"messages": [_tool_call("no_such_tool", {}, "call_1")]}

    result = make_tools_node(tmp_cfg)(state)

    (msg,) = result["messages"]
    assert isinstance(msg, ToolMessage)
    assert msg.content == "Error: unknown tool no_such_tool"
    assert msg.tool_call_id == "call_1"


def test_tools_node_tool_exception_becomes_error_message(tmp_cfg: Config, tmp_path: Path) -> None:
    """Given a tool_call whose tool raises, When the node runs, Then the error is surfaced in a ToolMessage."""
    blocked = tmp_path / "adir"
    blocked.mkdir()
    state: GAState = {"messages": [_tool_call("file_write", {"path": str(blocked), "content": "x"}, "call_1")]}

    result = make_tools_node(tmp_cfg)(state)

    (msg,) = result["messages"]
    assert isinstance(msg, ToolMessage)
    assert msg.content.startswith("Error:")
    assert msg.tool_call_id == "call_1"


def test_tools_node_ask_user_should_exit_sets_exited(monkeypatch: pytest.MonkeyPatch, tmp_cfg: Config) -> None:
    """Given an ask_user answer flagged should_exit, When the node runs, Then exit_reason becomes EXITED."""
    stub = _StubTool("ask_user", {"answer": "abort", "question": "continue?", "should_exit": True})
    monkeypatch.setattr("gacore.nodes.tools.build_tool_list", lambda cfg: _stub_tool_list(cfg, stub))
    state: GAState = {"messages": [_tool_call("ask_user", {"question": "continue?"}, "call_1")]}

    result = make_tools_node(tmp_cfg)(state)

    assert result["exit_reason"] == "EXITED"
    (msg,) = result["messages"]
    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call_1"


def test_tools_node_ask_user_without_exit_leaves_state_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_cfg: Config) -> None:
    """Given an ask_user answer without should_exit, When the node runs, Then no control channels are written."""
    stub = _StubTool("ask_user", {"answer": "continue", "question": "go?", "should_exit": False})
    monkeypatch.setattr("gacore.nodes.tools.build_tool_list", lambda cfg: _stub_tool_list(cfg, stub))
    state: GAState = {"messages": [_tool_call("ask_user", {"question": "go?"}, "call_1")]}

    result = make_tools_node(tmp_cfg)(state)

    assert "exit_reason" not in result
    assert "working" not in result


def test_tools_node_update_working_checkpoint_extracts_key_info(monkeypatch: pytest.MonkeyPatch, tmp_cfg: Config) -> None:
    """Given an update_working_checkpoint result, When the node runs, Then working gains key_info and related_sop."""
    stub = _StubTool(
        "update_working_checkpoint",
        {"key_info": "K", "related_sop": "S", "result": "working key_info updated"},
    )
    monkeypatch.setattr("gacore.nodes.tools.build_tool_list", lambda cfg: _stub_tool_list(cfg, stub))
    state: GAState = {"messages": [_tool_call("update_working_checkpoint", {"key_info": "K"}, "call_1")], "working": {}}

    result = make_tools_node(tmp_cfg)(state)

    assert result["working"] == {"key_info": "K", "related_sop": "S"}


def test_tools_node_ai_message_without_tool_calls_returns_empty_update(tmp_cfg: Config) -> None:
    """Given a last AIMessage with no tool_calls, When the node runs, Then it returns an empty messages update."""
    state: GAState = {"messages": [AIMessage(content="no tools here")]}

    result = make_tools_node(tmp_cfg)(state)

    assert result == {"messages": []}


def test_tools_node_pairs_multiple_tool_call_ids(tmp_cfg: Config, tmp_path: Path) -> None:
    """Given two tool_calls with distinct ids, When the node runs, Then each ToolMessage pairs to its own id."""
    target = tmp_path / "pair.txt"
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "file_write", "args": {"path": str(target), "content": "a"}, "id": "call_1", "type": "tool_call"},
            {"name": "no_such_tool", "args": {}, "id": "call_2", "type": "tool_call"},
        ],
    )

    result = make_tools_node(tmp_cfg)({"messages": [ai]})

    by_id = {m.tool_call_id: m for m in result["messages"]}
    assert set(by_id) == {"call_1", "call_2"}
    assert '"status": "ok"' in by_id["call_1"].content
    assert by_id["call_2"].content == "Error: unknown tool no_such_tool"


def test_tools_node_empty_tool_list_yields_unknown_tool_errors(monkeypatch: pytest.MonkeyPatch, tmp_cfg: Config) -> None:
    """Given tool_calls but an empty tool list, When the node runs, Then each call becomes an unknown-tool error."""
    monkeypatch.setattr("gacore.nodes.tools.build_tool_list", lambda cfg: [])
    state: GAState = {"messages": [_tool_call("file_write", {"path": "x", "content": "hi"}, "call_1")]}

    result = make_tools_node(tmp_cfg)(state)

    (msg,) = result["messages"]
    assert isinstance(msg, ToolMessage)
    assert msg.content == "Error: unknown tool file_write"
    assert msg.tool_call_id == "call_1"


def test_route_after_tools_exit_reason_ends() -> None:
    """Given an exit_reason in state, When routing after tools, Then the graph ends."""
    assert route_after_tools({"exit_reason": "EXITED"}) == "END"


def test_route_after_tools_without_exit_reason_loops_to_agent() -> None:
    """Given no exit_reason, When routing after tools, Then the graph loops back to the agent."""
    assert route_after_tools({"messages": []}) == "agent"
