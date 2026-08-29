"""Proactive QQ outreach pipeline for gacore (P0 of the 主动交互 design, v0.2).

The scheduler fires ``type == "proactive"`` jobs: ``run_loop`` first applies the
window/cooldown gate (``proactive_due``), then hands the job to the module-level
single-worker ``PROACTIVE_POOL`` so the poll loop is never blocked by LLM generation
or QQ network I/O. ``run_proactive_job`` (running in the worker thread) enforces the
Job Guard (daily cap / hot-chat cooldown / idle threshold), picks target users from
``data/qq_known_users.json``, runs a headless agent whose prompt requires calling the
existing ``qq_push`` tool to actually deliver, and persists every outcome to
``data/proactive_state.json`` (atomic write, idempotent, East-8 timestamps).

Design constraints inherited from the v0.2 doc:
  - No new send tool / no queue consumer / no write-back to the main thread (avoids
    touching the checkpointer from a worker thread);
  - Every time judgment uses East-8 (``_TZ``), aligned with the project time rules;
  - Proactive messages are not user facts: the prompt explicitly marks them as such,
    and memory sedimentation only extracts from HumanMessages anyway.
"""

from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Final

from gacore.config import Config
from gacore.jsonl_logger import get_logger
from gacore.langTrack.qq_push import load_known_users

if TYPE_CHECKING:
    from gacore.scheduler import Job, JobState

logger = get_logger("proactive")

_TZ: Final = timezone(timedelta(hours=8))
_STATE_FILE: Final = "proactive_state.json"
_GUARD_FILE: Final = "proactive.json"
_WINDOW_RE: Final = re.compile(r"^(\d{1,2}):(\d{2})$")

_POOL_MAX_WORKERS: Final = 1
_POOL_QUEUE_MAXSIZE: Final = 2

# Job Guard default thresholds (aligned with design doc §6.1).
_MAX_PER_DAY: Final = 2          # daily cap: at most N proactive messages per user per day
_IDLE_HOURS: Final = 24          # idle threshold: only reach out when last_active older than this
_HOT_CHAT_MINUTES: Final = 30    # hot-chat cooldown: never interrupt while the user is active
_MAX_FAIL_RETRIES: Final = 3     # per-user per-day delivery-failure retry cap (M3): once a day's
                                 # failed_count reaches this, the window will not keep re-calling

# P1 jitter: recommended default is ±30min (config/proactive.json carries it). The code
# level default is 0 (= no jitter) so environments without a guard config file keep the
# deterministic P0 behaviour; jitter only activates once the file explicitly sets it.
_JITTER_MINUTES_DEFAULT: Final = 30
_JITTER_PROB_MIN: Final = 0.05   # floor on the per-tick pass probability (never fully muted)

# P1 Topic Recall — lightweight "not a real question" gate (independent, dependency-free
# reimplementation of qq.py::trivial_detect semantics; proactive never imports the QQ
# frontend to avoid dragging botpy into the worker path).
_TRIVIAL_MAX_LEN: Final = 8
_INTENT_WORDS: Final = frozenset({
    "如何", "怎么", "怎样", "帮我", "请", "推荐", "啥", "什么", "为什么", "几点",
    "吗", "呢", "要不要", "能不能", "可不可以", "在哪", "哪里", "多少钱",
    "怎么办", "聊聊", "说说", "解释", "分析", "对比", "建议",
})
_TRIVIAL_WORDS: Final = frozenset({
    "嗯", "哦", "好", "哈", "哈哈", "呵呵", "ok", "好的", "知道了", "收到",
    "晚安", "早安", "在吗", "在不在", "吃饭", "下班", "加班", "忙", "睡了",
    "没事", "没事了", "算了", "行", "可以", "666", "赞", "nice", "嗯呢",
})

_state_lock = threading.Lock()


# --------------------------------------------------------------------------- bounded pool


