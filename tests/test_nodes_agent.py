"""Tests for gacore.nodes.agent: the LLM-call agent node and its routing.

The agent node is exercised with the fake chat models from conftest (message_llm) plus
hand-rolled Runnable stand-ins for the max-turns guard and the AGENT_ERROR path.
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

    Used to prove the max-turns guard short-circuits before any LLM work happens.
    """

    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools: list[object]) -> _CountingLLM:
        return self

    def invoke(self, prompt: object) -> AIMessage:
        self.calls += 1
        raise AssertionError("LLM must not be invoked when the max-turns guard fires")


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


def test_agent_node_max_turns_guard_returns_without_llm_call(tmp_cfg: Config) -> None:
    """Given a state already at max_turns, When the node runs, Then it exits without invoking the LLM."""
    llm = _CountingLLM()
    node = make_agent_node(llm, tmp_cfg)
    state: GAState = {"messages": [], "current_turn": 40, "max_turns": 40}

    result = node(state)

    assert result == {"exit_reason": "MAX_TURNS_EXCEEDED"}
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


def test_route_after_agent_exit_reason_ends() -> None:
    """Given an exit_reason in state, When routing, Then the graph ends."""
    assert route_after_agent({"exit_reason": "EXITED"}) == "END"


def test_route_after_agent_tool_calls_routes_to_tools() -> None:
    """Given a last AIMessage with tool_calls, When routing, Then the graph runs tools."""
    state: GAState = {"messages": [AIMessage(content="", tool_calls=[_CALL])]}
    assert route_after_agent(state) == "tools"


def test_route_after_agent_plain_content_routes_to_final() -> None:
    """Given a last AIMessage without tool_calls, When routing, Then the graph finalizes."""
    state: GAState = {"messages": [AIMessage(content="done")]}
    assert route_after_agent(state) == "final"


def test_route_after_agent_no_messages_routes_to_final() -> None:
    """Given an empty message history, When routing, Then the graph finalizes defensively."""
    assert route_after_agent({"messages": []}) == "final"
