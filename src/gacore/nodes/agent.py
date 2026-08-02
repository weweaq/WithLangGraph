"""Agent node and its routing: the LLM call at the heart of the turn loop.

The agent node (make_agent_node) performs one LLM turn and then applies what GA's
engine called the "no_tool" final logic in the same step: empty responses retry with a
corrective prompt (max 3), pending done_hooks fire one at a time, and a real answer
completes the task. Truncation detection was deliberately removed (refactor decision).
When state already carries an exit_reason (ask_user abort, max_turns) the node
short-circuits without calling the LLM, so resuming an interrupted turn never re-invokes
the model.

Error-handling decision: when llm.invoke raises, the exception is caught, logged through
gacore.logging, and surfaced as a clean graph exit (exit_reason="AGENT_ERROR") with an
AIMessage explaining the failure. GA would have retried the call; for the port a clean,
testable error exit is preferred over a hard graph crash, matching the state machine's
other terminal reasons.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable

from gacore.config import Config
from gacore.context import build_turn_prompt
from gacore.logging import get_logger
from gacore.state import GAState
from gacore.tools import build_tool_list

_FALLBACK_MAX_TURNS: Final = 40
_EMPTY_PROMPT: Final = "[Empty response. Please respond or call a tool.]"
_MAX_EMPTY_RETRIES: Final = 3
logger = get_logger("nodes.agent")


def make_agent_node(llm: Runnable, cfg: Config) -> Callable[[GAState], dict]:
    """Return a graph node that performs one LLM turn against the state.

    The node reads current_turn/max_turns from state, builds the turn prompt, invokes the
    tool-bound LLM, and returns the AIMessage plus the incremented turn. When the turn
    exceeds max_turns the LLM is never called and the node exits immediately. A state that
    already carries an exit_reason short-circuits with an empty update (no LLM call).
    """

    def agent_node(state: GAState) -> dict:
        if state.get("exit_reason"):
            return {}  # short-circuit: no LLM re-call after abort / max_turns
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
        if response.tool_calls:
            return {"messages": [response], "current_turn": turn}
        # Final (no_tool) logic, formerly gacore.nodes.final: truncation detection removed.
        done_hooks = state.get("done_hooks") or []
        if not response.content:
            if state.get("retry_count", 0) < _MAX_EMPTY_RETRIES:
                return {
                    "messages": [response, HumanMessage(content=_EMPTY_PROMPT)],
                    "retry_count": state.get("retry_count", 0) + 1,
                    "current_turn": turn,
                }
            return {
                "messages": [response],
                "exit_reason": "EXITED",
                "current_turn": turn,
            }
        if done_hooks:
            return {
                "messages": [response, HumanMessage(content=done_hooks[0])],
                "done_hooks": done_hooks[1:],
                "retry_count": 0,
                "current_turn": turn,
            }
        return {
            "messages": [response],
            "exit_reason": "CURRENT_TASK_DONE",
            "retry_count": 0,
            "current_turn": turn,
        }

    return agent_node


def route_after_agent(state: GAState) -> str:
    """Route the agent's response: end on control channels, else to tools or a re-run.

    A plain (no-tool) answer never routes to a separate finalizer — the agent node already
    applied the final logic and either set exit_reason (END) or appended a corrective /
    done_hook HumanMessage (loop back to agent for another turn).
    """
    if state.get("exit_reason"):
        return "END"
    messages = state.get("messages") or []
    if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
        return "tools"
    return "agent"