class _BoundedExecutor:
    """ThreadPoolExecutor with a bounded pending queue.

    ``submit`` returns None when ``queue_maxsize`` tasks are already pending or running,
    so a busy pool never accumulates work — the caller simply skips the current tick.
    """

    def __init__(self, max_workers: int, queue_maxsize: int, thread_name_prefix: str) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        self._queue_maxsize = queue_maxsize
        self._lock = threading.Lock()
        self._pending = 0

    def submit(self, fn: Callable[..., object], *args: object, **kwargs: object) -> Future | None:
        """Submit a task; return None when the bounded queue is full."""
        with self._lock:
            if self._pending >= self._queue_maxsize:
                return None
            self._pending += 1
        try:
            future = self._pool.submit(fn, *args, **kwargs)
        except Exception:
            with self._lock:
                self._pending -= 1
            raise
        future.add_done_callback(self._on_done)
        return future

    def _on_done(self, _future: Future) -> None:
        with self._lock:
            self._pending -= 1

    def shutdown(self, wait: bool = True) -> None:
        """Shut the underlying pool down (used by tests only)."""
        self._pool.shutdown(wait=wait)


PROACTIVE_POOL = _BoundedExecutor(
    max_workers=_POOL_MAX_WORKERS,
    queue_maxsize=_POOL_QUEUE_MAXSIZE,
    thread_name_prefix="proactive",
)


# --------------------------------------------------------------------------- time helpers


def _now_east8() -> datetime:
    """Return the current East-8 aware datetime (project canonical clock)."""
    return datetime.now(_TZ)


def _as_east8(dt: datetime) -> datetime:
    """Coerce a datetime to East-8 aware; naive inputs are assumed East-8."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ)


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO timestamp string; return None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- guard config


def _guard_path(cfg: Config) -> Path:
    """Return the guard config file path (config/proactive.json)."""
    return cfg.root / "config" / _GUARD_FILE


def load_guard_config(cfg: Config) -> dict:
    """Load the ``guard`` section of config/proactive.json (P1).

    Returns an empty dict when the file is missing or malformed — every gate then
    falls back to the module-level default thresholds (i.e. the exact P0 behaviour),
    so environments without a guard config stay fully deterministic.
    """
    path = _guard_path(cfg)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    guard = raw.get("guard")
    return guard if isinstance(guard, dict) else {}


def _guard_int(guard: dict, key: str, default: int) -> int:
    """Parse one guard value as int with a safe fallback."""
    raw = guard.get(key) if guard else None
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _jitter_minutes(guard: dict) -> int:
    """P1 jitter window from the guard config; 0 (= off) when not configured.

    The shipped config/proactive.json sets ``guard.jitter_minutes: 30`` (design doc
    §5.3 default ±30min); code-level fallback is 0 so a missing file never introduces
    random behaviour.
    """
    return _guard_int(guard, "jitter_minutes", 0)


def jitter_allows(
    job: Job,
    jitter_minutes: int,
    rng: Callable[[], float] | None = None,
) -> tuple[bool, str]:
    """P1 randomisation gate: with probability p pass the tick, else skip with jitter.

    A proactive job can't express "HH:MM ± offset" in its schedule, so the jitter is
    applied at trigger time: each eligible tick passes with probability
    ``p = 60 / (60 + jitter_minutes)`` (jitter=30 → 2/3, i.e. a ~30min mean shift on
    an hourly cadence). ``jitter_minutes <= 0`` disables the gate entirely (P0
    behaviour). ``rng`` is the test seam (defaults to ``random.random``).
    """
    if jitter_minutes <= 0:
        return True, ""
    p = max(_JITTER_PROB_MIN, min(0.95, 60.0 / (60.0 + float(jitter_minutes))))
    roll = rng() if rng else random.random()
    if roll < p:
        return True, ""
    return False, "jitter_skip"


# --------------------------------------------------------------------------- window / cooldown


def parse_window(window: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Parse "HH:MM-HH:MM" into ((start_h, start_m), (end_h, end_m)); None when empty/invalid."""
    if not window or not window.strip():
        return None
    parts = [p.strip() for p in window.split("-")]
    if len(parts) != 2:
        return None
    bounds: list[tuple[int, int]] = []
    for part in parts:
        m = _WINDOW_RE.match(part)
        if m is None:
            return None
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        bounds.append((hour, minute))
    return (bounds[0], bounds[1])


