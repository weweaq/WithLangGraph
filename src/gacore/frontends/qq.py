"""QQ channel frontend for gacore: connect a QQ Official Bot to the create_agent loop.

Replicates the capability of GA's ``frontends/qqapp.py``: listens for QQ private
(C2C) messages and group @-mentions, runs them through the gacore agent graph, and
sends the response back as markdown. Each QQ user gets their own thread_id so
conversation history is preserved across turns.

Run with::

    python -m gacore.frontends.qq

Environment variables (see .env.example)::

    QQ_APP_ID / QQ_APP_SECRET          QQ Open Platform credentials
    QQ_ALLOWED_USERS = * or openid,...  allowlist (``*`` = public)
    QQ_LOG_FILE = logs/qq.log           optional log redirect

ask_user interrupts: when the agent graph pauses on an interrupt, the question is
sent to the user and the pending graph config is stored keyed by user_id. The next
message from that user resumes the graph with ``Command(resume=answer)`` — the agent
continues the same turn without restarting.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from typing import Final

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from gacore.config import Config, load_dotenv
from gacore.graph import DEFAULT_RECURSION_LIMIT, build_graph
from gacore.jsonl_logger import get_logger
from gacore.state import new_state

load_dotenv()

logger = get_logger("qq")

try:
    import botpy
    from botpy.message import C2CMessage, GroupMessage
except Exception:  # noqa: BLE001
    print("Please install qq-botpy to use QQ frontend: pip install qq-botpy")
    sys.exit(1)

# --------------------------------------------------------------------------- config

_APP_ID: str = str(os.environ.get("QQ_APP_ID", "")).strip()
_APP_SECRET: str = str(os.environ.get("QQ_APP_SECRET", "")).strip()
_ALLOWED: frozenset[str] = frozenset(
    str(x).strip() for x in os.environ.get("QQ_ALLOWED_USERS", "*").split(",") if str(x).strip()
)
_LOG_FILE: str = os.environ.get("QQ_LOG_FILE", "").strip()

_SPLIT_LIMIT: Final = 4500  # QQ markdown message length safety margin
_RECONNECT_INITIAL: Final = 5
_RECONNECT_MAX: Final = 300

# --------------------------------------------------------------------------- state

_processed_ids: deque[str] = deque(maxlen=2000)          # dedupe by message id
_pending_interrupt: dict[str, dict] = {}                 # user_id -> graph config awaiting resume
_user_threads: dict[str, str] = {}                       # user_id -> thread_id
_graph: CompiledStateGraph | None = None

_msg_seq_counter = 0
_msg_seq_lock = threading.Lock()


def _next_seq() -> int:
    global _msg_seq_counter
    with _msg_seq_lock:
        _msg_seq_counter += 1
        return _msg_seq_counter


def _is_public() -> bool:
    return "*" in _ALLOWED


def _split_text(text: str) -> list[str]:
    """Split a long reply into QQ-safe chunks (newline-aware)."""
    text = (text or "").strip() or "..."
    if len(text) <= _SPLIT_LIMIT:
        return [text]
    parts: list[str] = []
    while len(text) > _SPLIT_LIMIT:
        cut = text.rfind("\n", 0, _SPLIT_LIMIT)
        if cut < _SPLIT_LIMIT * 0.5:
            cut = _SPLIT_LIMIT
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts or ["..."]


# --------------------------------------------------------------------------- bot


def _build_intents() -> botpy.Intents:
    """Subscribe to the events GA's qqapp subscribes to (best-effort by version)."""
    try:
        return botpy.Intents(public_messages=True, direct_message=True)
    except Exception:  # noqa: BLE001
        intents = botpy.Intents.none() if hasattr(botpy.Intents, "none") else botpy.Intents()
        for attr in (
            "public_messages",
            "public_guild_messages",
            "direct_message",
            "direct_messages",
            "c2c_message",
            "c2c_messages",
            "group_at_message",
            "group_at_messages",
        ):
            if hasattr(intents, attr):
                try:
                    setattr(intents, attr, True)
                except (AttributeError, TypeError):
                    pass
        return intents


