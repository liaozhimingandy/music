"""model 层：领域实体（与数据库行一一对应，不含任何 IO）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any


def _bool(value: Any) -> bool:
    return bool(value)


@dataclass
class SongRecord:
    id: str
    title: str | None = None
    task_type: str = "text2music"
    mode: str = "simple"
    model_variant: str = "turbo"
    status: str = "queued"
    current_stage: str | None = None
    prompt: str | None = None
    lyrics: str | None = None
    style_prompt: str | None = None
    instrumental: bool = False
    duration_seconds: int | None = None
    bpm: int | None = None
    musical_key: str | None = None
    time_signature: str | None = None
    audio_url: str | None = None
    audio_path: str | None = None
    cover_color: str | None = None
    cover_icon: str | None = None
    cover_image_url: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    provider_task_id: str | None = None
    source_prompt: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SongRecord":
        raw = dict(row)
        raw["instrumental"] = _bool(raw.get("instrumental"))
        raw["metadata"] = json.loads(raw.pop("metadata_json", None) or "{}")
        return cls(**raw)

    def to_row(self) -> dict[str, Any]:
        data = asdict(self)
        data["instrumental"] = int(self.instrumental)
        data["metadata_json"] = json.dumps(self.metadata, ensure_ascii=False)
        del data["metadata"]
        return data


@dataclass
class StageRecord:
    song_id: str
    stage: str
    status: str = "pending"
    output_file: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "StageRecord":
        return cls(**dict(row))