def in_window(window: str, now: datetime) -> bool:
    """True when ``now``'s time-of-day falls inside the window (closed interval).

    An empty window always returns True. Cross-midnight windows (start > end, e.g.
    "22:30-23:59" style reversed ranges) are treated as "after start OR before end".
    """
    parsed = parse_window(window)
    if parsed is None:
        return True
    (sh, sm), (eh, em) = parsed
    # Normalize to East-8 first: window bounds are East-8 wall-clock times, so a
    # UTC (or any other tz) input must be converted before comparing time-of-day.
    now_e = _as_east8(now)
    current = now_e.hour * 60 + now_e.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def cooldown_ok(job: Job, state: JobState, now: datetime) -> bool:
    """Job-level trigger cooldown: at least ``cooldown_minutes`` since last_run."""
    if not job.cooldown_minutes or job.cooldown_minutes <= 0:
        return True
    last = _parse_iso(state.last_run) if state.last_run else None
    if last is None:
        return True
    return (_as_east8(now) - _as_east8(last)) >= timedelta(minutes=int(job.cooldown_minutes))


def proactive_due(job: Job, state: JobState, now: datetime) -> bool:
    """Extra due gate for proactive jobs: inside the window AND past the cooldown."""
    if not in_window(job.window, now):
        return False
    return cooldown_ok(job, state, now)


# --------------------------------------------------------------------------- state persistence


def _state_path(cfg: Config) -> Path:
    """Return the proactive state file path (data/proactive_state.json)."""
    return cfg.root / "data" / _STATE_FILE


def load_state(cfg: Config) -> dict:
    """Load proactive_state.json; empty dict when missing or corrupt."""
    path = _state_path(cfg)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(cfg: Config, state: dict) -> None:
    """Persist proactive_state.json atomically (temp file + os.replace)."""
    path = _state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _with_state(cfg: Config, mutate: Callable[[dict], None]) -> dict:
    """Load → mutate → atomic save under the module lock; returns the saved state."""
    with _state_lock:
        state = load_state(cfg)
        mutate(state)
        save_state(cfg, state)
    return state


def record_last_active(user_id: str, cfg: Config | None = None, now: datetime | None = None) -> None:
    """Record a user's last_active into proactive_state.json (best-effort, never raises).

    Called from the QQ frontend on every incoming message so proactive jobs can tell
    how recently the user was active. Failure is logged, never blocks the chat path.
    """
    if not user_id or user_id in ("unknown", "None"):
        return
    try:
        resolved_cfg = cfg or Config.default()
        ts = (now or _now_east8()).isoformat(timespec="seconds")

        def _mutate(state: dict) -> None:
            key = f"u_{user_id}"
            entry = state.setdefault(key, {})
            entry["last_active"] = ts
            entry["updated_at"] = ts
            entry.setdefault("created_at", ts)

        _with_state(resolved_cfg, _mutate)
    except Exception as exc:  # noqa: BLE001 — activity tracking must never block chat
        logger.error(
            "record_last_active failed",
            user=user_id,
            error_type=type(exc).__name__,
            stack_trace=str(exc),
        )


def _mark_sent(cfg: Config, user_id: str, scene: str, now: datetime) -> None:
    """Atomically bump the per-user sent bookkeeping for one successful delivery."""
    ts = now.isoformat(timespec="seconds")
    today = now.date().isoformat()

    def _mutate(state: dict) -> None:
        key = f"u_{user_id}"
        entry = state.setdefault(key, {})
        last_sent = entry.setdefault("last_sent", {})
        last_sent[scene] = ts
        if entry.get("last_sent_date") != today:
            entry["last_sent_date"] = today
            entry["daily_count"] = 1
        else:
            entry["daily_count"] = (entry.get("daily_count") or 0) + 1
        entry["last_sent_time"] = ts
        # A successful delivery resets the per-day failure counters (M3): a later
        # failure retry is a fresh attempt on a healthy channel, not a continuation.
        entry.pop("last_failed", None)
        entry.pop("failed_date", None)
        entry.pop("failed_count", None)
        entry["updated_at"] = ts
        entry.setdefault("created_at", ts)

    _with_state(cfg, _mutate)


