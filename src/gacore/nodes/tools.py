"""Stateful tool node and its routing: execute tool_calls and extract control state.

The tools node (make_tools_node) is the GAStatefulToolNode port: unlike langgraph's stock
ToolNode it writes non-message channels in the same update dict. Each tool_call is paired
to exactly one ToolMessage by its id; unknown tools and tool exceptions become error
ToolMessages. Post-processing extracts GA's StepOutcome control signals from result dicts:

- ask_user: should_exit=True sets exit_reason="EXITED"
- update_working_checkpoint: key_info (and related_sop) fold into state.working
- start_long_term_update / code_run / file_* / web_*: no state extraction
"""

from __future__ import annotations

import json
from collections.abc import Callable

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp

from gacore.config import Config
from gacore.state import GAState
from gacore.tools import build_tool_list


def make_tools_node(cfg: Config) -> Callable[[GAState], dict]:
    """Return a graph node that executes every tool_call in the last AIMessage.

    cfg is resolved from the closure on every invocation so tests can swap the tool
    registry by patching gacore.nodes.tools.build_tool_list.
    """

    def tools_node(state: GAState) -> dict:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        update: dict[str, object] = {}
        tool_messages: list[ToolMessage] = []
        if isinstance(last, AIMessage) and last.tool_calls:
            tools_by_name = {tool.name: tool for tool in build_tool_list(cfg)}
            for tc in last.tool_calls:
                call_id = tc["id"]
                tool = tools_by_name.get(tc["name"])
                if tool is None:
                    tool_messages.append(ToolMessage(content=f"Error: unknown tool {tc['name']}", tool_call_id=call_id))
                    continue
                try:
                    result = tool.invoke(tc["args"])
                except GraphBubbleUp:
                    raise  # interrupts (ask_user) must propagate, not become error ToolMessages
                except Exception as e:  # noqa: BLE001 - tool errors are arbitrary; must not crash the graph
                    tool_messages.append(ToolMessage(content=f"Error: {type(e).__name__}: {e}", tool_call_id=call_id))
                    continue
                tool_messages.append(ToolMessage(content=_content_for(result), tool_call_id=call_id))
                _extract_control(result, state, update)
        update["messages"] = tool_messages
        return update

    return tools_node


def _content_for(result: object) -> str:
    """Serialize a tool result into a ToolMessage content string."""
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, default=str)
    return str(result)


def _extract_control(result: object, state: GAState, update: dict[str, object]) -> None:
    """Fold GA StepOutcome signals out of a tool result into the state update."""
    if not isinstance(result, dict):
        return
    if result.get("should_exit") is True:
        update["exit_reason"] = "EXITED"
    if "key_info" in result:
        working = dict(state.get("working") or {})
        working["key_info"] = result["key_info"]
        if "related_sop" in result:
            working["related_sop"] = result["related_sop"]
        update["working"] = working


def route_after_tools(state: GAState) -> str:
    """After tools run, either end (a control channel was set) or loop back to the agent."""
    if state.get("exit_reason"):
        return "END"
    return "agent"
