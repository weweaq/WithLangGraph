"""Interactive REPL for gacore: a thin human-in-the-loop frontend over the compiled graph.

Port of GA's frontends/tui_v3.py. run_repl is the testable core: it drives one
conversation turn at a time on a single thread (so MemorySaver keeps conversation
history across turns), detects ask_user interrupts either as a returned
``__interrupt__`` key or a raised GraphInterrupt, prompts the human, resumes with a
``Command(resume=...)``, and reports the final ``exit_reason``. Input is injected via
``input_func`` so tests never touch real stdin or the network.

Run with ``python -m gacore`` (with PYTHONPATH=src or after installing the package).
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from typing import Final

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langgraph.errors import GraphInterrupt
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from prompt_toolkit import PromptSession

from gacore.config import Config, load_dotenv
from gacore.graph import DEFAULT_RECURSION_LIMIT, build_graph
from gacore.logging import get_logger
from gacore.state import new_state

__version__: Final = "0.1.0"
_QUIT_WORD: Final = "/quit"

logger = get_logger("cli")

# Created lazily so importing gacore.cli works in non-console environments (tests, CI).
_session: PromptSession | None = None


def _default_input(prompt: str) -> str:
    """Read one line via prompt_toolkit (Ctrl-D raises EOFError, Ctrl-C KeyboardInterrupt)."""
    global _session
    if _session is None:
        _session = PromptSession()
    return _session.prompt(prompt)


def _interrupt_payload(result: dict) -> dict | None:
    """Extract the first interrupt's value dict, or None when the result is not an interrupt.

    Works on both langgraph surfaces: a result dict carrying ``__interrupt__`` and any
    dict-shaped payload. ask_user interrupts carry ``{"question": ..., "options": ...}``.
    """
    interrupts = result.get("__interrupt__")
    if not isinstance(interrupts, (list, tuple)) or not interrupts:
        return None
    value = getattr(interrupts[0], "value", None)
    return value if isinstance(value, dict) else None


def _invoke(graph: CompiledStateGraph, state: object, config: dict) -> dict:
    """Invoke the graph, normalizing a raised GraphInterrupt into the dict form."""
    try:
        return graph.invoke(state, config)
    except GraphInterrupt as exc:
        raw = exc.args[0] if exc.args else ()
        interrupts = tuple(raw) if isinstance(raw, (tuple, list)) else (raw,)
        return {"__interrupt__": interrupts}


def _last_ai_reply(result: dict) -> str | None:
    """Return the newest AIMessage content without tool_calls, or None."""
    messages = result.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls and message.content:
            return str(message.content)
    return None


def _print_result(result: dict) -> None:
    """Print the final assistant reply, or the exit_reason when there is none."""
    reply = _last_ai_reply(result)
    if reply is not None:
        print(reply)
        return
    reason = result.get("exit_reason")
    if reason:
        print(f"[{reason}]")


def _run_turn(
    graph: CompiledStateGraph,
    cfg: Config,
    user_input: str,
    config: dict,
    input_func: Callable[[str], str],
) -> str | None:
    """Run one user turn to completion, resolving any interrupts with the human."""
    result = _invoke(graph, new_state(user_input, cfg), config)
    while True:
        payload = _interrupt_payload(result)
        if payload is None:
            break
        question = str(payload.get("question") or "?")
        options = payload.get("options")
        if isinstance(options, list) and options:
            print(f"[ask_user] {question} (options: {', '.join(str(o) for o in options)})")
        else:
            print(f"[ask_user] {question}")
        try:
            answer = input_func(f"Your answer ({question}): ")
        except EOFError:
            answer = "abort"
        result = _invoke(graph, Command(resume=answer), config)
    _print_result(result)
    reason = result.get("exit_reason")
    return reason if isinstance(reason, str) else None


def run_repl(
    cfg: Config | None = None,
    llm: Runnable | None = None,
    input_func: Callable[[str], str] | None = None,
) -> str | None:
    """Run the interactive REPL over the compiled graph and return the final exit_reason.

    Args:
        cfg: Runtime configuration; defaults to Config.default().
        llm: Chat model bound to the tool list. When None, build_graph resolves it from
            the environment (get_llm may raise when no provider/API key is configured —
            tests pass a fake).
        input_func: Line reader taking a prompt and returning the user's input; defaults
            to a prompt_toolkit PromptSession. Raise EOFError to simulate Ctrl-D.

    Returns:
        The final ``exit_reason`` ("CURRENT_TASK_DONE", "EXITED", ...) or None when the
        user quit with /quit before any turn completed.
    """
    resolved_cfg = cfg or Config.default()
    graph = build_graph(llm=llm, cfg=resolved_cfg)
    input_source = input_func or _default_input
    # One thread per REPL session: MemorySaver keeps conversation history across turns.
    thread_id = uuid.uuid4().hex
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": DEFAULT_RECURSION_LIMIT,
    }
    print(f"gacore v{__version__} - interactive agent")
    print("Type /quit to exit")
    last_reason: str | None = None
    try:
        while True:
            try:
                line = input_source("> ")
            except EOFError:
                break
            except KeyboardInterrupt:
                print("\nInterrupted. Goodbye.")
                break
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower() == _QUIT_WORD:
                break
            last_reason = _run_turn(graph, resolved_cfg, stripped, config, input_source)
            if last_reason == "EXITED":
                break
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye.")
    return last_reason


def main() -> None:
    """CLI entry point: load .env, build config + graph, run the REPL, exit 0/1."""
    load_dotenv()
    try:
        run_repl(cfg=Config.default())
    except Exception as e:  # noqa: BLE001 - CLI boundary: report any failure and exit 1
        logger.error(
            "REPL failed to start",
            error_type=type(e).__name__,
            stack_trace=str(e),
            context={},
        )
        print(f"gacore: {type(e).__name__}: {e}")
        sys.exit(1)


__all__ = ("main", "run_repl")