def _mark_failed(cfg: Config, user_id: str, scene: str, now: datetime) -> None:
    """Atomically record one failed delivery attempt per user (M3).

    Persists ``last_failed`` (timestamp), ``failed_date`` (East-8 date) and a per-day
    ``failed_count`` so the retry cap can stop a job from hammering a dead channel
    every tick inside the window.
    """
    ts = now.isoformat(timespec="seconds")
    today = _as_east8(now).date().isoformat()

    def _mutate(state: dict) -> None:
        key = f"u_{user_id}"
        entry = state.setdefault(key, {})
        if entry.get("failed_date") != today:
            entry["failed_date"] = today
            entry["failed_count"] = 1
        else:
            entry["failed_count"] = (entry.get("failed_count") or 0) + 1
        entry["last_failed"] = ts
        entry["updated_at"] = ts
        entry.setdefault("created_at", ts)

    _with_state(cfg, _mutate)


def _failure_exhausted(entry: dict, now: datetime) -> bool:
    """True when today's delivery-failure count already hit the retry cap (M3).

    Only counts failures recorded on the same East-8 date; a stale counter from a
    previous day is ignored so the cap resets naturally at midnight.
    """
    today = _as_east8(now).date().isoformat()
    if entry.get("failed_date") != today:
        return False
    return (entry.get("failed_count") or 0) >= _MAX_FAIL_RETRIES


# --------------------------------------------------------------------------- Job Guard


def _scene_of(job: Job) -> str:
    """Derive the scene key; an explicit ``job.scene`` wins over name sniffing (L4)."""
    if getattr(job, "scene", ""):
        return job.scene
    name = job.name.lower()
    for scene in ("morning", "night", "idle"):
        if scene in name:
            return scene
    return job.name


def _target_users(job: Job) -> list[str]:
    """Target openids for this job: v0.2 always uses all known users (target=auto)."""
    return list(load_known_users().keys())


def job_guard_allows(
    user_id: str,
    job: Job,
    state: dict,
    now: datetime,
    guard: dict | None = None,
) -> tuple[bool, str]:
    """Job Guard gate: daily cap / hot-chat cooldown / idle threshold (+ monotonic).

    Thresholds come from the optional ``guard`` dict (config/proactive.json, P1) with
    per-key fallback to the module-level defaults — so a missing config file keeps the
    exact P0 behaviour. Returns (allowed, reason) where reason is a machine-readable
    tag describing the denial, or an empty string when allowed.
    """
    max_per_day = _guard_int(guard, "max_per_day", _MAX_PER_DAY)
    hot_chat_minutes = _guard_int(guard, "hot_chat_minutes", _HOT_CHAT_MINUTES)
    idle_hours = _guard_int(guard, "idle_hours", _IDLE_HOURS)

    entry = state.get(f"u_{user_id}") or {}
    now_e = _as_east8(now)
    today = now_e.date().isoformat()

    # 1. Daily cap.
    if entry.get("last_sent_date") == today and (entry.get("daily_count") or 0) >= max_per_day:
        return False, "daily_cap"

    # 2. Hot-chat cooldown: never interrupt while the user was active recently.
    last_active = entry.get("last_active")
    last_e: datetime | None = None
    if last_active:
        last_dt = _parse_iso(last_active)
        if last_dt is not None:
            last_e = _as_east8(last_dt)
            if (now_e - last_e) < timedelta(minutes=hot_chat_minutes):
                return False, "hot_chat"

    # 3. Idle threshold + monotonic (idle-scene jobs only).
    if "idle" in job.name.lower():
        if last_e is not None and (now_e - last_e) <= timedelta(hours=idle_hours):
            return False, "not_idle"
        # Monotonic: only re-reach-out after the user becomes active again past the
        # previous idle send; otherwise the idle outreach would hammer every tick.
        if last_e is not None:
            last_sent_idle = (entry.get("last_sent") or {}).get("idle")
            if last_sent_idle:
                sent_dt = _parse_iso(last_sent_idle)
                if sent_dt is not None and last_e < _as_east8(sent_dt):
                    return False, "idle_already_sent"
        # No last_active at all: the user is in known_users but we have no state yet —
        # treat them as idle-long-enough and allow the first outreach.
    return True, ""


# --------------------------------------------------------------------------- P1 topic recall


def _threads_path(cfg: Config) -> Path:
    """Path of the QQ openid -> thread_id mapping file."""
    return cfg.root / "data" / "qq_user_threads.json"


