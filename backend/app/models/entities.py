"""model 层：领域实体（与数据库行一一对应，不含任何 IO）。

数据流向：``sqlite3.Row`` --from_row--> 实体 --Service 加工--> --to_row--> 数据库字段。
这里刻意用 dataclass 而非 ORM：只有两张表，避免为简单 CRUD 引入 SQLAlchemy。
注意实体里的 ``instrumental`` 是 bool、``metadata`` 是 dict，入库前需做转换。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any


def _bool(value: Any) -> bool:
    """把 SQLite 的 0/1 / None 统一转成 Python bool。"""
    return bool(value)


@dataclass
class SongRecord:
    """一条作品记录。字段名与 songs 表列同名，除 ``metadata`` 对应 ``metadata_json``。

    状态机：新建时为 ``queued``；后台线程接手后改 ``processing``；
    五阶段全部完成改 ``ready``；任一阶段异常改 ``failed`` 并写 ``error_message``。
    """

    id: str
    # --- 基础展示信息 ---
    title: str | None = None
    task_type: str = "text2music"
    mode: str = "simple"
    model_variant: str = "turbo"
    status: str = "queued"
    current_stage: str | None = None  # 正在执行/最后完成的阶段名，供前端进度文案使用
    # --- 创作输入与产出 ---
    prompt: str | None = None
    lyrics: str | None = None
    style_prompt: str | None = None
    instrumental: bool = False
    duration_seconds: int | None = None
    bpm: int | None = None
    musical_key: str | None = None  # 出参映射为前端的 key
    time_signature: str | None = None
    # --- 音频与封面 ---
    audio_url: str | None = None     # 相对 API 路径 /api/generations/{id}/audio
    audio_path: str | None = None   # 服务器本地绝对路径，不返回给前端
    cover_color: str | None = None
    cover_icon: str | None = None
    cover_image_url: str | None = None
    # --- 运行信息与扩展 ---
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    provider_task_id: str | None = None
    source_prompt: str | None = None  # 用户最初输入，五个阶段共用
    input_fingerprint: str | None = None  # 请求指纹；幂等去重依据，不返回给前端
    client_key: str | None = None         # 客户端 IP 哈希；单客户端限流依据，不返回给前端
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SongRecord":
        """把数据库行还原成实体：0/1 -> bool、metadata_json -> dict。

        ``metadata_json`` 允许为空或历史脏数据，这里按空字典兜底，避免
        一条坏记录让整个作品库接口 500。
        """
        raw = dict(row)
        raw["instrumental"] = _bool(raw.get("instrumental"))
        raw["metadata"] = json.loads(raw.pop("metadata_json", None) or "{}")
        return cls(**raw)

    def to_row(self) -> dict[str, Any]:
        """把实体转成可直接用于 SQL 参数绑定的字典（列名 -> 值）。

        与 ``from_row`` 互逆：bool 转 0/1、metadata 序列化为 metadata_json，
        并从结果里移除实体专有的 ``metadata`` 键。
        """
        data = asdict(self)
        data["instrumental"] = int(self.instrumental)
        data["metadata_json"] = json.dumps(self.metadata, ensure_ascii=False)
        del data["metadata"]
        return data


@dataclass
class StageRecord:
    """某一首歌某个阶段的执行记录；``output_file`` 存在即可视为产物已落盘。"""

    song_id: str
    stage: str
    status: str = "pending"
    output_file: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    id: int | None = None  # 自增主键，仅用于按插入顺序排序展示

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "StageRecord":
        """字段同名，直接展开即可；列清单见 models/db_schema.STAGE_COLUMNS。"""
        return cls(**dict(row))
