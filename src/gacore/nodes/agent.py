"""Agent node and its routing: the LLM call at the heart of the turn loop.

The agent node (make_agent_node) performs one LLM turn: it enforces the max-turns guard
before any model call, assembles the turn prompt from gacore.context, binds the
registered tool list, and returns the AIMessage (which may carry tool_calls) plus the
incremented turn count.

Error-handling decision: when llm.invoke raises, the exception is caught, logged through
gacore.logging, and surfaced as a clean graph exit (exit_reason="AGENT_ERROR") with an
AIMessage explaining the failure. GA would have retried the call; for the port a clean,
testable error exit is preferred over a hard graph crash, matching the state machine's
other terminal reasons.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from gacore.config import Config
from gacore.context import build_turn_prompt
from gacore.logging import get_logger
from gacore.state import GAState
from gacore.tools import build_tool_list

_FALLBACK_MAX_TURNS: Final = 40
logger = get_logger("nodes.agent")


def make_agent_node(llm: Runnable, cfg: Config) -> Callable[[GAState], dict]:
    """Return a graph node that performs one LLM turn against the state.

    The node reads current_turn/max_turns from state, builds the turn prompt, invokes the
    tool-bound LLM, and returns the AIMessage plus the incremented turn. When the turn
    exceeds max_turns the LLM is never called and the node exits immediately.
    """

    def agent_node(state: GAState) -> dict:
        turn = state.get("current_turn", 0) + 1
        if turn > state.get("max_turns", _FALLBACK_MAX_TURNS):
            return {"exit_reason": "MAX_TURNS_EXCEEDED"}
        try:
            response = llm.bind_tools(build_tool_list(cfg)).invoke(build_turn_prompt(state, cfg))
        except Exception as e:  # noqa: BLE001 - provider errors are arbitrary; must exit cleanly, not crash
            logger.error(
                "agent node LLM invoke failed",
                error_type=type(e).__name__,
                stack_trace=str(e),
                context={"turn": turn},
            )
            return {"exit_reason": "AGENT_ERROR", "messages": [AIMessage(content=f"[Agent error: {e}]")]}
        return {"messages": [response], "current_turn": turn}

    return agent_node


def route_after_agent(state: GAState) -> str:
    """Route the agent's response: end on control channels, else to tools or the finalizer."""
    if state.get("exit_reason"):
        return "END"
    messages = state.get("messages") or []
    if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
        return "tools"
    return "final"