def _thread_id_of(cfg: Config, user_id: str) -> str:
    """Look up the user's main-chat thread_id (empty when unmapped)."""
    path = _threads_path(cfg)
    if not path.is_file():
        return ""
    try:
        mapping = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(mapping, dict):
        return ""
    tid = mapping.get(user_id)
    return str(tid) if tid else ""


def _read_thread_messages(cfg: Config, thread_id: str) -> list:
    """Read the user's main thread messages (P1, read-only).

    Concurrency scheme (avoids review H3): the worker thread opens a *separate,
    throwaway* read-only sqlite3 connection (`mode=ro`) to the same checkpoints DB
    and uses the synchronous langgraph ``SqliteSaver`` to fetch the latest snapshot.
    It never shares the async connection with the main asyncio loop and never bridges
    through asyncio, so there is no cross-thread asyncio single-connection hazard.
    SQLite allows concurrent connections, and a read-only URI can't take a write lock.
    Any failure (missing DB, missing table, corrupt snapshot) degrades to [] and is
    logged — Topic Recall is best-effort and must never break the outreach.

    Returns a list of langchain message objects (or raw dicts), newest last.
    """
    db = cfg.root / "data" / "gacore_chat.db"
    if not thread_id or not db.is_file():
        return []
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        uri = f"file:{db.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            saver = SqliteSaver(conn)
            snapshot = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "topic recall: read thread failed",
            thread_id=thread_id,
            error_type=type(exc).__name__,
            stack_trace=str(exc),
        )
        return []
    if snapshot is None:
        return []
    values = (snapshot.checkpoint or {}).get("channel_values") or {}
    msgs = values.get("messages") or []
    return list(msgs) if isinstance(msgs, (list, tuple)) else []


def _msg_type(msg) -> str:
    """Message type tag, tolerant of both langchain objects and plain dicts."""
    t = getattr(msg, "type", None)
    if t:
        return str(t)
    if isinstance(msg, dict):
        return str(msg.get("type") or "")
    # Last-resort fallback (audit fix L4): prefix-match the class name so
    # "HumanMessage" -> "human", "AIMessage" -> "ai", "ToolMessage" -> "tool",
    # "SystemMessage" -> "system" instead of leaking the raw class name.
    name = type(msg).__name__.lower()
    for prefix in ("human", "system", "ai", "tool"):
        if name.startswith(prefix):
            return prefix
    return name


def _msg_text(msg) -> str:
    """Message text content, tolerant of both langchain objects and plain dicts."""
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(msg, dict):
        c = msg.get("content")
        return c if isinstance(c, str) else ""
    return ""


def _is_trivial(text: str) -> bool:
    """Lightweight "not a real question" gate for Topic Recall.

    Mirrors qq.py::trivial_detect semantics without importing the QQ frontend: a
    message carrying explicit intent words is always non-trivial; otherwise short
    filler (<= 8 chars) or pure reaction words is treated as trivial.

    Audit fix (M1): an exact chit-chat filler now wins *before* the intent-word
    check, so "在吗" / "嗯呢" — which contain the broad intent chars "吗" / "呢" —
    are treated as trivial small talk instead of being mis-detected as open
    questions. Longer messages that merely mention a filler word still fail open.
    """
    t = (text or "").strip()
    if not t:
        return True
    # Exact filler hit first (tolerating trailing punctuation): "在吗？" -> trivial.
    if t.rstrip("？！!?。~～ ") in _TRIVIAL_WORDS:
        return True
    if any(w in t for w in _INTENT_WORDS):
        return False
    if len(t) <= _TRIVIAL_MAX_LEN:
        return True
    return any(w in t for w in _TRIVIAL_WORDS)


def _open_question_from(messages: list) -> str:
    """Extract the latest user message that the agent has NOT closed yet.

    A message counts as an open question when it is the last user turn and no AI
    message follows it (a trailing ToolMessage is fine — that's the agent still
    processing). Trivial filler never counts. Returns the raw text or "".
    """
    last_human = -1
    for i, msg in enumerate(messages):
        if _msg_type(msg) == "human":
            last_human = i
    if last_human < 0:
        return ""
    for msg in messages[last_human + 1 :]:
        if _msg_type(msg) == "ai":
            return ""
    text = _msg_text(messages[last_human])
    if _is_trivial(text):
        return ""
    return text


