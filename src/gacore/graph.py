"""LangGraph graph wiring for gacore: the compiled GAState loop and its convenience runner.

Port of GA's agent_loop.py:42-107: the agent node runs one LLM turn, tool calls route
through the tools node and back, no-tool answers go through the final validator, and
control channels (exit_reason) terminate the loop. ask_user interrupts pause the graph
via the checkpointer and resume with a Command.

The module is deliberately a thin assembly layer: every node and router lives in
gacore.nodes, and this file only decides the topology. build_graph() returns a compiled
StateGraph over GAState; run_once() is a one-shot convenience wrapper for a single user
turn on a fresh thread.
"""

from __future__ import annotations

import uuid
from typing import Final

from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from gacore.config import Config
from gacore.llm import get_llm
from gacore.nodes.agent import make_agent_node, route_after_agent
from gacore.nodes.final import final_validator, route_from_validator
from gacore.nodes.tools import make_tools_node, route_after_tools
from gacore.state import GAState, new_state
from gacore.tools import build_tool_list

DEFAULT_RECURSION_LIMIT: Final = 200
_AGENT_TARGETS: Final = {"tools": "tools", "final": "final", "END": END}
_TOOLS_TARGETS: Final = {"agent": "agent", "END": END}
_VALIDATOR_TARGETS: Final = {"agent": "agent", "END": END}


def suggested_recursion_limit(max_turns: int | None) -> int:
    """Return a recursion limit that leaves headroom for a full turn budget.

    Each turn costs roughly three graph steps (agent, tools, agent/final), so max_turns
    needs a 3x multiplier; the +50 margin absorbs validator retries and done_hooks loops.
    Falls back to the module default when max_turns is unknown.
    """
    if max_turns is None:
        return DEFAULT_RECURSION_LIMIT
    return max_turns * 3 + 50


def build_graph(
    llm: Runnable | None = None,
    cfg: Config | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Assemble and compile the full GA agent loop.

    The topology mirrors GA's agent_runner_loop: START -> agent, then the agent routes
    tool_calls to tools / plain answers to final / control channels to END, the tools
    node loops back to the agent unless a control channel fired, and the final validator
    either ends or returns to the agent for another turn. A MemorySaver checkpointer is
    required for ask_user interrupts; pass a custom saver to share state across calls.

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
    builder.add_node("tools", make_tools_node(resolved_cfg))
    builder.add_node("final", final_validator)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent, _AGENT_TARGETS)
    builder.add_conditional_edges("tools", route_after_tools, _TOOLS_TARGETS)
    builder.add_conditional_edges("final", route_from_validator, _VALIDATOR_TARGETS)
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
