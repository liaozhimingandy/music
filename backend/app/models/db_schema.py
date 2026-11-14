"""model 层：SQLite3 表结构定义（DDL 的唯一来源）。

字段按前端 GenerationResponse 需要的数据建模；阶段明细单独一张表，
便于前端轮询时展示「当前进行到哪一步」。
"""

from __future__ import annotations

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS songs (
    id                TEXT PRIMARY KEY,
    title             TEXT,
    task_type         TEXT    NOT NULL DEFAULT 'text2music',
    mode              TEXT    NOT NULL DEFAULT 'simple',
    model_variant     TEXT    NOT NULL DEFAULT 'turbo',
    status            TEXT    NOT NULL DEFAULT 'queued',
    current_stage     TEXT,
    prompt            TEXT,
    lyrics            TEXT,
    style_prompt      TEXT,
    instrumental      INTEGER NOT NULL DEFAULT 0,
    duration_seconds  INTEGER,
    bpm               INTEGER,
    musical_key       TEXT,
    time_signature    TEXT,
    audio_url         TEXT,
    audio_path        TEXT,
    cover_color       TEXT,
    cover_icon        TEXT,
    cover_image_url   TEXT,
    error_message     TEXT,
    metadata_json     TEXT    NOT NULL DEFAULT '{}',
    provider_task_id  TEXT,
    source_prompt     TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_songs_created_at ON songs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_songs_status ON songs(status);

CREATE TABLE IF NOT EXISTS song_stages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id      TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    stage        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    output_file  TEXT,
    error        TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    UNIQUE(song_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_stages_song ON song_stages(song_id);
"""

# 供 DAL 做插入/更新时使用的列清单（与 DDL 保持一致）
SONG_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "task_type",
    "mode",
    "model_variant",
    "status",
    "current_stage",
    "prompt",
    "lyrics",
    "style_prompt",
    "instrumental",
    "duration_seconds",
    "bpm",
    "musical_key",
    "time_signature",
    "audio_url",
    "audio_path",
    "cover_color",
    "cover_icon",
    "cover_image_url",
    "error_message",
    "metadata_json",
    "provider_task_id",
    "source_prompt",
    "created_at",
    "updated_at",
)

STAGE_COLUMNS: tuple[str, ...] = (
    "song_id",
    "stage",
    "status",
    "output_file",
    "error",
    "started_at",
    "finished_at",
)