def _load_onboard_pack(cfg: Config, now: datetime) -> dict | None:
    """Load yesterday's onboard pack if present and dated before today.

    The onboard pack is the cross-day memory bundle (daily summary + long-term
    portrait) consumed by qq.py::_maybe_rollover; when it still exists for a previous
    date it is the richest "yesterday portrait" source.
    """
    path = cfg.root / "data" / "onboard_pack.json"
    if not path.is_file():
        return None
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(pack, dict):
        return None
    pack_date = str(pack.get("date") or "").strip()
    if not pack_date or pack_date >= _as_east8(now).date().isoformat():
        return None
    return pack


def _yesterday_daily_text(cfg: Config, now: datetime) -> str:
    """Yesterday portrait for the "昨天看到你在忙 XX" injection (P1).

    Priority: (1) yesterday's onboard pack daily_summary_md / long_term_md;
    (2) yesterday's daily-note bullet recap (via the shared daily_notes helper).
    Returns a compact snippet or "".
    """
    pack = _load_onboard_pack(cfg, now)
    if pack:
        payload = pack.get("payload") if isinstance(pack.get("payload"), dict) else {}
        daily = str(payload.get("daily_summary_md") or "").strip()
        if daily:
            return _snippet(daily, 240)
        long_term = str(payload.get("long_term_md") or "").strip()
        if long_term:
            return _snippet(long_term, 240)
    try:
        from gacore.tools.daily_notes import load_recent_daily_summaries

        notes = load_recent_daily_summaries(cfg, days=2)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "topic recall: daily note read failed",
            error_type=type(exc).__name__,
            stack_trace=str(exc),
        )
        return ""
    if not notes:
        return ""
    yesterday = (_as_east8(now) - timedelta(days=1)).date().isoformat()
    # load_recent_daily_summaries returns blocks like "[YYYY-MM-DD]\n- bullet..." —
    # pick the block whose header date equals yesterday.
    for block in re.split(r"(?m)^\[", notes):
        if block.startswith(yesterday):
            body = block[len(yesterday) + 1 :].strip()
            return _snippet(body, 240)
    return ""


def recall_topic(cfg: Config, user_id: str, now: datetime) -> dict:
    """P1 Topic Recall: pull "last unfinished topic" or "yesterday portrait".

    Returns a dict::

        {"kind": "open_question"|"daily_note"|"none",
         "text": <recalled snippet or "">,
         "thread_id": <main thread id or "">}

    Prefers the last open question from the user's main thread; falls back to the
    yesterday portrait when the user left nothing open or the thread is unreadable.
    Both inputs are injected by the caller into the proactive prompt.
    """
    thread_id = _thread_id_of(cfg, user_id)
    if thread_id:
        messages = _read_thread_messages(cfg, thread_id)
        question = _open_question_from(messages)
        if question:
            return {"kind": "open_question", "text": question, "thread_id": thread_id}
    insight = _yesterday_daily_text(cfg, now)
    if insight:
        return {"kind": "daily_note", "text": insight, "thread_id": thread_id}
    return {"kind": "none", "text": "", "thread_id": thread_id}


# --------------------------------------------------------------------------- prompt & headless run


