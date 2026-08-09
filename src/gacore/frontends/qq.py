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
    QQ_ADMIN_USERS = * or openid,...    who may trigger /reboot (``*`` = any allowlisted user)
    QQ_LOG_FILE = logs/qq.log           optional log redirect

ask_user interrupts: when the agent graph pauses on an interrupt, the question is
sent to the user and the pending graph config is stored keyed by user_id. The next
message from that user resumes the graph with ``Command(resume=answer)`` — the agent
continues the same turn without restarting.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Final

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from gacore.config import Config, load_dotenv
from gacore.graph import DEFAULT_RECURSION_LIMIT, build_graph
from gacore.jsonl_logger import get_logger
from gacore.state import new_state
from gacore.tools.ocr_tools import ocr_image

load_dotenv()

logger = get_logger("qq")

try:
    import botpy
    from botpy.message import C2CMessage, GroupMessage
except Exception:  # noqa: BLE001
    logger.error("Please install qq-botpy to use QQ frontend: pip install qq-botpy")
    sys.exit(1)

# --------------------------------------------------------------------------- config

_APP_ID: str = str(os.environ.get("QQ_APP_ID", "")).strip()
_APP_SECRET: str = str(os.environ.get("QQ_APP_SECRET", "")).strip()
_ALLOWED: frozenset[str] = frozenset(
    str(x).strip() for x in os.environ.get("QQ_ALLOWED_USERS", "*").split(",") if str(x).strip()
)
# Who may trigger /reboot. Empty = nobody; "*" = anyone in the allowlist; else openid list.
_ADMIN_IDS: frozenset[str] = frozenset(
    str(x).strip() for x in os.environ.get("QQ_ADMIN_USERS", "").split(",") if str(x).strip()
)
_LOG_FILE: str = os.environ.get("QQ_LOG_FILE", "").strip()

_SPLIT_LIMIT: Final = 4500  # QQ markdown message length safety margin
_RECONNECT_INITIAL: Final = 5
_RECONNECT_MAX: Final = 300
_HTTP_TIMEOUT: Final = 30  # botpy default (5s) is too short for slow TLS handshakes
_IMAGE_WAIT_TIMEOUT: Final = 300  # seconds the wait_for_text interrupt auto-resumes after

# --------------------------------------------------------------------------- state

_processed_ids: deque[str] = deque(maxlen=2000)          # dedupe by message id
_pending_interrupt: dict[str, dict] = {}                 # user_id -> graph config awaiting resume
_user_threads: dict[str, str] = {}                       # user_id -> thread_id
_queued_inputs: dict[str, deque] = {}                    # user_id -> FIFO queue of pending inputs
_pending_image: dict[str, dict] = {}                     # user_id -> pending image batch info
_image_processing: set[str] = set()                      # user_ids currently being processed
_rendered_msg_ids: dict[str, set[str]] = {}              # user_id -> rendered message ids (stream dedupe)
_graph: CompiledStateGraph | None = None
_instance_sock: socket.socket | None = None              # single-instance port, released by /reboot

_msg_seq_counter = 0
_msg_seq_lock = threading.Lock()


def _next_seq() -> int:
    global _msg_seq_counter
    with _msg_seq_lock:
        _msg_seq_counter += 1
        return _msg_seq_counter


def _is_public() -> bool:
    return "*" in _ALLOWED


def _is_admin(user_id: str) -> bool:
    """Whether a user may trigger /reboot. ``*`` = any allowlisted user; empty list = nobody."""
    if not _ADMIN_IDS:
        return False
    if "*" in _ADMIN_IDS:
        return _is_public() or user_id in _ALLOWED
    return user_id in _ADMIN_IDS


# --------------------------------------------------------------------------- image helpers

_IMG_TAG_RE: Final = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
_SUMMARY_TAG_RE: Final = re.compile(r"<summary>.*?</summary>", re.DOTALL)


def _extract_image_urls(data) -> list[str]:
    """Extract image URLs from a QQ message: attachments first, then <img> tags in content."""
    urls: list[str] = []
    attachments = getattr(data, "attachments", None) or []
    for att in attachments:
        url = getattr(att, "url", None)
        if url and isinstance(url, str):
            urls.append(url)
    content = getattr(data, "content", "") or ""
    urls.extend(m.group(1) for m in _IMG_TAG_RE.finditer(content))
    return urls


