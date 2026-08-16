from __future__ import annotations

from gacore.weitrack.storage import Storage


def _new_storage(tmp_path):
    return Storage(tmp_path / "wei_track.db")


def test_insert_event_and_count(tmp_path):
    s = _new_storage(tmp_path)
    s.upsert_device("dev1", 1000)
    s.insert_event("dev1", 1000, "usage", {"pkg": "com.x", "foreground_ms": 5}, 2000)
    assert s.event_count() == 1
    s.close()


def test_batch_idempotent(tmp_path):
    s = _new_storage(tmp_path)
    assert s.register_batch("b1", "dev1", 1000) is True
    assert s.register_batch("b1", "dev1", 1000) is False  # 重复
    s.close()


def test_duplicate_batch_not_double_insert(tmp_path):
    s = _new_storage(tmp_path)
    s.upsert_device("dev1", 1000)
    assert s.register_batch("b1", "dev1", 2000) is True
    s.insert_event("dev1", 1000, "usage", {"pkg": "com.x"}, 2000)
    assert s.register_batch("b1", "dev1", 2000) is False
    assert s.event_count() == 1  # caller skips insert when register_batch reports duplicate
    s.close()
