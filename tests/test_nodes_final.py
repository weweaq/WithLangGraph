"""Tests for gacore.nodes.final: the final-answer validation node and its routing.

final_validator ports GA's engine-injected no_tool logic: empty-response retries (max 3),
truncation markers, done_hooks continuation, and normal completion. Both functions are
pure over GAState, so states are built directly (no compiled graph needed).
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from gacore.config import Config
from gacore.nodes.final import final_validator, route_from_validator
from gacore.state import GAState, new_state


def _state(
    cfg: Config,
    tail: list[BaseMessage],
    *,
    retry_count: int = 0,
    done_hooks: list[str] | None = None,
) -> GAState:
    """A fresh conversation state seeded with the user message plus the given tail."""
    state = new_state("user", cfg)
    state["messages"] = [*state["messages"], *tail]
    state["retry_count"] = retry_count
    state["done_hooks"] = done_hooks or []
    return state


def test_blank_response_retries_with_corrective_message(tmp_cfg: Config) -> None:
    """Given a blank AIMessage on the first try, When validated, Then a corrective prompt is appended and the count bumps."""
    state = _state(tmp_cfg, [AIMessage(content="")])

    result = final_validator(state)

    assert "exit_reason" not in result
    assert result["retry_count"] == 1
    (msg,) = result["messages"]
    assert isinstance(msg, HumanMessage)
    assert msg.content == "[Empty response. Please respond or call a tool.]"
    assert route_from_validator({**state, **result}) == "agent"


def test_blank_response_after_three_retries_exits(tmp_cfg: Config) -> None:
    """Given three prior blank retries, When validated again, Then the node exits with EXITED."""
    state = _state(tmp_cfg, [AIMessage(content="")], retry_count=3)

    result = final_validator(state)

    assert result["exit_reason"] == "EXITED"
    assert route_from_validator({**state, **result}) == "END"


def test_truncated_response_retries(tmp_cfg: Config) -> None:
    """Given an OpenAI-style finish_reason=length, When validated, Then the node retries with a continue prompt."""
    state = _state(
        tmp_cfg,
        [AIMessage(content="partial", response_metadata={"finish_reason": "length"})],
    )

    result = final_validator(state)

    assert "exit_reason" not in result
    assert result["retry_count"] == 1
    (msg,) = result["messages"]
    assert isinstance(msg, HumanMessage)
    assert msg.content == "[Your response was truncated. Continue from where you stopped, in smaller steps.]"


def test_truncated_anthropic_style_retries(tmp_cfg: Config) -> None:
    """Given an Anthropic-style stop_reason=max_tokens, When validated, Then the node retries."""
    state = _state(
        tmp_cfg,
        [AIMessage(content="", additional_kwargs={"stop_reason": "max_tokens"})],
    )

    result = final_validator(state)

    assert "exit_reason" not in result
    assert result["retry_count"] == 1


def test_done_hooks_fire_one_at_a_time(tmp_cfg: Config) -> None:
    """Given pending done_hooks behind a real answer, When validated, Then the first hook fires and the count resets."""
    state = _state(tmp_cfg, [AIMessage(content="done")], retry_count=5, done_hooks=["hook1", "hook2"])

    result = final_validator(state)

    assert "exit_reason" not in result
    assert result["retry_count"] == 0
    (msg,) = result["messages"]
    assert isinstance(msg, HumanMessage)
    assert msg.content == "hook1"
    assert result["done_hooks"] == ["hook2"]
    assert route_from_validator({**state, **result}) == "agent"


def test_normal_completion_exits_task_done(tmp_cfg: Config) -> None:
    """Given a real answer and no hooks, When validated, Then the task completes."""
    state = _state(tmp_cfg, [AIMessage(content="final answer")])

    result = final_validator(state)

    assert result["exit_reason"] == "CURRENT_TASK_DONE"
    assert route_from_validator({**state, **result}) == "END"


def test_successful_response_resets_retry_count(tmp_cfg: Config) -> None:
    """Given a real answer after prior blanks, When validated, Then the retry counter resets to 0."""
    state = _state(tmp_cfg, [AIMessage(content="here it is")], retry_count=5)

    result = final_validator(state)

    assert result["exit_reason"] == "CURRENT_TASK_DONE"
    assert result["retry_count"] == 0


def test_non_ai_message_treated_as_normal_completion(tmp_cfg: Config) -> None:
    """Given a history ending in a HumanMessage, When validated, Then the task completes defensively."""
    state = _state(tmp_cfg, [])

    result = final_validator(state)

    assert result["exit_reason"] == "CURRENT_TASK_DONE"


def test_empty_done_hooks_do_not_continue(tmp_cfg: Config) -> None:
    """Given an explicit empty hook list, When validated, Then the loop does not continue."""
    state = _state(tmp_cfg, [AIMessage(content="answer")], done_hooks=[])

    result = final_validator(state)

    assert result["exit_reason"] == "CURRENT_TASK_DONE"
    assert "done_hooks" not in result


def test_route_from_validator_exit_reason_ends() -> None:
    """Given an exit_reason in state, When routing, Then the graph ends."""
    assert route_from_validator({"exit_reason": "CURRENT_TASK_DONE"}) == "END"


def test_route_from_validator_loops_to_agent() -> None:
    """Given no exit_reason in state, When routing, Then the graph loops to the agent."""
    assert route_from_validator({}) == "agent"
