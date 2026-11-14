"""model 层：SQLite3 表结构定义（DDL 的唯一来源）。

设计约定
--------
* ``songs`` 一行 = 一个生成任务 = 作品库里的一张卡片；字段按前端
  ``GenerationResponse``（frontend/src/interface/index.ts）建模。
* 唯一命名例外是 ``musical_key``：前端字段叫 ``key``，由 SongService 在
  出参时做 ``musical_key -> key`` 映射，避免与 SQL 关键字、Python 属性混淆。
* ``song_stages`` 一个任务对应五行（planning / lyrics / polish / music /
  synthesize），前端轮询时用它显示「当前进行到哪一步」。
* 作品状态只用前端认识的 ``queued / processing / ready / failed``；阶段状态
  用 ``pending / running / completed / failed``，两套枚举见 constants.py。

去重与限流字段
--------------
``input_fingerprint`` 是请求内容的 sha256（或客户端 Idempotency-Key 的哈希），
用于幂等去重；``client_key`` 是客户端 IP 的哈希，用于单客户端活跃任务上限。
两者都是后加的列，故额外提供 ``SONG_TABLE_MIGRATIONS`` 让旧库平滑升级
（``CREATE TABLE IF NOT EXISTS`` 不会改已有表）；依赖这两列的索引放在
``POST_MIGRATION_SQL``，必须等 ALTER 完成后再建，否则旧库启动会失败。

维护提示
--------
新增数据库字段时必须同步改四处：``SCHEMA_SQL``（新库）、
``SONG_TABLE_MIGRATIONS``（旧库）、``SONG_COLUMNS``（DAL 字段白名单）、
``entities.SongRecord``（实体）。漏掉白名单会静默丢数据。
"""

from __future__ import annotations

# 由 dal.database.init_schema() 在应用启动时执行一次；语句全部幂等，
# 重复启动不会破坏已有数据。PRAGMA foreign_keys 必须在建表语句之前执行，
# song_stages 的 ON DELETE CASCADE 才会真正生效。
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
    input_fingerprint TEXT,           -- 请求指纹，幂等去重依据
    client_key        TEXT,           -- 客户端标识哈希，单客户端限流依据
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

# 旧库补列映射（列名 -> 列类型）。init_schema() 会在建表后检查
# PRAGMA table_info(songs)，缺哪列就 ALTER 哪列，保证升级不丢历史作品。
SONG_TABLE_MIGRATIONS: dict[str, str] = {
    "input_fingerprint": "TEXT",
    "client_key": "TEXT",
}

# 依赖新列的索引必须在 ALTER 之后创建，否则旧库（尚无该列）会在启动时报错。
POST_MIGRATION_SQL = """
CREATE INDEX IF NOT EXISTS idx_songs_fingerprint ON songs(input_fingerprint, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_songs_client ON songs(client_key, status);
"""

# DAL 插入 / 更新时使用的列白名单，顺序与 DDL 保持一致。
# 说明：这里不含 song_stages 的自增 id，也不含任何派生字段。
SONG_COLUMNS: tuple[str, ...] = (
    # --- 任务标识与配置 ---
    "id",
    "title",
    "task_type",
    "mode",
    "model_variant",
    # --- 运行状态（后台线程逐阶段回写）---
    "status",
    "current_stage",
    # --- 创作输入 / 生成结果 ---
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
    # --- 前端展示用封面 ---
    "cover_color",
    "cover_icon",
    "cover_image_url",
    # --- 扩展信息与外部任务关联 ---
    "error_message",
    "metadata_json",     # JSON 字符串，实体里对应 metadata: dict
    "provider_task_id",  # ModelScope 侧的任务 ID，用于查询生成状态
    "source_prompt",
    # --- 幂等与接口防护 ---
    "input_fingerprint",  # 请求内容 sha256 / Idempotency-Key 哈希
    "client_key",         # 客户端 IP 哈希（不存明文）
    # --- 时间戳（ISO 8601 UTC 字符串）---
    "created_at",
    "updated_at",
)

# song_stages 的可写列；id 为自增主键，由数据库维护，不在此列。
STAGE_COLUMNS: tuple[str, ...] = (
    "song_id",
    "stage",
    "status",
    "output_file",  # 阶段产物文件名，存在即代表该阶段可跳过（断点续跑）
    "error",
    "started_at",
    "finished_at",
)