def _make_bot_class(app: QQApp):
    class QQBot(botpy.Client):
        def __init__(self) -> None:
            super().__init__(intents=_build_intents(), ext_handlers=False)

        async def on_ready(self) -> None:
            name = getattr(getattr(self, "robot", None), "name", "QQBot")
            logger.info(f"QQ bot ready: {name}")
            print(f"[QQ] bot ready: {name}")

        async def on_c2c_message_create(self, message: C2CMessage) -> None:
            await app.on_message(message, is_group=False)

        async def on_group_at_message_create(self, message: GroupMessage) -> None:
            await app.on_message(message, is_group=True)

        async def on_direct_message_create(self, message) -> None:
            await app.on_message(message, is_group=False)

    return QQBot


class QQApp:
    """Routes QQ messages into the gacore agent loop and sends back replies."""

    label: str = "QQ"
    source: str = "qq"

    def __init__(self, graph: CompiledStateGraph) -> None:
        self.graph = graph
        self.client: botpy.Client | None = None

    # --------------------------------------------------------------- sending

    async def _send_markdown(self, chat_id: str, content: str, *, is_group: bool, msg_id: str | None) -> None:
        """Send text as markdown, falling back to plain text on error."""
        if not self.client:
            return
        api = self.client.api.post_group_message if is_group else self.client.api.post_c2c_message
        key = "group_openid" if is_group else "openid"
        for part in _split_text(content):
            seq = _next_seq()
            try:
                await api(**{key: chat_id, "msg_type": 2, "markdown": {"content": part}, "msg_id": msg_id, "msg_seq": seq})
            except Exception:  # noqa: BLE001 — markdown unsupported, fall back to plain text
                await api(**{key: chat_id, "msg_type": 0, "content": part, "msg_id": msg_id, "msg_seq": seq})

    async def send_text(self, chat_id: str, content: str, *, msg_id: str | None = None, is_group: bool = False) -> None:
        await self._send_markdown(chat_id, content, is_group=is_group, msg_id=msg_id)

    # --------------------------------------------------------------- messages

    async def on_message(self, data, is_group: bool = False) -> None:
        """Main message handler: dedupe -> auth -> route to command / resume / agent."""
        try:
            msg_id = getattr(data, "id", None)
            if not msg_id or msg_id in _processed_ids:
                return
            _processed_ids.append(msg_id)

            content = (getattr(data, "content", "") or "").strip()
            if not content:
                return

            author = getattr(data, "author", None)
            user_id = str(
                getattr(author, "member_openid" if is_group else "user_openid", "")
                or getattr(author, "id", "")
                or "unknown"
            )
            chat_id = str(getattr(data, "group_openid", "") or user_id) if is_group else user_id

            if not _is_public() and user_id not in _ALLOWED:
                logger.warning(f"unauthorized QQ user: {user_id}")
                return

            logger.info(f"QQ message from {user_id} ({'group' if is_group else 'c2c'}): {content[:80]}")
            print(f"[QQ] {user_id} ({'group' if is_group else 'c2c'}): {content[:80]}")

            # 1) If this user has a pending ask_user interrupt, resume the graph.
            if user_id in _pending_interrupt:
                config = _pending_interrupt.pop(user_id)
                asyncio.create_task(self._resume_agent(chat_id, content, config, msg_id=msg_id, is_group=is_group))
                return

            # 2) Slash commands.
            if content.startswith("/"):
                await self._handle_command(chat_id, content, msg_id=msg_id, is_group=is_group)
                return

            # 3) Normal agent turn.
            asyncio.create_task(self._run_agent(chat_id, content, user_id, msg_id=msg_id, is_group=is_group))

        except Exception:  # noqa: BLE001 — top-level message handler crash guard
            logger.error("QQ on_message error", stack_trace=traceback.format_exc())
            print("[QQ] handle_message error")
            traceback.print_exc()

    # --------------------------------------------------------------- commands

    async def _handle_command(self, chat_id: str, cmd: str, *, msg_id: str | None, is_group: bool) -> None:
        op = (cmd or "").split()[0].lower()
        if op == "/help":
            return await self.send_text(
                chat_id,
                "📖 命令列表:\n"
                "/help — 显示帮助\n"
                "/new — 开启新对话(清空上下文)\n"
                "/status — 查看当前会话状态\n"
                "/stop — 停止当前任务",
                msg_id=msg_id,
                is_group=is_group,
            )
        if op == "/new":
            old_thread = _user_threads.pop(chat_id, None)
            if old_thread:
                try:
                    self.graph.delete_thread(old_thread)
                except (KeyError, ValueError, LookupError):
                    pass
            return await self.send_text(chat_id, "✅ 已开启新对话", msg_id=msg_id, is_group=is_group)
        if op == "/status":
            thread_id = _user_threads.get(chat_id)
            has_pending = "有" if chat_id in _pending_interrupt else "无"
            return await self.send_text(
                chat_id,
                f"🟢 会话状态:\nthread: {thread_id or '未创建'}\n待回复中断: {has_pending}",
                msg_id=msg_id,
                is_group=is_group,
            )
        if op == "/stop":
            # No running-task tracking in QQ frontend (stateless per turn); just inform.
            return await self.send_text(chat_id, "⏹️ 当前无运行中的任务", msg_id=msg_id, is_group=is_group)
        await self.send_text(chat_id, "未知命令,输入 /help 查看", msg_id=msg_id, is_group=is_group)

    # --------------------------------------------------------------- agent

    def _thread_for(self, user_id: str) -> str:
        """Return (or create) the per-user thread_id so history persists across turns."""
        if user_id not in _user_threads:
            _user_threads[user_id] = f"qq-{user_id}-{uuid.uuid4().hex[:8]}"
        return _user_threads[user_id]

    async def _run_agent(
        self, chat_id: str, text: str, user_id: str, *, msg_id: str | None, is_group: bool
    ) -> None:
        """Run one agent turn from scratch (new HumanMessage appended to the user's thread)."""
        thread_id = self._thread_for(user_id)
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": DEFAULT_RECURSION_LIMIT}
        state = new_state(text, Config.default())
        await self._stream_agent(chat_id, state, config, msg_id=msg_id, is_group=is_group, user_id=user_id)

    async def _resume_agent(
        self, chat_id: str, answer: str, config: dict, *, msg_id: str | None, is_group: bool
    ) -> None:
        """Resume a paused (ask_user) turn with the user's answer."""
        await self._stream_agent(
            chat_id, Command(resume=answer), config, msg_id=msg_id, is_group=is_group, user_id=None
        )

    async def _stream_agent(
        self,
        chat_id: str,
        input_data,
        config: dict,
        *,
        msg_id: str | None,
        is_group: bool,
        user_id: str | None,
    ) -> None:
        """Stream graph updates: render tool calls, tool results, final answer; handle interrupts."""
        reply_parts: list[str] = []
        try:
            await self.send_text(chat_id, "思考中...", msg_id=msg_id, is_group=is_group)
            async for chunk in self.graph.astream(input_data, config, stream_mode="updates"):
                # ask_user interrupt: store config, send question, wait for next message.
                if "__interrupt__" in chunk:
                    interrupts = chunk["__interrupt__"]
                    if interrupts:
                        value = getattr(interrupts[0], "value", None)
                        if isinstance(value, dict) and "question" in value:
                            if user_id is not None:
                                _pending_interrupt[user_id] = config
                            question = str(value.get("question") or "?")
                            options = value.get("options")
                            if isinstance(options, list) and options:
                                await self.send_text(
                                    chat_id, f"[ask_user] {question}\n(选项: {', '.join(str(o) for o in options)})",
                                    is_group=is_group,
                                )
                            else:
                                await self.send_text(chat_id, f"[ask_user] {question}", is_group=is_group)
                            return
                    continue

                for update in chunk.values():
                    if not isinstance(update, dict):
                        continue
                    for message in update.get("messages", []):
                        if isinstance(message, AIMessage):
                            if message.tool_calls:
                                for call in message.tool_calls:
                                    name = call.get("name", "?")
                                    if name == "ask_user":
                                        await self.send_text(chat_id, "[agent] -> ask_user", is_group=is_group)
                                    else:
                                        args = call.get("args") or {}
                                        await self.send_text(
                                            chat_id, f"[agent] -> {name}({', '.join(f'{k}={v}' for k, v in args.items())})",
                                            is_group=is_group,
                                        )
                            elif message.content:
                                reply_parts.append(str(message.content))
                        elif isinstance(message, ToolMessage):
                            content = str(message.content)
                            if len(content) > 200:
                                content = content[:197] + "..."
                            await self.send_text(chat_id, f"[tools] <- {content}", is_group=is_group)

            # Stream finished: send the final answer (if not already sent via interrupt).
            if reply_parts:
                final_text = "\n\n".join(p for p in reply_parts if p and not p.startswith("<summary>"))
                if final_text.strip():
                    await self.send_text(chat_id, final_text, is_group=is_group)

        except Exception:  # noqa: BLE001 — top-level stream crash guard
            logger.error("QQ agent stream error", stack_trace=traceback.format_exc())
            await self.send_text(chat_id, "❌ 运行出错,请稍后重试或输入 /new 重置", is_group=is_group)

    # --------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Start the QQ bot with auto-reconnect (exponential backoff)."""
        self.client = _make_bot_class(self)()
        delay = _RECONNECT_INITIAL
        while True:
            started_at = time.monotonic()
            try:
                print(f"[QQ] bot starting... {time.strftime('%m-%d %H:%M')}")
                await self.client.start(appid=_APP_ID, secret=_APP_SECRET)
            except Exception as e:  # noqa: BLE001 — top-level reconnect guard
                logger.error(f"QQ bot error: {e}")
                print(f"[QQ] bot error: {e}")
            if time.monotonic() - started_at >= 60:
                delay = _RECONNECT_INITIAL
            print(f"[QQ] reconnect in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)


# --------------------------------------------------------------------------- entry

def _ensure_single_instance(port: int = 19528) -> None:
    """Prevent two QQ frontends from running simultaneously."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", port))
    except OSError:
        print("[QQ] Another instance is already running, skipping...")
        sys.exit(1)


def _redirect_log() -> None:
    """Redirect stdout/stderr to a log file if QQ_LOG_FILE is set."""
    if not _LOG_FILE:
        return
    log_dir = os.path.dirname(_LOG_FILE) or "."
    os.makedirs(log_dir, exist_ok=True)
    logf = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 — intentional lifetime redirect
    sys.stdout = sys.stderr = logf
    print(f"[QQ] process starting at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[QQ] allow list: {'public' if _is_public() else sorted(_ALLOWED)}")


def main() -> None:
    """Entry point for ``python -m gacore.frontends.qq``."""
    if not _APP_ID or not _APP_SECRET:
        print("[QQ] ERROR: please set QQ_APP_ID and QQ_APP_SECRET in .env")
        sys.exit(1)

    _ensure_single_instance()
    _redirect_log()

    graph = build_config()
    logger.info("QQ frontend starting")
    asyncio.run(QQApp(graph).start())


def build_config() -> CompiledStateGraph:
    """Build the agent graph (lazy singleton so imports are cheap)."""
    global _graph
    if _graph is None:
        _graph = build_graph(cfg=Config.default())
    return _graph


if __name__ == "__main__":
    main()
