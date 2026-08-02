"""Tests for gacore.middleware: the GA turn logic as create_agent middleware.

Two layers are covered:
- Unit tests drive ``GATurnLogicMiddleware`` hooks directly with hand-built state dicts,
  proving the before_model short-circuit / max_turns guard and the after_model
  empty-retry / done_hooks / completion / agent-error branches.
- Integration tests compile a create_agent graph with the real middleware chain and a
  fake chat model, proving the jump_to control flow works end to end (the middleware
  hooks cannot redirect execution without the conditional edges create_agent adds).
"""

from __future__ import annotations

from typing import Any

from conftest import BindableFakeMessagesListChatModel
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from gacore.config import Config
from gacore.middleware import (
    GAPromptMiddleware,
    GATurnLogicMiddleware,
    format_agent_error,
)
from gacore.state import GAState, new_state

_EMPTY_PROMPT: str = "[Empty response. Please respond or call a tool.]"
_CALL: dict[str, object] = {
    "name": "file_write",
    "args": {"path": "notes.txt", "content": "hi"},
    "id": "call_1",
    "type": "tool_call",
}


def _state(**overrides: Any) -> dict:
    base: dict = {
        "messages": [HumanMessage(content="hello")],
        "working": {},
        "current_turn": 0,
        "max_turns": 40,
        "done_hooks": [],
        "retry_count": 0,
        "exit_reason": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- unit: before_model


def test_before_model_exit_reason_short_circuits(tmp_cfg: Config) -> None:
    """Given an exit_reason already set, before_model must jump to end without touching turn."""
    mw = GATurnLogicMiddleware()
    update = mw.before_model(_state(exit_reason="EXITED"), None)  # type: ignore[arg-type]

    assert update == {"jump_to": "end"}


def test_before_model_increments_turn(tmp_cfg: Config) -> None:
    """Given a live state, before_model must increment current_turn and stay on the model path."""
    mw = GATurnLogicMiddleware()
    update = mw.before_model(_state(current_turn=4), None)  # type: ignore[arg-type]

    assert update == {"current_turn": 5}


def test_before_model_max_turns_guard(tmp_cfg: Config) -> None:
    """Given current_turn at max_turns, before_model must exit with MAX_TURNS_EXCEEDED."""
    mw = GATurnLogicMiddleware()
    update = mw.before_model(_state(current_turn=40, max_turns=40), None)  # type: ignore[arg-type]

    assert update == {"jump_to": "end", "exit_reason": "MAX_TURNS_EXCEEDED"}


# ---------------------------------------------------------------- unit: after_model


def test_after_model_tool_calls_route_to_tools(tmp_cfg: Config) -> None:
    """Given a response with tool_calls, after_model must return None (default route: tools)."""
    mw = GATurnLogicMiddleware()
    update = mw.after_model(
        _state(messages=[HumanMessage(content="x"), AIMessage(content="", tool_calls=[_CALL])]),
        None,  # type: ignore[arg-type]
    )

    assert update is None


def test_after_model_empty_retries_with_corrective_prompt(tmp_cfg: Config) -> None:
    """Given an empty response within retry budget, after_model must loop back with a correction."""
    mw = GATurnLogicMiddleware()
    update = mw.after_model(
        _state(messages=[HumanMessage(content="x"), AIMessage(content="")], retry_count=1),
        None,  # type: ignore[arg-type]
    )

    assert update is not None
    assert update["jump_to"] == "model"
    assert [m.content for m in update["messages"]] == ["", _EMPTY_PROMPT]
    assert update["retry_count"] == 2


def test_after_model_empty_retry_exhausted_exits(tmp_cfg: Config) -> None:
    """Given an empty response past the retry budget, after_model must exit with EXITED."""
    mw = GATurnLogicMiddleware()
    update = mw.after_model(
        _state(messages=[HumanMessage(content="x"), AIMessage(content="")], retry_count=3),
        None,  # type: ignore[arg-type]
    )

    assert update == {"exit_reason": "EXITED", "retry_count": 0}


def test_after_model_fires_next_done_hook(tmp_cfg: Config) -> None:
    """Given pending done_hooks, after_model must fire the next one and loop back to the model."""
    mw = GATurnLogicMiddleware()
    update = mw.after_model(
        _state(
            messages=[HumanMessage(content="x"), AIMessage(content="partial")],
            done_hooks=["[hook1]", "[hook2]"],
        ),
        None,  # type: ignore[arg-type]
    )

    assert update is not None
    assert update["jump_to"] == "model"
    assert [m.content for m in update["messages"]] == ["partial", "[hook1]"]
    assert update["done_hooks"] == ["[hook2]"]
    assert update["retry_count"] == 0


def test_after_model_agent_error_message_exits(tmp_cfg: Config) -> None:
    """Given an injected agent-error message, after_model must exit with AGENT_ERROR."""
    mw = GATurnLogicMiddleware()
    update = mw.after_model(
        _state(messages=[HumanMessage(content="x"), AIMessage(content="[Agent error: boom]")]),
        None,  # type: ignore[arg-type]
    )

    assert update == {"exit_reason": "AGENT_ERROR", "retry_count": 0}


def test_after_model_completes_task(tmp_cfg: Config) -> None:
    """Given a real answer with no hooks pending, after_model must complete the task."""
    mw = GATurnLogicMiddleware()
    update = mw.after_model(
        _state(messages=[HumanMessage(content="x"), AIMessage(content="the answer")]),
        None,  # type: ignore[arg-type]
    )

    assert update == {"exit_reason": "CURRENT_TASK_DONE", "retry_count": 0}


# ------------------------------------------------------- integration: full graph


class _CountingFake(BindableFakeMessagesListChatModel):
    """A fake chat model that counts invokes and optionally raises.

    ``calls`` is declared as a pydantic field and ``_raise_on_invoke`` as a private
    attribute (underscore prefix), because pydantic v2 blocks undeclared attribute
    assignment on BaseModel subclasses.
    """

    calls: int = 0
    _raise_on_invoke: bool = False

    def __init__(self, responses: list[Any], *, raise_on_invoke: bool = False) -> None:
        super().__init__(responses=responses)
        self.calls = 0
        self._raise_on_invoke = raise_on_invoke

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self._raise_on_invoke:
            raise RuntimeError("provider boom")
        return super().invoke(*args, **kwargs)


def _graph(llm: Any, cfg: Config):
    return create_agent(
        llm,
        tools=[],
        state_schema=GAState,
        middleware=[
            GAPromptMiddleware(cfg),
            GATurnLogicMiddleware(),
            ModelRetryMiddleware(max_retries=0, retry_on=(Exception,), on_failure=format_agent_error),
        ],
        checkpointer=MemorySaver(),
        name="test-gacore",
    )


def test_graph_done_hooks_continue_then_complete(
    tmp_cfg: Config, message_llm: Any
) -> None:
    """End to end: empty turn -> done_hook turn -> final answer, exit CURRENT_TASK_DONE."""
    llm = message_llm(
        [
            AIMessage(content=""),
            AIMessage(content="partial answer"),
            AIMessage(content="final answer"),
        ]
    )
    graph = _graph(llm, tmp_cfg)
    state = new_state("hello", tmp_cfg)
    state["done_hooks"] = ["[hook1]"]

    final = graph.invoke(state, {"configurable": {"thread_id": "e2e-hooks"}})

    assert final["exit_reason"] == "CURRENT_TASK_DONE"
    contents = [m.content for m in final["messages"]]
    assert contents == ["hello", "", _EMPTY_PROMPT, "partial answer", "[hook1]", "final answer"]


def test_graph_exit_reason_short_circuits_without_model_call(
    tmp_cfg: Config, message_llm: Any
) -> None:
    """End to end: a state already carrying exit_reason never reaches the model."""
    llm = _CountingFake([AIMessage(content="unexpected")])
    graph = _graph(llm, tmp_cfg)
    state = new_state("hello", tmp_cfg)
    state["exit_reason"] = "EXITED"

    final = graph.invoke(state, {"configurable": {"thread_id": "e2e-short"}})

    assert final["exit_reason"] == "EXITED"
    assert llm.calls == 0


def test_graph_max_turns_guard_never_calls_model(tmp_cfg: Config, message_llm: Any) -> None:
    """End to end: current_turn at max_turns exits with MAX_TURNS_EXCEEDED, no model call."""
    llm = _CountingFake([AIMessage(content="unexpected")])
    graph = _graph(llm, tmp_cfg)
    state = new_state("hello", tmp_cfg)
    state["current_turn"] = 40
    state["max_turns"] = 40

    final = graph.invoke(state, {"configurable": {"thread_id": "e2e-maxturns"}})

    assert final["exit_reason"] == "MAX_TURNS_EXCEEDED"
    assert llm.calls == 0


def test_graph_tool_calls_route_to_tools_and_back(tmp_cfg: Config, message_llm: Any) -> None:
    """End to end: a tool call executes through the prebuilt ToolNode, then the agent answers."""
    from gacore.tools import build_tool_list

    llm = message_llm(
        [
            AIMessage(content="", tool_calls=[_CALL]),
            AIMessage(content="after tool"),
        ]
    )
    graph = create_agent(
        llm,
        tools=build_tool_list(tmp_cfg),
        state_schema=GAState,
        middleware=[
            GAPromptMiddleware(tmp_cfg),
            GATurnLogicMiddleware(),
            ModelRetryMiddleware(max_retries=0, retry_on=(Exception,), on_failure=format_agent_error),
        ],
        checkpointer=MemorySaver(),
        name="test-gacore-tools",
    )
    state = new_state("hello", tmp_cfg)

    final = graph.invoke(state, {"configurable": {"thread_id": "e2e-tools"}})

    assert final["exit_reason"] == "CURRENT_TASK_DONE"
    types_ = [type(m).__name__ for m in final["messages"]]
    assert types_ == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]


def test_graph_model_failure_injects_agent_error(tmp_cfg: Config, message_llm: Any) -> None:
    """End to end: a provider exception becomes an AGENT_ERROR exit, not a graph crash."""
    llm = _CountingFake([AIMessage(content="unexpected")], raise_on_invoke=True)
    graph = _graph(llm, tmp_cfg)
    state = new_state("hello", tmp_cfg)

    final = graph.invoke(state, {"configurable": {"thread_id": "e2e-error"}})

    assert final["exit_reason"] == "AGENT_ERROR"
    last = final["messages"][-1]
    assert isinstance(last, AIMessage)
    assert last.content.startswith("[Agent error: provider boom]")