def build_proactive_prompt(
    job: Job,
    user_id: str,
    now: datetime,
    topic: str = "",
    insight: str = "",
) -> str:
    """Assemble the proactive-outreach prompt for the headless agent.

    The prompt tells the model it MUST call the existing ``qq_push`` tool to deliver;
    not calling it counts as giving up this round. It also marks the message as a
    proactive greeting, not a user fact, and pins the real East-8 time anchor.

    P1 Topic Recall: ``topic`` (last open question from the user's main thread) and
    ``insight`` (yesterday portrait) are injected as background material so the model
    can pick the conversation back up ("上次聊到…还没说完") or reference what the user
    was busy with yesterday ("昨天看到你在忙 XX").
    """
    scene = _scene_of(job)
    base = (job.prompt or "").strip()
    scene_line = f"场景说明：{base}" if base else f"场景：{scene}"
    now_line = now.strftime("%Y-%m-%d %H:%M:%S")

    recall_lines: list[str] = []
    if topic:
        recall_lines.append(f"最近话题（主人上次抛出、还没说完）：{topic}")
    if insight:
        recall_lines.append(f"昨日画像线索（可自然引用\"昨天看到你在忙…\"）：{insight}")
    recall_block = ""
    if recall_lines:
        recall_block = "\n".join(recall_lines) + "\n"

    topic_note = ""
    if topic:
        topic_note = (
            "4. 有最近话题时，优先自然地把上次没说完的话题接上，别硬转新话题；"
            "只有确无话题可接时才另起家常话头；\n"
        )

    return (
        "目标：主动给主人发一条 QQ 私聊消息（≤200字，韩立口吻，克制不啰嗦）。\n"
        f"目标收件人 openid：{user_id}（调用 qq_push 时必须把该 openid 原样填入 to 参数，"
        "不得改动、猜测或留空，否则消息会发错人）。\n"
        f"{scene_line}\n"
        f"当前时间（东八区）：{now_line}\n"
        f"{recall_block}"
        "动作：内容想好后，必须调用 qq_push(message=..., to=<上面的目标 openid>) 工具把这条消息"
        "主动发给主人；不调用工具视为放弃本轮。\n"
        "约束：\n"
        "1. 用真实当前时间作话题依据，拿不准就明确不引用时间；\n"
        "2. 不提\"我是机器人/自动消息/定时任务\"；\n"
        "3. 结尾自然给用户一个可接话的口子，但别连续追问；\n"
        f"{topic_note}"
        "5. 本条为主动问候，不计入用户事实，不得据此沉淀用户画像/作息。"
    )


