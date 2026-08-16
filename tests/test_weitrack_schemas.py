from __future__ import annotations

import pytest
from pydantic import ValidationError

from gacore.weitrack.schemas import IngestRequest


def test_valid_request():
    req = IngestRequest(
        device_id="dev1",
        batch_id="b1",
        client_ts=1000,
        events=[
            {"type": "usage", "ts": 1000, "data": {"pkg": "com.x", "foreground_ms": 5}},
            {"type": "session", "ts": 1001, "data": {"kind": "screen_on"}},
        ],
    )
    assert len(req.events) == 2


def test_invalid_type_rejected():
    with pytest.raises(ValidationError):
        IngestRequest(
            device_id="dev1", batch_id="b1", client_ts=1000,
            events=[{"type": "bogus", "ts": 1000, "data": {}}],
        )


def test_missing_ts_rejected():
    with pytest.raises(ValidationError):
        IngestRequest(
            device_id="dev1", batch_id="b1", client_ts=1000,
            events=[{"type": "usage", "data": {"pkg": "com.x"}}],
        )
