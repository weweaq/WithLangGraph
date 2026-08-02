"""Human-in-the-loop tool: ask the user a question via a LangGraph interrupt.

Maps to GA's do_ask_user (INTERRUPT / HUMAN_INTERVENTION). The tool is pure: the first call
raises a resumable interrupt carrying the question, and once the graph resumes with the
human's answer the tool returns a dict whose should_exit flag the GAStatefulToolNode (T13)
consumes to stop the loop with an exit_reason.
"""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.types import interrupt

_ABORT_WORDS: frozenset[str] = frozenset({"abort", "exit", "quit", "stop", "cancel"})


@tool
def ask_user(question: str, options: list[str] | None = None) -> dict[str, object]:
    """Ask the user a question and wait for their answer.

    Pauses the graph with interrupt() so a human can respond; the resumed value becomes the
    returned answer. `should_exit` is True when the answer is an abort/exit word, letting the
    GAStatefulToolNode terminate the conversation loop.
    """
    answer = interrupt({"question": question, "options": options})
    if answer is None:
        # Never resumed (or resumed with a null value): treat as interrupted, force exit.
        return {"answer": None, "question": question, "should_exit": True, "interrupted": True}
    return {
        "answer": answer,
        "question": question,
        "options": options or [],
        "should_exit": bool(answer.strip().lower() in _ABORT_WORDS),
    }
