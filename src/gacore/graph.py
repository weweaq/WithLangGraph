"""LangGraph agent wiring for gacore: the official create_agent loop and its runner.

Replaces the hand-written 2-node StateGraph with ``langchain.agents.create_agent``: the
official prebuilt agent topology (model node + prebuilt ToolNode + tool routing) with
GA's turn logic carried by middleware (gacore.middleware) and the custom GAState schema.
ask_user interrupts pause the graph via the checkpointer and resume with a Command, as
before.

The module is deliberately a thin assembly layer: middleware lives in gacore.middleware,
the tools are plain @tool/Command-returning functions registered in gacore.tools; this
file only decides the middleware chain and compiles. build_graph() returns a compiled
create_agent graph over GAState; run_once() is a one-shot convenience wrapper for a
single user turn on a fresh thread.
"""

from __future__ import annotations

import uuid
from typing import Final

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from gacore.config import Config
from gacore.llm import get_llm
from gacore.middleware import GAPromptMiddleware, GATurnLogicMiddleware, format_agent_error
from gacore.state import GAState, new_state
from gacore.tools import build_tool_list

DEFAULT_RECURSION_LIMIT: Final = 200


def suggested_recursion_limit(max_turns: int | None) -> int:
    """Return a recursion limit that leaves headroom for a full turn budget.

    Each tool round costs two graph steps (model, tools) and a plain answer one (model),
    so max_turns needs a 2x multiplier; the +50 margin absorbs empty-response retries and
    done_hooks loops. Falls back to the module default when max_turns is unknown.
    """
    if max_turns is None:
        return DEFAULT_RECURSION_LIMIT
    return max_turns * 2 + 50


def build_graph(
    llm: BaseChatModel | None = None,
    cfg: Config | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Assemble and compile the full GA agent loop via the official create_agent.

    Middleware chain (first = outermost):
      - GAPromptMiddleware rebuilds the per-turn system prompt in wrap_model_call.
      - GATurnLogicMiddleware applies the GA turn control (short-circuit, max_turns
        guard, empty retry, done_hooks, completion) via before/after_model hooks.
      - ModelRetryMiddleware (official) converts provider failures into GA agent-error
        messages instead of crashing the graph; max_retries=0 keeps GA's fail-fast
        behavior while still formatting the error cleanly.

    A MemorySaver checkpointer is required for ask_user interrupts; pass a custom saver
    to share state across calls.

    Args:
        llm: The chat model (unbound; create_agent binds the tool list itself). When
            None, get_llm() resolves it from the environment (requires a configured
            provider/API key).
        cfg: Runtime configuration; defaults to Config.default().
        checkpointer: Persistence backend; defaults to a fresh MemorySaver.
    """
    resolved_cfg = cfg or Config.default()
    tool_list = build_tool_list(resolved_cfg)
    resolved_llm = llm or get_llm(tool_list, bind_tools=False)
    return create_agent(
        resolved_llm,
        tools=tool_list,
        state_schema=GAState,
        middleware=[
            GAPromptMiddleware(resolved_cfg),
            GATurnLogicMiddleware(),
            ModelRetryMiddleware(
                max_retries=0,
                retry_on=(Exception,),
                on_failure=format_agent_error,
            ),
        ],
        checkpointer=checkpointer or MemorySaver(),
        name="gacore",
    )


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
