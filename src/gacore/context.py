"""Per-turn prompt assembly for gacore: system prompt, history folding, summaries, periodic hints.

Port of GA's turn_end_callback + _get_anchor_prompt + get_global_memory (ga.py:558-613). Every function
is pure: it reads state and never mutates it. The system prompt is rebuilt fresh each turn — it is never
stored in state.messages, so the leading SystemMessage is not duplicated by the add_messages reducer.
"""

from __future__ import annotations

import re
from typing import Final

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from gacore.config import Config
from gacore.state import GAState
from gacore.tools.daily_notes import load_recent_daily_summaries

_MAX_CONTENT_LEN: Final = 200
_FOLD_LIMIT: Final = 30
_TRUNCATION_MARKER: Final = "..."
EARLIER_HEADER: Final = "=== Earlier context ==="
DAILY_HEADER: Final = "=== Recent daily notes ==="

_MEMORY_HINT: Final = "[Memory refresh: reload L1 insights and L2 facts into working memory]"
_CHECKPOINT_HINT: Final = "[Checkpoint time: update working checkpoint]"
_FILE_HINT: Final = "[Write your current state to a file]"
_ASK_USER_HINT: Final = "[Long-running: consider asking the user for confirmation]"
_ANTI_LOOP_HINT: Final = "[Warning: long loop detected, wrap up soon]"

# Fallback L0 rules (probe-first) used when config/assets/sys_prompt.txt is missing.
_DEFAULT_BASE_PROMPT: Final = (
    "行动原则：探测优先——失败时先充分获取信息（日志/状态/上下文），关键信息存入工作记忆，再决定重试或换方案。"
    "不可逆操作先询问用户。完成发生在现实中：必须产出实际结果并按错误代价验证，既不过早收工也不无限研究。"
)

_SUMMARY_RE: Final = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)


def build_system_prompt(state: GAState, cfg: Config) -> str:
    """Build the per-turn system prompt: L0 rules, daily recap, working checkpoint, and periodic hints."""
    path = cfg.asset_dir / "sys_prompt.txt"
    if path.is_file():
        prompt = path.read_text(encoding="utf-8")
    else:
        prompt = _DEFAULT_BASE_PROMPT
    daily = load_recent_daily_summaries(cfg)
    if daily:
        prompt += f"\n{DAILY_HEADER}\n{daily}"
    key_info = (state.get("working") or {}).get("key_info")
    if key_info:
        prompt += f"\n[Working checkpoint]\n{key_info}"
    hints = periodic_hints(state.get("current_turn", 0), cfg)
    if hints:
        prompt += "\n[Periodic hints]\n" + "\n".join(f"- {hint}" for hint in hints)
    return prompt


def fold_history(messages: list[BaseMessage], max_lines: int = _FOLD_LIMIT) -> list[str]:
    """Fold message history to <= max_lines summary lines, keeping the most recent entries.

    HumanMessage -> "[USER] {content[:200]}", AIMessage -> "[Agent] {content[:200]}", long content is
    truncated with a marker, ToolMessage and non-string content are skipped.
    """
    folded: list[str] = []
    for msg in messages:
        if isinstance(msg, ToolMessage) or not isinstance(msg.content, str):
            continue
        if isinstance(msg, HumanMessage):
            prefix = "[USER] "
        elif isinstance(msg, AIMessage):
            prefix = "[Agent] "
        else:
            continue
        content = msg.content
        if len(content) > _MAX_CONTENT_LEN:
            content = content[:_MAX_CONTENT_LEN] + _TRUNCATION_MARKER
        folded.append(prefix + content)
    if len(folded) <= max_lines:
        return folded
    return folded[len(folded) - max_lines :]


def extract_summaries(messages: list[BaseMessage]) -> list[str]:
    """Return the inner text of every <summary>...</summary> block found in AIMessages (GA's protocol)."""
    summaries: list[str] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and isinstance(msg.content, str):
            summaries.extend(match.strip() for match in _SUMMARY_RE.findall(msg.content))
    return summaries


def periodic_hints(turn: int, cfg: Config) -> list[str]:
    """Return the memory/checkpoint hints scheduled at this turn boundary; empty when none fire.

    Memory refresh every 10 turns (L1 insight + L2 facts reload), checkpoint at 13, file write at 31,
    ask-user at 175, and an anti-loop warning every turn after 100.
    """
    hints: list[str] = []
    if turn % 10 == 0:
        hints.append(_MEMORY_HINT)
    if turn % 13 == 0:
        hints.append(_CHECKPOINT_HINT)
    if turn % 31 == 0:
        hints.append(_FILE_HINT)
    if turn % 175 == 0:
        hints.append(_ASK_USER_HINT)
    if turn > 100:
        hints.append(_ANTI_LOOP_HINT)
    return hints


def build_turn_prompt(state: GAState, cfg: Config) -> list[BaseMessage]:
    """Assemble the per-turn prompt: a fresh SystemMessage, then the existing messages untouched.

    The folded earlier context is embedded in the system prompt string (never duplicated as a separate
    user message, and never appended to state).
    """
    messages = state.get("messages") or []
    prompt = build_system_prompt(state, cfg)
    folded = fold_history(messages)
    if folded:
        prompt += f"\n\n{EARLIER_HEADER}\n" + "\n".join(folded)
    return [SystemMessage(content=prompt), *messages]