def _headless_run(job: Job, cfg: Config, prompt: str, max_turns: int) -> tuple[str | None, str, list[str]]:
    """Run a single-turn headless agent over the full tool list.

    Returns (exit_reason, reply, qq_push_results). ``qq_push_results`` is the list of
    ToolMessage contents of every qq_push tool call (empty when the model never called
    the tool). The list form lets the caller check the *latest* tool result, which is
    what decides delivery success.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    from gacore.graph import build_graph, run_once

    graph = build_graph(cfg=cfg)
    thread_id = f"proactive-{uuid.uuid4().hex[:8]}"
    state = run_once(graph, prompt, thread_id=thread_id, max_turns=max_turns)
    exit_reason = state.get("exit_reason")
    messages = state.get("messages") or []

    qq_push_results: list[str] = []
    for msg in messages:
        if (
            isinstance(msg, ToolMessage)
            and getattr(msg, "name", None) == "qq_push"
            and isinstance(msg.content, str)
        ):
            qq_push_results.append(msg.content)

    reply = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content:
            reply = msg.content
            break
    return exit_reason, reply, qq_push_results


def _qq_push_sent(qq_push_results: list[str]) -> bool:
    """True when the latest qq_push tool result reports status "sent" (M4).

    Parses the ToolMessage content as JSON instead of substring matching, so a payload
    like `{"ok": true, "sent": {"to": ["x"]}}` is recognized and an unrelated string
    (or an unparseable payload) counts as NOT sent.
    """
    if not qq_push_results:
        return False
    latest = qq_push_results[-1]
    try:
        parsed = json.loads(latest)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and parsed.get("status") == "sent"


def _snippet(text: str | None, limit: int = 200) -> str:
    """First ``limit`` chars of text on one line, for log/result payloads."""
    if not text:
        return ""
    return text.replace("\n", " ")[:limit]


# --------------------------------------------------------------------------- main entry


def run_proactive_job(
    job: Job,
    cfg: Config | None = None,
    clock: Callable[[], datetime] | None = None,
    rng: Callable[[], float] | None = None,
) -> dict:
    """Execute one proactive job in a worker thread (P0 pipeline + P1 additions).

    Flow: P1 jitter gate (probability-pass, only when config sets jitter_minutes) →
    Job Guard (daily cap / hot-chat cooldown / idle threshold, thresholds overridable
    via config/proactive.json) → target users → P1 Topic Recall (last open question
    or yesterday portrait) → headless agent generation → the LLM must call the
    existing ``qq_push`` tool to deliver → persist outcome to proactive_state.json.
    Runs entirely in the pool worker thread; a failure for one user never blocks the
    remaining users.

    ``clock`` is the test injection seam; None uses the East-8 wall clock. ``rng`` is
    the jitter test seam; None uses ``random.random``.
    """
    resolved_cfg = cfg or Config.default()
    now = clock() if clock else _now_east8()
    scene = _scene_of(job)
    state = load_state(resolved_cfg)
    guard = load_guard_config(resolved_cfg)
    jitter = _jitter_minutes(guard)
    targets = _target_users(job)

    result: dict = {
        "job": job.name,
        "scene": scene,
        "now": now.isoformat(timespec="seconds"),
        "attempted": 0,
        "sent": 0,
        "skipped": [],
        "errors": [],
    }
    if not targets:
        logger.warning("proactive job skipped: no known users", job=job.name)
        result["skipped"].append({"user": "", "reason": "no_known_users"})
        return result

    # P1: job-level jitter — with probability p skip this whole tick so the outreach
    # does not fire on a rigid schedule (config/proactive.json guard.jitter_minutes).
    allowed_j, reason_j = jitter_allows(job, jitter, rng)
    if not allowed_j:
        logger.info(
            "proactive job jittered off",
            job=job.name,
            jitter_minutes=jitter,
            reason=reason_j,
        )
        result["skipped"].append({"user": "", "reason": reason_j})
        return result

    for uid in targets:
        allowed, reason = job_guard_allows(uid, job, state, now, guard=guard)
        if not allowed:
            result["skipped"].append({"user": uid, "reason": reason})
            continue
        # M3: delivery-failure retry cap — once this user already failed N times today,
        # stop re-calling them inside the window (guarded skip, not an attempt).
        if _failure_exhausted(state.get(f"u_{uid}") or {}, now):
            result["skipped"].append({"user": uid, "reason": "fail_retries_exhausted"})
            continue
        result["attempted"] += 1
        try:
            # P1 Topic Recall: last open question first, yesterday portrait as fallback.
            recall = recall_topic(resolved_cfg, uid, now)
            topic = recall["text"] if recall["kind"] == "open_question" else ""
            insight = recall["text"] if recall["kind"] == "daily_note" else ""
            if topic or insight:
                logger.info(
                    "proactive: topic recall injected",
                    job=job.name,
                    user=uid,
                    kind=recall["kind"],
                    text=_snippet(topic or insight, 120),
                )
            prompt = build_proactive_prompt(job, uid, now, topic=topic, insight=insight)
            exit_reason, reply, qq_results = _headless_run(job, resolved_cfg, prompt, job.max_turns)
            if not qq_results:
                logger.info(
                    "proactive: llm decided not to push",
                    job=job.name,
                    user=uid,
                    exit_reason=exit_reason,
                )
                result["skipped"].append({"user": uid, "reason": "llm_no_push"})
                continue
            if not _qq_push_sent(qq_results):
                _mark_failed(resolved_cfg, uid, scene, now)
                logger.warning(
                    "proactive: qq_push did not confirm delivery",
                    job=job.name,
                    user=uid,
                    exit_reason=exit_reason,
                    result=_snippet(qq_results[-1]),
                )
                result["skipped"].append({"user": uid, "reason": "push_failed", "detail": _snippet(qq_results[-1])})
                continue
            _mark_sent(resolved_cfg, uid, scene, now)
            result["sent"] += 1
            logger.info(
                "proactive: pushed",
                job=job.name,
                user=uid,
                scene=scene,
                reply=_snippet(reply),
            )
        except Exception as exc:  # noqa: BLE001 — one user's failure never blocks the rest
            logger.error(
                "proactive job failed for user",
                job=job.name,
                user=uid,
                error_type=type(exc).__name__,
                stack_trace=str(exc),
            )
            result["errors"].append({"user": uid, "error": f"{type(exc).__name__}: {exc}"})

    return result


__all__ = (
    "PROACTIVE_POOL",
    "build_proactive_prompt",
    "cooldown_ok",
    "in_window",
    "jitter_allows",
    "job_guard_allows",
    "load_guard_config",
    "load_state",
    "parse_window",
    "proactive_due",
    "recall_topic",
    "record_last_active",
    "run_proactive_job",
    "save_state",
)
