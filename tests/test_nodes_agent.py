"""Tests for gacore.nodes.agent: the LLM-call agent node and its routing.

The agent node is exercised with the fake chat models from conftest (message_llm) plus
hand-rolled Runnable stand-ins for the max-turns guard and the AGENT_ERROR path. Since
the no_tool final logic (empty retry, done_hooks, completion) now lives inside the agent
node, those cases are tested here directly against node(state) without a compiled graph.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from gacore.config import Config
from gacore.nodes.agent import make_agent_node, route_after_agent
from gacore.state import GAState

_CALL: dict[str, object] = {
    "name": "file_write",
    "args": {"path": "notes.txt", "content": "hi"},
    "id": "call_1",
    "type": "tool_call",
}

_EMPTY_PROMPT: str = "[Empty response. Please respond or call a tool.]"


class _BindableFake:
    """Adapter over a fake chat model so bind_tools behaves like a real provider.

    Fake chat models inherit BaseChatModel.bind_tools, which raises NotImplementedError;
    real providers (ChatOpenAI/ChatAnthropic) override it. This adapter restores that
    contract so the node's bind-then-invoke path runs against a fake model.
    """

    def __init__(self, inner: FakeMessagesListChatModel) -> None:
        self._inner = inner

    def bind_tools(self, tools: list[object]) -> _BindableFake:
        return self

    def invoke(self, prompt: object) -> AIMessage:
        return self._inner.invoke(prompt)


class _CountingLLM:
    """A Runnable stand-in that records invocations and raises when called.

    Used to prove the max-turns guard and the exit_reason short-circuit never call the LLM.
    """

    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools: list[object]) -> _CountingLLM:
        return self

    def invoke(self, prompt: object) -> AIMessage:
        self.calls += 1
        raise AssertionError("LLM must not be invoked when the guard fires")


class _RaisingLLM:
    """A Runnable stand-in that always raises, driving the AGENT_ERROR path."""

    def bind_tools(self, tools: list[object]) -> _RaisingLLM:
        return self

    def invoke(self, prompt: object) -> AIMessage:
        raise RuntimeError("simulated provider outage")


def test_agent_node_returns_ai_message_and_increments_turn(
    message_llm: Callable[[list[BaseMessage]], FakeMessagesListChatModel], tmp_cfg: Config
) -> None:
    """Given a model answering a plain message, When the node runs, Then it returns that message and turn 1."""
    llm = _BindableFake(message_llm([AIMessage(content="hi")]))
    node = make_agent_node(llm, tmp_cfg)
    state: GAState = {"messages": [HumanMessage(content="hello")], "current_turn": 0, "max_turns": 40}

    result = node(state)

    assert result["current_turn"] == 1
    (msg,) = result["messages"]
    assert isinstance(msg, AIMessage)
    assert msg.content == "hi"


def test_agent_node_preserves_tool_calls(
    message_llm: Callable[[list[BaseMessage]], FakeMessagesListChatModel], tmp_cfg: Config
) -> None:
    """Given a model answering with a tool_call, When the node runs, Then the AIMessage keeps its tool_calls."""
    llm = _BindableFake(message_llm([AIMessage(content="", tool_calls=[_CALL])]))
    node = make_agent_node(llm, tmp_cfg)
    state: GAState = {"messages": [HumanMessage(content="write a file")], "current_turn": 0, "max_turns": 40}

    result = node(state)

    (msg,) = result["messages"]
    assert isinstance(msg, AIMessage)
    assert msg.tool_calls == [_CALL]
    assert result["current_turn"] == 1
    assert "exit_reason" not in result


def test_agent_node_max_turns_guard_returns_without_llm_call(tmp_cfg: Config) -> None:
    """Given a state already at max_turns, When the node runs, Then it exits without invoking the LLM."""
    llm = _CountingLLM()
    node = make_agent_node(llm, tmp_cfg)
    state: GAState = {"messages": [], "current_turn": 40, "max_turns": 40}

    result = node(state)

    assert result == {"exit_reason": "MAX_TURNS_EXCEEDED"}
    assert llm.calls == 0


def test_agent_node_short_circuits_when_exit_reason_already_set(tmp_cfg: Config) -> None:
    """Given a state with exit_reason (ask_user abort), When the node runs, Then it returns {} without calling the LLM."""
    llm = _CountingLLM()
    node = make_agent_node(llm, tmp_cfg)
    state: GAState = {"messages": [], "current_turn": 1, "max_turns": 40, "exit_reason": "EXITED"}

    result = node(state)

    assert result == {}
    assert llm.calls == 0


def test_agent_node_catches_llm_error_and_sets_exit_reason(tmp_cfg: Config) -> None:
    """Given an LLM that raises, When the node runs, Then it exits cleanly with AGENT_ERROR."""
    node = make_agent_node(_RaisingLLM(), tmp_cfg)
    state: GAState = {"messages": [], "current_turn": 0, "max_turns": 40}

    result = node(state)

    assert result["exit_reason"] == "AGENT_ERROR"
    (msg,) = result["messages"]
    assert isinstance(msg, AIMessage)
    assert msg.content.startswith("[Agent error:")


def test_agent_node_empty_response_retries_with_corrective_message(
    message_llm: Callable[[list[BaseMessage]], FakeMessagesListChatModel], tmp_cfg: Config
) -> None:
    """Given a blank AIMessage on the first try, When the node runs, Then a corrective prompt is appended and the count bumps."""
    llm = _BindableFake(message_llm([AIMessage(content="")]))
    node = make_agent_node(llm, tmp_cfg)
    state: GAState = {"messages": [HumanMessage(content="hi")], "current_turn": 0, "max_turns": 40}

    result = node(state)

    assert "exit_reason" not in result
    assert result["retry_count"] == 1
    messages = result["messages"]
    assert isinstance(messages[0], AIMessage)
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == _EMPTY_PROMPT


def test_agent_node_empty_response_after_three_retries_exits(
    message_llm: Callable[[list[BaseMessage]], FakeMessagesListChatModel], tmp_cfg: Config
) -> None:
    """Given three prior blank retries, When the node runs again, Then it exits with EXITED."""
    llm = _BindableFake(message_llm([AIMessage(content="")]))
    node = make_agent_node(llm, tmp_cfg)
    state: GAState = {"messages": [HumanMessage(content="hi")], "current_turn": 0, "max_turns": 40, "retry_count": 3}

    result = node(state)

    assert result["exit_reason"] == "EXITED"


def test_agent_node_done_hooks_fire_one_at_a_time(
    message_llm: Callable[[list[BaseMessage]], FakeMessagesListChatModel], tmp_cfg: Config
) -> None:
    """Given pending done_hooks behind a real answer, When the node runs, Then the first hook fires and the count resets."""
    llm = _BindableFake(message_llm([AIMessage(content="done")]))
    node = make_agent_node(llm, tmp_cfg)
    state: GAState = {
        "messages": [HumanMessage(content="hi")],
        "current_turn": 0,
        "max_turns": 40,
        "retry_count": 5,
        "done_hooks": ["hook1", "hook2"],
    }

    result = node(state)

    assert "exit_reason" not in result
    assert result["retry_count"] == 0
    messages = result["messages"]
    assert isinstance(messages[0], AIMessage)
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "hook1"
    assert result["done_hooks"] == ["hook2"]


def test_agent_node_normal_completion_exits_task_done(
    message_llm: Callable[[list[BaseMessage]], FakeMessagesListChatModel], tmp_cfg: Config
) -> None:
    """Given a real answer and no hooks, When the node runs, Then the task completes."""
    llm = _BindableFake(message_llm([AIMessage(content="final answer")]))
    node = make_agent_node(llm, tmp_cfg)
    state: GAState = {"messages": [HumanMessage(content="hi")], "current_turn": 0, "max_turns": 40}

    result = node(state)

    assert result["exit_reason"] == "CURRENT_TASK_DONE"
    assert result["retry_count"] == 0


def test_route_after_agent_exit_reason_ends() -> None:
    """Given an exit_reason in state, When routing, Then the graph ends."""
    assert route_after_agent({"exit_reason": "EXITED"}) == "END"


def test_route_after_agent_tool_calls_routes_to_tools() -> None:
    """Given a last AIMessage with tool_calls, When routing, Then the graph runs tools."""
    state: GAState = {"messages": [AIMessage(content="", tool_calls=[_CALL])]}
    assert route_after_agent(state) == "tools"


def test_route_after_agent_plain_content_routes_to_agent() -> None:
    """Given a last AIMessage without tool_calls, When routing, Then the graph re-runs the agent."""
    state: GAState = {"messages": [AIMessage(content="done")]}
    assert route_after_agent(state) == "agent"


def test_route_after_agent_no_messages_routes_to_agent() -> None:
    """Given an empty message history, When routing, Then the graph re-runs the agent defensively."""
    assert route_after_agent({"messages": []}) == "agent"
