"""Tests for gacore.cli.run_repl: the interactive REPL over the compiled graph.

The REPL is driven by an injected input_func so tests never touch real stdin or the
network. Every test builds a fresh graph over a fake LLM (bindable adapter over the
conftest fakes) and asserts only the observable outcome: the returned exit_reason and
the prompts the REPL handed to input_func.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
    GenericFakeChatModel,
)
from langchain_core.messages import AIMessage, BaseMessage

from gacore.cli import run_repl
from gacore.config import Config


class _BindableFake:
    """Adapter so a fake chat model survives the agent node's bind-then-invoke path.

    Fake chat models inherit BaseChatModel.bind_tools, which raises NotImplementedError;
    the agent node needs the provider-style contract, so this wrapper restores it.
    """

    def __init__(self, inner: BaseChatModel) -> None:
        self._inner = inner

    def bind_tools(self, tools: object) -> _BindableFake:
        return self

    def invoke(self, prompt: object) -> AIMessage:
        return self._inner.invoke(prompt)


class _FakeInput:
    """A queue-based input_func: yields queued lines, records prompts, EOF when empty."""

    def __init__(self, *lines: str) -> None:
        self._lines = list(lines)
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


def _tool_call(name: str, args: dict[str, object], call_id: str) -> AIMessage:
    """Build an AIMessage carrying a single tool_call to a registered tool."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def test_repl_plain_answer_returns_current_task_done(
    tmp_cfg: Config,
    scripted_llm: Callable[[list[str]], GenericFakeChatModel],
) -> None:
    """Given a text-only fake LLM, When the user sends one plain message, Then run_repl returns CURRENT_TASK_DONE."""
    llm = _BindableFake(scripted_llm(["hello world"]))
    fake_input = _FakeInput("hello world")

    reason = run_repl(cfg=tmp_cfg, llm=llm, input_func=fake_input)

    assert reason == "CURRENT_TASK_DONE"


def test_quit_breaks_immediately(
    tmp_cfg: Config,
    scripted_llm: Callable[[list[str]], GenericFakeChatModel],
) -> None:
    """Given a /quit first input, When run_repl starts, Then it returns None without running the graph."""
    llm = _BindableFake(scripted_llm(["unused"]))
    fake_input = _FakeInput("/quit")

    reason = run_repl(cfg=tmp_cfg, llm=llm, input_func=fake_input)

    assert reason is None


def test_interrupt_prompts_human_and_resumes(
    tmp_cfg: Config,
    message_llm: Callable[[list[BaseMessage]], FakeMessagesListChatModel],
) -> None:
    """Given an ask_user interrupt then a final answer, When the human answers "go on", Then the graph completes with CURRENT_TASK_DONE and the question reached input_func."""
    responses = [
        _tool_call("ask_user", {"question": "continue?", "options": ["yes", "no"]}, "call_1"),
        AIMessage(content="final answer"),
    ]
    llm = _BindableFake(message_llm(responses))
    fake_input = _FakeInput("proceed", "go on")

    reason = run_repl(cfg=tmp_cfg, llm=llm, input_func=fake_input)

    assert reason == "CURRENT_TASK_DONE"
    assert any("continue?" in call for call in fake_input.calls)


def test_abort_answer_exits_repl(
    tmp_cfg: Config,
    message_llm: Callable[[list[BaseMessage]], FakeMessagesListChatModel],
) -> None:
    """Given an ask_user interrupt, When the human answers an abort word, Then run_repl stops and returns EXITED."""
    responses = [_tool_call("ask_user", {"question": "keep going?"}, "call_1")]
    llm = _BindableFake(message_llm(responses))
    fake_input = _FakeInput("proceed", "abort")

    reason = run_repl(cfg=tmp_cfg, llm=llm, input_func=fake_input)

    assert reason == "EXITED"
