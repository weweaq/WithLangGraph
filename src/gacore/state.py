"""LangGraph state schema and initializer for gacore."""

from __future__ import annotations

from typing import Annotated, Final, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from gacore.config import Config

EXIT_REASONS: Final = ("CURRENT_TASK_DONE", "EXITED", "MAX_TURNS_EXCEEDED")
DEFAULT_MAX_TURNS: Final = 40


class GAState(TypedDict, total=False):
    """LangGraph state schema: full message history plus overwrite-only working channels.

    `messages` carries the complete conversation (the add_messages reducer appends). Every
    other channel has no reducer, so LangGraph's default overwrite semantics apply: the
    latest update replaces the previous value.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    working: dict
    current_turn: int
    max_turns: int
    done_hooks: list[str]
    retry_count: int
    exit_reason: str | None


def new_state(user_input: str, cfg: Config) -> GAState:
    """Seed a fresh GAState for a new conversation with the user's first message."""
    return GAState(
        messages=[HumanMessage(content=user_input)],
        working={},
        current_turn=0,
        max_turns=cfg.max_turns,
        done_hooks=[],
        retry_count=0,
        exit_reason=None,
    )
