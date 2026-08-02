"""LangGraph graph wiring for gacore: the compiled GAState loop and its convenience runner.

Port of GA's agent_loop.py:42-107 on a 2-node ReAct-style topology. The agent node runs one
LLM turn and applies the final (no_tool) logic itself; tool calls route through langgraph's
standard prebuilt ToolNode and back, and control channels (exit_reason) terminate the loop.
ask_user interrupts pause the graph via the checkpointer and resume with a Command.

The module is deliberately a thin assembly layer: the agent node lives in gacore.nodes and
the tools are plain @tool/Command-returning functions registered in gacore.tools; this file
only decides the topology. build_graph() returns a compiled StateGraph over GAState;
run_once() is a one-shot convenience wrapper for a single user turn on a fresh thread.
"""

from __future__ import annotations

import uuid
from typing import Final

from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from gacore.config import Config
from gacore.llm import get_llm
from gacore.nodes.agent import make_agent_node, route_after_agent
from gacore.state import GAState, new_state
from gacore.tools import build_tool_list

DEFAULT_RECURSION_LIMIT: Final = 200
_AGENT_TARGETS: Final = {"tools": "tools", "agent": "agent", "END": END}


def suggested_recursion_limit(max_turns: int | None) -> int:
    """Return a recursion limit that leaves headroom for a full turn budget.

    Each tool round costs two graph steps (agent, tools) and a plain answer one (agent),
    so max_turns needs a 2x multiplier; the +50 margin absorbs empty-response retries and
    done_hooks loops. Falls back to the module default when max_turns is unknown.
    """
    if max_turns is None:
        return DEFAULT_RECURSION_LIMIT
    return max_turns * 2 + 50


def build_graph(
    llm: Runnable | None = None,
    cfg: Config | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Assemble and compile the full GA agent loop.

    Topology: START -> agent, then a single conditional edge routes tool_calls to the
    prebuilt ToolNode / plain answers back to agent / control channels to END; tools loop
    back to the agent on a static edge. A MemorySaver checkpointer is required for ask_user
    interrupts; pass a custom saver to share state across calls.

    Args:
        llm: The chat model bound to the tool list. When None, get_llm() resolves it
            from the environment (requires a configured provider/API key).
        cfg: Runtime configuration; defaults to Config.default().
        checkpointer: Persistence backend; defaults to a fresh MemorySaver.
    """
    resolved_cfg = cfg or Config.default()
    resolved_llm = llm or get_llm(build_tool_list(resolved_cfg))

    builder = StateGraph(GAState)
    builder.add_node("agent", make_agent_node(resolved_llm, resolved_cfg))
    # handle_tool_errors=True: langgraph's default error handler re-raises non-validation
    # tool exceptions, but GA parity requires every tool failure to become an error
    # ToolMessage so the loop never crashes (old GAStatefulToolNode behavior).
    builder.add_node("tools", ToolNode(build_tool_list(resolved_cfg), handle_tool_errors=True))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent, _AGENT_TARGETS)
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer or MemorySaver())


def run_once(
    graph: CompiledStateGraph,
    user_input: str,
    thread_id: str | None = None,
    recursion_limit: int | None = None,
    **extra_state: object,
) -> dict:
    """Run one user turn to completion on a fresh thread and return the final state.

    Builds the initial GAState from the user input plus any extra_state overrides
    (e.g. max_turns for tests), then invokes the compiled graph. Each call uses its own
    thread id so MemorySaver checkpoints never collide between turns.
    """
    state: dict[str, object] = {**new_state(user_input, Config.default()), **extra_state}
    config = {
        "configurable": {"thread_id": thread_id or uuid.uuid4().hex},
        "recursion_limit": recursion_limit or DEFAULT_RECURSION_LIMIT,
    }
    return graph.invoke(state, config)
