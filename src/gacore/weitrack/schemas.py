"""上报请求的 Pydantic 模型。只校验外层结构；data 内部存原文不深校验。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Event(BaseModel):
    type: Literal["usage", "session"]
    ts: int
    data: dict


class IngestRequest(BaseModel):
    device_id: str
    batch_id: str
    client_ts: int
    events: list[Event]
