"""GA turn logic as official ``create_agent`` middleware: prompt injection and turn control.

This module replaces the hand-written 2-node StateGraph (gacore.nodes.agent) with the
official ``langchain.agents.create_agent`` middleware system. Two middleware classes
carry what GA's engine called the agent-node + no_tool-final logic:

- ``GAPromptMiddleware`` rebuilds the per-turn system prompt inside ``wrap_model_call``
  (the official channel for customizing the model request; ``ModelRequest.override`` is
  the non-deprecated way to replace the system message).
- ``GATurnLogicMiddleware`` implements the control logic that used to live in the agent
  node and its router: the exit_reason short-circuit and the max_turns guard run in
  ``before_model`` (jumping to END via the ``jump_to`` state channel); empty-response
  retries, done_hooks continuation and task completion run in ``after_model`` (jumping
  back to the model node when another LLM turn is needed).

Middleware control flow uses the official ``hook_config``/``can_jump_to`` mechanism:
a hook decorated with ``@hook_config(can_jump_to=[...])`` gets a conditional graph edge
that reads the ``jump_to`` channel, so returning ``{"jump_to": "end"}`` / ``{"jump_to":
"model"}`` redirects execution without any custom graph wiring.
"""

from __future__ import annotations

from typing import Any, Final

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    Runtime,
    hook_config,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from gacore.config import Config
from gacore.context import build_system_prompt
from gacore.state import GAState

_EMPTY_PROMPT: Final = "[Empty response. Please respond or call a tool.]"
_MAX_EMPTY_RETRIES: Final = 3
_AGENT_ERROR_PREFIX: Final = "[Agent error:"


class GAPromptMiddleware(AgentMiddleware[GAState, None, Any]):
    """Rebuild the per-turn system prompt (rules + working checkpoint + hints).

    GA never stores the system prompt in state.messages: it is rebuilt fresh on every
    model call. This middleware applies that behavior at the model-request level, which
    keeps the ``messages`` channel clean (no duplicated leading SystemMessage).

    Note: the full ``state.messages`` is passed to the LLM by create_agent; we do NOT
    fold history into the system prompt — that would cause every message to appear
    twice, triggering duplicate replies from the model.
    """

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg

    def wrap_model_call(
        self, request: ModelRequest[None], handler: Any
    ) -> ModelResponse[Any] | AIMessage:
        """Replace the request's system message with the GA per-turn prompt."""
        req = self._inject_prompt(request)
        return handler(req)

    async def awrap_model_call(
        self, request: ModelRequest[None], handler: Any
    ) -> ModelResponse[Any] | AIMessage:
        """Async twin of wrap_model_call — required when the graph runs via astream()."""
        req = self._inject_prompt(request)
        return await handler(req)

    def _inject_prompt(self, request: ModelRequest[None]) -> ModelRequest[None]:
        """Build the per-turn system message and return an overridden request.

        Note: fold_history is intentionally not called here. create_agent already
        passes the full state.messages to the LLM; folding them into the system
        prompt as well would cause every message to appear twice, triggering
        duplicate replies from the model.
        """
        state = request.state
        prompt = build_system_prompt(state, self.cfg)
        return request.override(system_message=SystemMessage(content=prompt))


class GATurnLogicMiddleware(AgentMiddleware[GAState, None, Any]):
    """Turn-loop control: short-circuits, max_turns guard, empty retry, done_hooks, completion.

    before_model (can jump to END):
      - state already carries exit_reason -> short-circuit without calling the model
        (resuming an interrupted turn never re-invokes the LLM).
      - current_turn exceeded max_turns -> exit with MAX_TURNS_EXCEEDED.

    after_model (can jump back to MODEL or END):
      - tool calls present -> None (the default route sends them to the tools node).
      - empty content -> retry with a corrective HumanMessage (max 3), else EXITED.
      - pending done_hooks -> fire the next one as a HumanMessage and loop back to the
        model for another turn.
      - an injected agent-error message -> exit with AGENT_ERROR.
      - a real answer -> complete the task with CURRENT_TASK_DONE.
    """

    @hook_config(can_jump_to=["end"])
    def before_model(
        self, state: GAState, runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        """Short-circuit on exit_reason / max_turns before the model is called."""
        if state.get("exit_reason"):
            return {"jump_to": "end"}
        turn = state.get("current_turn", 0) + 1
        if turn > state.get("max_turns", 40):
            return {"jump_to": "end", "exit_reason": "MAX_TURNS_EXCEEDED"}
        return {"current_turn": turn}

    @hook_config(can_jump_to=["model", "end"])
    def after_model(
        self, state: GAState, runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        """Apply the final (no_tool) logic after one model call: retry / hooks / done."""
        messages = state.get("messages") or []
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        response = messages[-1]
        if response.tool_calls:
            return None  # default route: tools node
        done_hooks = state.get("done_hooks") or []
        if response.content.startswith(_AGENT_ERROR_PREFIX):
            return {"exit_reason": "AGENT_ERROR", "retry_count": 0}
        if not response.content:
            if state.get("retry_count", 0) < _MAX_EMPTY_RETRIES:
                return {
                    "jump_to": "model",
                    "messages": [response, HumanMessage(content=_EMPTY_PROMPT)],
                    "retry_count": state.get("retry_count", 0) + 1,
                }
            return {"exit_reason": "EXITED", "retry_count": 0}
        if done_hooks:
            return {
                "jump_to": "model",
                "messages": [response, HumanMessage(content=done_hooks[0])],
                "done_hooks": done_hooks[1:],
                "retry_count": 0,
            }
        return {"exit_reason": "CURRENT_TASK_DONE", "retry_count": 0}


def format_agent_error(exc: Exception) -> str:
    """Format a provider failure as the GA agent-error message (for ModelRetryMiddleware)."""
    return f"[Agent error: {exc}]"
