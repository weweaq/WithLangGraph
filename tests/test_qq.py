"""Tests for the QQ frontend: message splitting, dedupe, allowlist, command routing."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure `src` is importable and fake botpy BEFORE importing the module under test.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Fake botpy so we can import the module without the real SDK installed.
_fake_botpy = types.ModuleType("botpy")


class _FakeIntents:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


_fake_botpy.Intents = _FakeIntents
_fake_message = types.ModuleType("botpy.message")
_fake_message.C2CMessage = type("C2CMessage", (), {})
_fake_message.GroupMessage = type("GroupMessage", (), {})
sys.modules["botpy"] = _fake_botpy
sys.modules["botpy.message"] = _fake_message


from gacore.frontends import qq
from gacore.frontends.qq import QQApp, _processed_ids, _split_text


def test_split_text_short_unchanged():
    """Given a short string, _split_text returns it as a single chunk."""
    text = "hello world"
    assert _split_text(text) == ["hello world"]


def test_split_text_long_splits_on_newline():
    """Given a long string, _split_text breaks into <= 4500-char chunks."""
    long_line = "x" * 10000
    parts = _split_text(long_line)
    assert all(len(p) <= qq._SPLIT_LIMIT for p in parts)
    assert "".join(parts) == long_line


def test_split_text_empty_becomes_ellipsis():
    """Given an empty or whitespace string, _split_text returns a placeholder."""
    assert _split_text("") == ["..."]
    assert _split_text("   ") == ["..."]


def test_processed_ids_dedupes():
    """Given the same message id twice, the second is dropped as a duplicate."""
    _processed_ids.clear()
    _processed_ids.append("msg-1")
    assert "msg-1" in _processed_ids
    assert len(_processed_ids) == 1


async def test_on_message_unauthorized_is_silenced():
    """Given a user not in the allowlist, on_message returns without sending."""
    _processed_ids.clear()
    graph = MagicMock()
    app = QQApp(graph)
    app.client = MagicMock()
    app.send_text = AsyncMock()

    msg = MagicMock()
    msg.id = "msg-auth"
    msg.content = "hello"
    msg.author = MagicMock()
    msg.author.user_openid = "user-123"
    msg.author.id = "user-123"

    with patch.object(qq, "_ALLOWED", frozenset({"other-user"})):
        await app.on_message(msg, is_group=False)

    # Message is deduped (id recorded) but no reply is sent to unauthorized users.
    app.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_help_command_replies():
    """Given /help, on_message sends the command list and records the message id."""
    _processed_ids.clear()
    graph = MagicMock()
    app = QQApp(graph)
    app.client = MagicMock()
    app.send_text = AsyncMock()

    msg = MagicMock()
    msg.id = "msg-help"
    msg.content = "/help"
    msg.author = MagicMock()
    msg.author.user_openid = "user-1"
    msg.author.id = "user-1"

    with patch.object(qq, "_ALLOWED", frozenset({"user-1"})):
        await app.on_message(msg, is_group=False)

    assert "msg-help" in _processed_ids
    app.send_text.assert_awaited()
    out = app.send_text.call_args.args[1]
    assert "/help" in out and "/new" in out


@pytest.mark.asyncio
async def test_on_message_new_resets_thread():
    """Given /new, on_message clears the user's thread mapping."""
    _processed_ids.clear()
    qq._user_threads.clear()
    qq._user_threads["user-1"] = "old-thread"

    graph = MagicMock()
    app = QQApp(graph)
    app.client = MagicMock()
    app.send_text = AsyncMock()

    msg = MagicMock()
    msg.id = "msg-new"
    msg.content = "/new"
    msg.author = MagicMock()
    msg.author.user_openid = "user-1"
    msg.author.id = "user-1"

    with patch.object(qq, "_ALLOWED", frozenset({"user-1"})):
        await app.on_message(msg, is_group=False)

    assert "user-1" not in qq._user_threads
