"""P2 tests: emotion-aware concern + closing strategy (no second chase).

Covers the lightweight rule-based emotion classifier, persisting the emotion tag to
proactive_state.json from the QQ message path, the open-question / concern dedupe
("no second chase"), the emotion hint injected into the proactive prompt, and the
end-to-end run_proactive_job wiring for both dedupe paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gacore import proactive
from gacore.config import Config
from gacore.scheduler import Job

_TZ = timezone(timedelta(hours=8))


def _dt(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=_TZ)


def _seed_entry(cfg: Config, user_id: str, entry: dict) -> None:
    def _mutate(state: dict) -> None:
        state[f"u_{user_id}"] = entry

    proactive._with_state(cfg, _mutate)


# --------------------------------------------------------------------------- classify_emotion


class TestClassifyEmotion:
    def test_empty_and_blank_are_normal(self) -> None:
        assert proactive.classify_emotion("") == "normal"
        assert proactive.classify_emotion("   ") == "normal"
        assert proactive.classify_emotion(None) == "normal"

    def test_plain_chitchat_is_normal(self) -> None:
        assert proactive.classify_emotion("今天天气不错，晚上一起吃饭吗") == "normal"

    def test_down_keywords(self) -> None:
        # Audit L-3/L-4: ambiguous words ("烦" / "怎么办" / "无聊" / "失败") were dropped;
        # strong emotion words retained, and "低落" added to match the design dictionary.
        for text in ("我好难过", "最近压力好大", "好烦躁", "有点焦虑", "心情低落", "心情不好"):
            assert proactive.classify_emotion(text) == "down", text

    def test_ambiguous_words_no_longer_down(self) -> None:
        # L-3: neutral-context substrings must not tag everyday chat as down.
        for text in ("这个 bug 怎么办", "项目失败了，继续加油", "有点无聊，出去走走", "今天有点烦，不想加班"):
            assert proactive.classify_emotion(text) == "normal", text

    def test_tired_keywords(self) -> None:
        for text in ("今天好累啊", "加班好累", "好困，没精神"):
            assert proactive.classify_emotion(text) == "tired", text

    def test_down_wins_over_tired(self) -> None:
        # "崩溃" is a down signal; even mixed with tired words it must stay down.
        assert proactive.classify_emotion("累到崩溃") == "down"


# --------------------------------------------------------------------------- record_user_emotion


class TestRecordUserEmotion:
    def test_writes_emotion_and_timestamp(self, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        proactive.record_user_emotion("u1", "最近好累啊", cfg=cfg, now=_dt(2026, 8, 28, 22, 10))
        state = proactive.load_state(cfg)
        entry = state["u_u1"]
        assert entry["emotion"] == "tired"
        assert entry["emotion_updated_at"] == "2026-08-28T22:10:00+08:00"
        assert entry["updated_at"] == "2026-08-28T22:10:00+08:00"

    def test_updates_existing_entry(self, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        _seed_entry(cfg, "u1", {"emotion": "normal", "created_at": "2026-08-01T00:00:00+08:00"})
        proactive.record_user_emotion("u1", "好烦躁", cfg=cfg, now=_dt(2026, 8, 28, 9))
        entry = proactive.load_state(cfg)["u_u1"]
        assert entry["emotion"] == "down"
        assert entry["created_at"] == "2026-08-01T00:00:00+08:00"  # preserved

    def test_unknown_user_is_ignored(self, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        proactive.record_user_emotion("unknown", "好累", cfg=cfg)  # must not raise
        assert proactive.load_state(cfg) == {}

    def test_never_raises_on_bad_content(self, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        proactive.record_user_emotion("u1", None, cfg=cfg)  # must not raise
        assert proactive.load_state(cfg)["u_u1"]["emotion"] == "normal"


# --------------------------------------------------------------------------- no second chase: open question


class TestTopicNoSecondChase:
    def test_fingerprint_normalises_whitespace_and_truncates(self) -> None:
        a = proactive._topic_fingerprint("上次那个方案改好了吗？ 你  觉得  呢")
        b = proactive._topic_fingerprint("上次那个方案改好了吗？你觉得呢")
        assert a == b
        assert len(proactive._topic_fingerprint("x" * 100)) == 40

    def test_answered_flags(self, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        now = _dt(2026, 8, 28, 8)
        proactive._mark_topic_answered(cfg, "u1", "上次那个方案改好了吗？", now)
        entry = proactive.load_state(cfg)["u_u1"]
        assert proactive._topic_answered(entry, "上次那个方案改好了吗？")
        assert proactive._topic_answered(entry, " 上次那个方案 改好了吗？")  # whitespace-insensitive
        assert not proactive._topic_answered(entry, "完全不同的另一个话题")
        assert not proactive._topic_answered(entry, "")

    def test_empty_text_never_marks(self, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        proactive._mark_topic_answered(cfg, "u1", "", _dt(2026, 8, 28, 8))
        assert proactive.load_state(cfg) == {}

    def test_answered_topics_lru_trimmed(self, tmp_path: Path) -> None:
        # L-5: answered_topics is bounded by _ANSWERED_TOPICS_MAX — when 60 topics are
        # marked with increasing timestamps, the 10 oldest are trimmed LRU-style.
        cfg = Config.for_tests(tmp_path)
        now = _dt(2026, 8, 28, 8)
        for i in range(60):
            proactive._mark_topic_answered(cfg, "u1", f"问题{i}", now + timedelta(minutes=i))
        entry = proactive.load_state(cfg)["u_u1"]
        answered = entry["answered_topics"]
        assert len(answered) == proactive._ANSWERED_TOPICS_MAX
        assert not proactive._topic_answered(entry, "问题0")   # oldest, trimmed
        assert not proactive._topic_answered(entry, "问题9")   # still trimmed
        assert proactive._topic_answered(entry, "问题10")      # kept
        assert proactive._topic_answered(entry, "问题59")      # newest, kept


# --------------------------------------------------------------------------- no second chase: concern cooldown


class TestConcernCooldown:
    def test_normal_emotion_never_due(self) -> None:
        assert not proactive._concern_due({"emotion": "normal"}, _dt(2026, 8, 28, 8))

    def test_down_without_last_concern_is_due(self) -> None:
        assert proactive._concern_due({"emotion": "down"}, _dt(2026, 8, 28, 8))

    def test_recent_concern_is_not_due(self) -> None:
        now = _dt(2026, 8, 28, 8)
        entry = {
            "emotion": "down",
            "last_concern": "2026-08-28T07:30:00+08:00",
            "last_concern_emotion": "down",  # M-2: same emotion inside cooldown
        }
        assert not proactive._concern_due(entry, now)

    def test_concern_older_than_cooldown_is_due_again(self) -> None:
        now = _dt(2026, 8, 28, 8)
        entry = {"emotion": "down", "last_concern": "2026-08-27T07:30:00+08:00"}  # ~24.5h ago
        assert proactive._concern_due(entry, now)

    def test_stale_or_malformed_timestamp_is_due(self) -> None:
        assert proactive._concern_due({"emotion": "tired", "last_concern": "not-a-date"}, _dt(2026, 8, 28, 8))

    def test_mark_concerned_persists(self, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        _seed_entry(cfg, "u1", {"emotion": "down", "created_at": "2026-08-01T00:00:00+08:00"})
        proactive._mark_concerned(cfg, "u1", _dt(2026, 8, 28, 8), "down")
        entry = proactive.load_state(cfg)["u_u1"]
        assert entry["last_concern"] == "2026-08-28T08:00:00+08:00"
        assert entry["last_concern_emotion"] == "down"  # M-2: emotion stored at delivery time
        assert entry["created_at"] == "2026-08-01T00:00:00+08:00"

    def test_emotion_change_resets_cooldown(self) -> None:
        # M-2: a recent concern for "down" must not block a NEW "tired" concern —
        # the changed emotion is a fresh concern point that resets the cooldown clock.
        now = _dt(2026, 8, 28, 8)
        entry = {
            "emotion": "tired",
            "last_concern": "2026-08-28T07:30:00+08:00",  # 30 min ago — inside cooldown
            "last_concern_emotion": "down",              # but it was for a different emotion
        }
        assert proactive._concern_due(entry, now)

    def test_same_emotion_still_respected_within_cooldown(self) -> None:
        # M-2: same emotion inside the cooldown stays not-due (the "no second chase" rule).
        now = _dt(2026, 8, 28, 8)
        entry = {
            "emotion": "tired",
            "last_concern": "2026-08-28T07:30:00+08:00",
            "last_concern_emotion": "tired",
        }
        assert not proactive._concern_due(entry, now)

    def test_legacy_entry_without_emotion_marker_is_due(self) -> None:
        # M-2: pre-upgrade state files have no last_concern_emotion — fail open (chase once).
        now = _dt(2026, 8, 28, 8)
        entry = {"emotion": "down", "last_concern": "2026-08-28T07:30:00+08:00"}
        assert proactive._concern_due(entry, now)


# --------------------------------------------------------------------------- prompt injection


class TestBuildPromptEmotion:
    def _job(self) -> Job:
        return Job(name="proactive-morning", schedule="every 1h", prompt="早安", type="proactive")

    def test_no_emotion_no_concern_hint(self) -> None:
        prompt = proactive.build_proactive_prompt(self._job(), "u1", _dt(2026, 8, 28, 8))
        assert "主人最近情绪" not in prompt

    def test_unknown_emotion_ignored(self) -> None:
        prompt = proactive.build_proactive_prompt(
            self._job(), "u1", _dt(2026, 8, 28, 8), emotion="bogus"
        )
        assert "主人最近情绪" not in prompt

    def test_down_injects_concern(self) -> None:
        prompt = proactive.build_proactive_prompt(
            self._job(), "u1", _dt(2026, 8, 28, 8), emotion="down"
        )
        assert "主人最近情绪" in prompt
        assert "低落/沮丧" in prompt
        assert "别打探隐私" in prompt

    def test_tired_injects_tired_label(self) -> None:
        prompt = proactive.build_proactive_prompt(
            self._job(), "u1", _dt(2026, 8, 28, 8), emotion="tired"
        )
        assert "疲惫" in prompt

    # ---- M-3: constraint numbering must be continuous (1..N), no gaps / no out-of-order

    def test_constraint_numbers_continuous_plain(self) -> None:
        prompt = proactive.build_proactive_prompt(self._job(), "u1", _dt(2026, 8, 28, 8))
        nums = self._constraint_numbers(prompt)
        assert nums == list(range(1, len(nums) + 1))
        assert len(nums) == 4  # base 3 + closing "proactive greeting" note

    def test_constraint_numbers_continuous_emotion_only(self) -> None:
        prompt = proactive.build_proactive_prompt(
            self._job(), "u1", _dt(2026, 8, 28, 8), emotion="down"
        )
        nums = self._constraint_numbers(prompt)
        assert nums == list(range(1, len(nums) + 1))
        assert len(nums) == 5

    def test_constraint_numbers_continuous_topic_and_emotion(self) -> None:
        # Worst case: both optional constraints present — numbering must stay 1..6.
        prompt = proactive.build_proactive_prompt(
            self._job(), "u1", _dt(2026, 8, 28, 8), topic="上次方案", emotion="down"
        )
        nums = self._constraint_numbers(prompt)
        assert nums == list(range(1, len(nums) + 1))
        assert len(nums) == 6

    @staticmethod
    def _constraint_numbers(prompt: str) -> list[int]:
        block = prompt.split("约束：", 1)[1].strip()
        nums: list[int] = []
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            head = line.split(".", 1)[0]
            if head.isdigit():
                nums.append(int(head))
        return nums


# --------------------------------------------------------------------------- run_proactive_job wiring


def _sent_headless(captured: list[str]) -> object:
    def fake(job: Job, cfg: Config, prompt: str, max_turns: int) -> tuple:
        captured.append(prompt)
        return ("CURRENT_TASK_DONE", "回复", ['{"status": "sent", "ok": 1}'])

    return fake


class TestRunProactiveJobP2:
    def test_concern_not_due_still_greets_normally(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # M-1: the concern cooldown gates ONLY the concern injection, never the whole
        # outreach — a down user inside the cooldown still receives the ordinary
        # greeting (attempted/sent), it just carries no second concern hint.
        cfg = Config.for_tests(tmp_path)
        _seed_entry(
            cfg,
            "u1",
            {
                "emotion": "down",
                "last_concern": "2026-08-28T07:30:00+08:00",
                "last_concern_emotion": "down",  # M-2: same emotion inside cooldown
            },
        )
        monkeypatch.setattr(proactive, "load_known_users", lambda: {"u1": {}})
        prompts: list[str] = []
        monkeypatch.setattr(proactive, "_headless_run", _sent_headless(prompts))
        monkeypatch.setattr(proactive, "recall_topic", lambda cfg_, uid, now: {"kind": "none", "text": "", "thread_id": ""})
        job = Job(name="proactive-morning", schedule="every 1h", prompt="早安", type="proactive")
        result = proactive.run_proactive_job(job, cfg=cfg, clock=lambda: _dt(2026, 8, 28, 8))
        assert result["attempted"] == 1
        assert result["sent"] == 1
        assert "主人最近情绪" not in prompts[0]

    def test_concern_injected_and_marked_when_due(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        _seed_entry(cfg, "u1", {"emotion": "down"})
        monkeypatch.setattr(proactive, "load_known_users", lambda: {"u1": {}})
        prompts: list[str] = []
        monkeypatch.setattr(proactive, "_headless_run", _sent_headless(prompts))
        monkeypatch.setattr(proactive, "recall_topic", lambda cfg_, uid, now: {"kind": "none", "text": "", "thread_id": ""})
        job = Job(name="proactive-morning", schedule="every 1h", prompt="早安", type="proactive")
        result = proactive.run_proactive_job(job, cfg=cfg, clock=lambda: _dt(2026, 8, 28, 8))
        assert result["sent"] == 1
        assert "主人最近情绪" in prompts[0]
        state = proactive.load_state(cfg)["u_u1"]
        assert state["last_concern"] == "2026-08-28T08:00:00+08:00"
        assert state["last_concern_emotion"] == "down"  # M-2: delivered concern records its emotion

    def test_open_question_not_chased_twice(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        _seed_entry(cfg, "u1", {"answered_topics": {proactive._topic_fingerprint("上次那个方案改好了吗"): "2026-08-27T08:00:00+08:00"}})
        monkeypatch.setattr(proactive, "load_known_users", lambda: {"u1": {}})
        prompts: list[str] = []
        monkeypatch.setattr(proactive, "_headless_run", _sent_headless(prompts))
        monkeypatch.setattr(
            proactive,
            "recall_topic",
            lambda cfg_, uid, now: {"kind": "open_question", "text": "上次那个方案改好了吗", "thread_id": "t1"},
        )
        job = Job(name="proactive-morning", schedule="every 1h", prompt="早安", type="proactive")
        result = proactive.run_proactive_job(job, cfg=cfg, clock=lambda: _dt(2026, 8, 28, 8))
        assert result["sent"] == 1  # still delivers a plain greeting
        assert "最近话题" not in prompts[0]
        assert "上次那个方案改好了吗" not in prompts[0]

    def test_fresh_open_question_is_injected_and_then_marked(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cfg = Config.for_tests(tmp_path)
        monkeypatch.setattr(proactive, "load_known_users", lambda: {"u1": {}})
        prompts: list[str] = []
        monkeypatch.setattr(proactive, "_headless_run", _sent_headless(prompts))
        monkeypatch.setattr(
            proactive,
            "recall_topic",
            lambda cfg_, uid, now: {"kind": "open_question", "text": "上次那个方案改好了吗", "thread_id": "t1"},
        )
        job = Job(name="proactive-morning", schedule="every 1h", prompt="早安", type="proactive")
        result = proactive.run_proactive_job(job, cfg=cfg, clock=lambda: _dt(2026, 8, 28, 8))
        assert result["sent"] == 1
        assert "最近话题" in prompts[0]
        state = proactive.load_state(cfg)["u_u1"]
        assert proactive._topic_answered(state, "上次那个方案改好了吗")