def _write_bytes(dest: str, content: bytes) -> None:
    """Write downloaded bytes to dest; runs in a worker thread to avoid blocking the loop."""
    with open(dest, "wb") as f:
        f.write(content)


async def _download_image(url: str, dest: str) -> bool:
    """Download an image URL to dest. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        await asyncio.to_thread(_write_bytes, dest, resp.content)
        return True
    except Exception:  # noqa: BLE001 — silent degradation
        logger.warning(f"QQ image download failed: {url}")
        return False


def _build_image_prompt(
    paths: list[str], ocr_texts: dict[str, str], user_text: str, history_path: str | None = None
) -> str:
    """Build a structured prompt embedding OCR-extracted text so the AGENT can analyze it.

    The image content is handed to the model as recognized text (not as a tool call
    the model must make itself), so the agent always has the content to reason about.
    When history_path is given, every OCR result is persisted there as JSONL, and the
    prompt tells the agent it can look up past images via file_read.
    """
    n = len(paths)
    blocks: list[str] = []
    for i, path in enumerate(paths, start=1):
        text = ocr_texts.get(path, "")
        blocks.append(f"图片 {i}（{path}）：\n{text if text else '（OCR 未识别到文字）'}")
    body = "\n\n".join(blocks)
    prefix = (
        f"[用户发送了 {n} 张图片，以下是每张图片 OCR 识别出的文字内容。"
        f"请基于识别内容理解图片并分析，直接给出结论，不要复述原始识别文本。]\n{body}"
    )
    if history_path:
        prefix += (
            f"\n\n[历史图片 OCR 记录保存在 {history_path}（JSONL，每行一条，含时间戳与图片路径）。"
            f"当用户询问之前发送过的图片内容时，用 file_read 读取该文件查询，不要猜测。]"
        )
    if user_text:
        return f"{prefix}\n用户原文：{user_text}"
    return prefix


def _persist_ocr_history(memory_dir: Path, paths: list[str], ocr_texts: dict[str, str]) -> str | None:
    """Append this batch of OCR results to memory/ocr_history.jsonl; returns the file path.

    Each line is a JSON object: {"ts": iso, "path": image path, "text": recognized text}.
    Returns None when the write fails (degraded: analysis proceeds without history).
    """
    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
        history_path = memory_dir / "ocr_history.jsonl"
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with history_path.open("a", encoding="utf-8") as fh:
            for path in paths:
                fh.write(
                    json.dumps(
                        {"ts": now, "path": path, "text": ocr_texts.get(path, "")},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        return str(history_path)
    except OSError as exc:
        logger.error(
            "QQ image OCR history persist failed",
            error_type=type(exc).__name__,
            stack_trace=str(exc),
            context={"paths": paths},
        )
        return None


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
            super().__init__(intents=_build_intents(), ext_handlers=False, timeout=_HTTP_TIMEOUT)

        async def on_ready(self) -> None:
            name = getattr(getattr(self, "robot", None), "name", "QQBot")
            logger.info(f"QQ bot ready: {name}")

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
        self._running_tasks: dict[str, asyncio.Task] = {}  # user_id -> running agent task

    # --------------------------------------------------------------- sending

    async def _send_markdown(self, chat_id: str, content: str, *, is_group: bool, msg_id: str | None) -> None:
        """Send text as markdown, falling back to plain text on error. Retries on timeout."""
        if not self.client:
            return
        api = self.client.api.post_group_message if is_group else self.client.api.post_c2c_message
        key = "group_openid" if is_group else "openid"
        for part in _split_text(content):
            seq = _next_seq()
            for attempt in range(3):
                try:
                    await api(**{key: chat_id, "msg_type": 2, "markdown": {"content": part}, "msg_id": msg_id, "msg_seq": seq})
                    break
                except Exception:  # noqa: BLE001 — markdown unsupported or timeout, fall back to plain text
                    try:
                        await api(**{key: chat_id, "msg_type": 0, "content": part, "msg_id": msg_id, "msg_seq": seq})
                        break
                    except Exception:  # noqa: BLE001 — retry
                        if attempt == 2:
                            logger.error(f"QQ send failed after 3 attempts: {chat_id}")
                        await asyncio.sleep(1)

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

            # --- Image messages: multi-image accumulation via interrupt/resume ---
            image_urls = _extract_image_urls(data)
            if image_urls:
                asyncio.create_task(
                    self._handle_images_v2(chat_id, user_id, image_urls, content, msg_id=msg_id, is_group=is_group)
                )
                return

            if not content:
                return

            logger.info(f"QQ message from {user_id} ({'group' if is_group else 'c2c'}): {content[:80]}")

            # 1) Slash commands — check BEFORE interrupt so /stop works during ask_user.
            if content.startswith("/"):
                if content.strip() == "/stop":
                    _pending_interrupt.pop(user_id, None)
                await self._handle_command(chat_id, content, user_id=user_id, msg_id=msg_id, is_group=is_group)
                return

            # 2) If a paused graph exists (wait_for_text interrupt), resume with this text.
            thread_id = _user_threads.get(chat_id)
            if thread_id:
                config = {"configurable": {"thread_id": thread_id}}
                try:
                    state = self.graph.get_state(config)
                    if state and state.next:
                        await self._stream_agent(
                            chat_id, Command(resume=content), config,
                            msg_id=msg_id, is_group=is_group, user_id=user_id,
                        )
                        return
                except Exception:  # noqa: BLE001 — get_state 失败视为无暂停图
                    logger.debug("get_state failed, assuming no paused graph", user_id=user_id)

            # 3) If this user has a pending ask_user interrupt, resume the graph.
            if user_id in _pending_interrupt:
                config = _pending_interrupt.pop(user_id)
                asyncio.create_task(self._resume_agent(chat_id, content, config, msg_id=msg_id, is_group=is_group, user_id=user_id))
                return

            # 4) Normal agent turn.
            asyncio.create_task(self._run_agent(chat_id, content, user_id, msg_id=msg_id, is_group=is_group))

        except Exception:  # noqa: BLE001 — top-level message handler crash guard
            logger.error("QQ on_message error", stack_trace=traceback.format_exc())
            traceback.print_exc()

    # --------------------------------------------------------------- image batching

    async def _process_image(self, user_id: str) -> None:
        """Process image batches for one user until none remain.

        The batch is kept in ``_pending_image`` while downloading/OCR-ing so text
        arriving in that window merges into ``pending["text"]`` (same dict reference)
        instead of spawning a competing task that would cancel this one. When the
        user gave no text, the prompt tells the model to call ``wait_for_text``, which
        pauses the graph (interrupt) until the user's follow-up text resumes it.
        """
        try:
            while True:
                pending = _pending_image.get(user_id)
                if pending is None:
                    return
                chat_id = pending["chat_id"]
                is_group = pending["is_group"]
                try:
                    await self.send_text(chat_id, "识别中...", msg_id=pending["msg_id"], is_group=is_group)

                    os.makedirs("temp", exist_ok=True)
                    paths: list[str] = []
                    for i, url in enumerate(pending["urls"], start=1):
                        dest = os.path.abspath(f"temp/qq_img_{user_id}_{i}_{int(time.time())}.jpg")
                        if await _download_image(url, dest):
                            paths.append(dest)

                    if not paths:
                        _pending_image.pop(user_id, None)
                        await self.send_text(chat_id, "图片处理失败，请重试", is_group=is_group)
                        continue

                    # OCR locally first so the agent receives the recognized text directly
                    # in its prompt and can ANALYZE it, instead of being asked to call
                    # ocr_image itself (which would echo the raw tool result back).
                    ocr_texts: dict[str, str] = {}
                    for path in paths:
                        try:
                            result = await asyncio.to_thread(ocr_image.invoke, {"path": path})
                            ocr_texts[path] = str((result or {}).get("text") or "")
                        except Exception:  # noqa: BLE001 — per-image OCR failure degrades to empty text
                            logger.error("QQ image OCR failed", stack_trace=traceback.format_exc(), context={"path": path})
                            ocr_texts[path] = ""

                    # Take the batch (and any text merged in while we were working).
                    final = _pending_image.pop(user_id, None) or pending
                    user_text = final.get("text") or ""

                    history_path = _persist_ocr_history(Config.default().memory_dir, paths, ocr_texts)
                    prompt = _build_image_prompt(paths, ocr_texts, user_text, history_path=history_path)
                    if not user_text:
                        prompt += (
                            "\n\n[用户只发送了图片，没有附带任何文字说明。你必须先调用 wait_for_text "
                            "工具询问用户希望你对这张图片做什么（例如：描述图片内容、解释图中文字、分析问题），"
                            "等待用户回复后再继续分析。]"
                        )
                    logger.info(f"QQ image OCR from {user_id}: {len(paths)} image(s)")
                    await self._run_agent(chat_id, prompt, user_id, msg_id=final.get("msg_id"), is_group=is_group)
                except Exception:  # noqa: BLE001 — silent degradation
                    logger.error("QQ image handling error", stack_trace=traceback.format_exc())
                    _pending_image.pop(user_id, None)
                    await self.send_text(chat_id, "图片处理失败，请重试", is_group=is_group)
        finally:
            _image_processing.discard(user_id)

    def _start_agent_turn(self, chat_id: str, text: str, user_id: str, *, msg_id: str | None, is_group: bool) -> None:
        """Create the agent task for a plain text turn and register it for /stop."""
        task = asyncio.create_task(self._run_agent(chat_id, text, user_id, msg_id=msg_id, is_group=is_group))
        self._running_tasks[user_id] = task

    async def _drain_queue(self, user_id: str) -> None:
        """After a turn ends, start the next queued input for this user (FIFO)."""
        if self._running_tasks.get(user_id) is not None and not self._running_tasks[user_id].done():
            return
        q = _queued_inputs.get(user_id)
        if not q:
            return
        kind, payload = q.popleft()
        if kind == "text":
            chat_id, content, _uid, msg_id, is_group = payload
            self._start_agent_turn(chat_id, content, user_id, msg_id=msg_id, is_group=is_group)

    async def _auto_resume_image(self, user_id: str, chat_id: str, is_group: bool) -> None:
        """Timeout safety net: a wait_for_text interrupt auto-resumes if the user stays silent."""
        await asyncio.sleep(_IMAGE_WAIT_TIMEOUT)
        config = _pending_interrupt.pop(user_id, None)
        if config is not None:
            await self._resume_agent(chat_id, "直接分析这张图片", config, msg_id=None, is_group=is_group)

    # --------------------------------------------------------------- image handling

    # --------------------------------------------------------------- commands

    async def _handle_command(
        self, chat_id: str, cmd: str, *, user_id: str, msg_id: str | None, is_group: bool
    ) -> None:
        op = (cmd or "").split()[0].lower()
        if op == "/help":
            return await self.send_text(
                chat_id,
                "📖 命令列表:\n"
                "/help — 显示帮助\n"
                "/new — 开启新对话(清空上下文)\n"
                "/status — 查看当前会话状态\n"
                "/stop — 停止当前任务\n"
                "/reboot — 重启服务(管理员)",
                msg_id=msg_id,
                is_group=is_group,
            )
        if op == "/reboot":
            # Only admins may restart the whole process (reloads code, clears all state).
            if not _is_admin(user_id):
                return await self.send_text(chat_id, "⛔ 无权限", msg_id=msg_id, is_group=is_group)
            await self.send_text(chat_id, "✅ 正在重启，请稍候...", msg_id=msg_id, is_group=is_group)
            await asyncio.sleep(0.5)  # let the confirm message flush before the process dies
            if _instance_sock:
                _instance_sock.close()  # release the single-instance port for the new process
            os.execv(sys.executable, [sys.executable, __file__])
            return
        if op == "/new":
            old_thread = _user_threads.pop(chat_id, None)
            if old_thread:
                try:
                    self.graph.checkpointer.delete_thread(old_thread)
                except (KeyError, ValueError, LookupError):
                    pass
            _pending_image.pop(user_id, None)
            _image_processing.discard(user_id)
            _queued_inputs.pop(user_id, None)
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
            task = self._running_tasks.get(user_id)
            if task is not None and not task.done():
                task.cancel()
                return await self.send_text(chat_id, "⏹️ 正在停止任务...", msg_id=msg_id, is_group=is_group)
            return await self.send_text(chat_id, "⏹️ 当前无运行中的任务", msg_id=msg_id, is_group=is_group)
        await self.send_text(chat_id, "未知命令,输入 /help 查看", msg_id=msg_id, is_group=is_group)

    # --------------------------------------------------------------- agent

    def _thread_for(self, user_id: str) -> str:
        """Return (or create) the per-user thread_id so history persists across turns."""
        if user_id not in _user_threads:
            _user_threads[user_id] = f"qq-{user_id}-{uuid.uuid4().hex[:8]}"
        return _user_threads[user_id]

    async def _handle_images_v2(
        self,
        chat_id: str,
        user_id: str,
        image_urls: list[str],
        content: str,
        *,
        msg_id: str | None,
        is_group: bool,
    ) -> None:
        """Download images and route to either a paused graph (Command(update)) or a fresh one.

        When a graph is already paused at wait_for_text, images are injected via
        Command(update={"pending_images": [...], "messages": [...]}) so the
        classify_message node picks up the new paths and wait_for_text re-interrupts.
        When no graph is running, a fresh graph is started with [IMAGE:path] markers
        in the HumanMessage — classify_message routes to wait_for_text if text is
        absent, or directly to process if text accompanied the first image.
        """
        # Download images to temp/
        os.makedirs("temp", exist_ok=True)
        paths: list[str] = []
        ts = int(time.time())
        for i, url in enumerate(image_urls):
            dest = os.path.abspath(f"temp/qq_img_{user_id}_{i}_{ts}.jpg")
            if await _download_image(url, dest):
                paths.append(dest)

        if not paths:
            return

        config = {"configurable": {"thread_id": self._thread_for(user_id)}}

        # Check for a paused graph (wait_for_text interrupt).
        try:
            state = self.graph.get_state(config)
            if state and state.next:
                # Inject images via Command(update); classify_message picks up new paths.
                # Merge with existing pending_images so previous images are preserved.
                existing_images: list[str] = (state.values or {}).get("pending_images") or []
                all_images = existing_images + paths
                parts = [f"[IMAGE:{p}]" for p in paths]
                if content:
                    parts.append(content)
                update = {
                    "pending_images": all_images,
                    "messages": [HumanMessage(content="\n".join(parts))],
                }
                await self._stream_agent(
                    chat_id, Command(update=update), config,
                    msg_id=msg_id, is_group=is_group, user_id=user_id,
                )
                return
        except Exception:  # noqa: BLE001 — 暂停图检查失败，降级为新会话
            logger.debug("paused graph check failed, starting fresh", user_id=user_id)

        # No paused graph — start a fresh one. OCR the images for the prompt.
        ocr_texts: dict[str, str] = {}
        for path in paths:
            try:
                result = await asyncio.to_thread(ocr_image.invoke, {"path": path})
                ocr_texts[path] = str((result or {}).get("text") or "")
            except Exception:  # noqa: BLE001 — 单图 OCR 失败降级为空文本
                ocr_texts[path] = ""

        prompt = _build_image_prompt(paths, ocr_texts, content)
        _persist_ocr_history(Config.default().memory_dir, paths, ocr_texts)
        asyncio.create_task(self._run_agent(chat_id, prompt, user_id, msg_id=msg_id, is_group=is_group))

    async def _run_agent(
        self, chat_id: str, text: str, user_id: str, *, msg_id: str | None, is_group: bool
    ) -> None:
        """Run one agent turn from scratch (new HumanMessage appended to the user's thread)."""
        thread_id = self._thread_for(user_id)
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": DEFAULT_RECURSION_LIMIT}
        state = new_state(text, Config.default())
        await self._stream_agent(chat_id, state, config, msg_id=msg_id, is_group=is_group, user_id=user_id)

    async def _resume_agent(
        self, chat_id: str, answer: str, config: dict, *, msg_id: str | None, is_group: bool, user_id: str
    ) -> None:
        """Resume a paused (ask_user) turn with the user's answer."""
        await self._stream_agent(
            chat_id, Command(resume=answer), config, msg_id=msg_id, is_group=is_group, user_id=user_id
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
        """Stream graph updates: render tool calls, tool results, final answer; handle interrupts.

        The wrapper graph nests the compiled create_agent subgraph as the ``process`` node
        and ``cleanup_images`` returns the full message list, so ``stream_mode="updates"``
        emits the same messages in multiple chunks (and previous turns' messages inside
        later turns' full-state chunks). Tracking rendered message ids per user keeps each
        message sent exactly once.
        """
        reply_parts: list[str] = []
        rendered: set[str] = _rendered_msg_ids.setdefault(user_id, set()) if user_id is not None else set()
        current_task = asyncio.current_task()
        if current_task is not None and user_id is not None:
            # Cancel any previous running task for this user before registering the new one.
            old = self._running_tasks.get(user_id)
            if old is not None and not old.done():
                old.cancel()
            self._running_tasks[user_id] = current_task
        try:
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
                            if isinstance(value, dict) and "waiting_for_text" in value:
                                # wait_for_text interrupt: graph paused, waiting for user text
                                count = value.get("pending_count", 0)
                                await self.send_text(
                                    chat_id, f"[image] 已收到 {count} 张图片，请发送文字说明...",
                                    is_group=is_group,
                                )
                                return
                        continue

                    for update in chunk.values():
                        if not isinstance(update, dict):
                            continue
                        for message in update.get("messages", []):
                            mid = getattr(message, "id", None)
                            if mid is not None and mid in rendered:
                                continue  # already sent by an earlier chunk (or an earlier turn)
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
                            if mid is not None:
                                rendered.add(mid)

                # Stream finished: send the final answer (if not already sent via interrupt).
                if reply_parts:
                    # Strip <summary>...</summary> blocks (GA protocol) instead of dropping
                    # the whole message — the model often puts the summary FIRST, followed
                    # by the real answer, so startswith("<summary>") would swallow the reply.
                    final_parts: list[str] = []
                    for part in reply_parts:
                        cleaned = _SUMMARY_TAG_RE.sub("", part).strip()
                        if cleaned:
                            final_parts.append(cleaned)
                    final_text = "\n\n".join(final_parts)
                    if final_text.strip():
                        await self.send_text(chat_id, final_text, is_group=is_group)

            except asyncio.CancelledError:
                logger.info(f"Agent task cancelled for user {user_id}")
                await self.send_text(chat_id, "⏹️ 任务已停止", is_group=is_group)
                raise

        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — top-level stream crash guard
            logger.error("QQ agent stream error", stack_trace=traceback.format_exc())
            await self.send_text(chat_id, "❌ 运行出错,请稍后重试或输入 /new 重置", is_group=is_group)
        finally:
            if user_id is not None and self._running_tasks.get(user_id) is current_task:
                self._running_tasks.pop(user_id, None)
                await self._drain_queue(user_id)

    # --------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Start the QQ bot with auto-reconnect (exponential backoff)."""
        self.client = _make_bot_class(self)()
        delay = _RECONNECT_INITIAL
        while True:
            started_at = time.monotonic()
            try:
                logger.info(f"QQ bot starting... {time.strftime('%m-%d %H:%M')}")
                await self.client.start(appid=_APP_ID, secret=_APP_SECRET)
            except Exception as e:  # noqa: BLE001 — top-level reconnect guard
                logger.error(f"QQ bot error: {e}")
            if time.monotonic() - started_at >= 60:
                delay = _RECONNECT_INITIAL
            logger.warning(f"QQ reconnect in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)


# --------------------------------------------------------------------------- entry

def _ensure_single_instance(port: int = 19528) -> None:
    """Prevent two QQ frontends from running simultaneously."""
    global _instance_sock
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", port))
    except OSError:
        logger.warning("Another instance is already running, skipping...")
        sys.exit(1)
    _instance_sock = sock


def _redirect_log() -> None:
    """Redirect stdout/stderr to a log file if QQ_LOG_FILE is set."""
    if not _LOG_FILE:
        return
    log_dir = os.path.dirname(_LOG_FILE) or "."
    os.makedirs(log_dir, exist_ok=True)
    logf = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 — intentional lifetime redirect
    sys.stdout = sys.stderr = logf
    logger.info(f"QQ process starting at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"QQ allow list: {'public' if _is_public() else sorted(_ALLOWED)}")


def _fix_encoding() -> None:
    """Force stdout/stderr to UTF-8 so Chinese text renders correctly on Windows terminals."""
    if sys.stdout.encoding.upper() != "UTF-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr.encoding.upper() != "UTF-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Entry point for ``python -m gacore.frontends.qq``."""
    _fix_encoding()

    if not _APP_ID or not _APP_SECRET:
        logger.error("Please set QQ_APP_ID and QQ_APP_SECRET in .env")
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
