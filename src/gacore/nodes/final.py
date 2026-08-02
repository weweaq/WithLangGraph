"""Final-answer validation node and its routing: GA's engine-injected no_tool logic.

final_validator runs after the agent produced an AIMessage without tool_calls and decides
whether the turn loop continues or the task is done. Cases, in priority order:

a. Truncated response: a finish/stop reason of "length"/"max_tokens" marks a provider
   cutoff (OpenAI reports finish_reason in response_metadata, Anthropic stop_reason in
   additional_kwargs; both keys are checked in both places) -> inject a continue prompt
   and retry.
b. Empty response: retry with a corrective prompt up to 3 times, then exit (GA's
   _retry_or_exit / 3-consecutive-blanks rule).
c. done_hooks pending: fire the first hook as a HumanMessage and continue the loop.
d. Normal completion: exit with CURRENT_TASK_DONE.

Ordering decision: truncated (a) is checked before done_hooks (c) so a cut-off answer
retries even when hooks are pending — hooks fire on genuine completion only, matching
GA's loop-end _done_hooks check. Retry-count decision: cases (c) and (d) return
retry_count=0 because a real (non-truncated, non-empty) response clears the blank streak;
retry paths (a)/(b) increment it instead.
"""

from __future__ import annotations

from typing import Final

from langchain_core.messages import AIMessage, HumanMessage

from gacore.state import GAState

_EMPTY_PROMPT: Final = "[Empty response. Please respond or call a tool.]"
_TRUNCATED_PROMPT: Final = "[Your response was truncated. Continue from where you stopped, in smaller steps.]"
_MAX_EMPTY_RETRIES: Final = 3
_TRUNCATION_REASONS: Final = frozenset(("length", "max_tokens"))


def final_validator(state: GAState) -> dict:
    """Validate the agent's final answer and return the next step's state update.

    Priority order: truncated -> empty-retry/exit -> done_hooks -> normal completion. A
    non-AIMessage (or missing) last message falls through to the done_hooks/normal cases.
    """

    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    retry_count = state.get("retry_count", 0)
    done_hooks = state.get("done_hooks") or []
    if isinstance(last, AIMessage) and _is_truncated(last):
        return {
            "messages": [HumanMessage(content=_TRUNCATED_PROMPT)],
            "retry_count": retry_count + 1,
        }
    if isinstance(last, AIMessage) and not last.content:
        if retry_count < _MAX_EMPTY_RETRIES:
            return {
                "messages": [HumanMessage(content=_EMPTY_PROMPT)],
                "retry_count": retry_count + 1,
            }
        return {"exit_reason": "EXITED"}
    if done_hooks:
        return {
            "messages": [HumanMessage(content=done_hooks[0])],
            "done_hooks": done_hooks[1:],
            "retry_count": 0,
        }
    return {"exit_reason": "CURRENT_TASK_DONE", "retry_count": 0}


def route_from_validator(state: GAState) -> str:
    """After validation, either end (a control channel fired) or loop back to the agent."""
    if state.get("exit_reason"):
        return "END"
    return "agent"


def _is_truncated(message: AIMessage) -> bool:
    """True when a provider marker says the response hit a length/max_tokens cutoff."""
    markers = (
        message.response_metadata.get("finish_reason"),
        message.response_metadata.get("stop_reason"),
        message.additional_kwargs.get("finish_reason"),
        message.additional_kwargs.get("stop_reason"),
    )
    return any(marker in _TRUNCATION_REASONS for marker in markers)
