"""SQLAlchemy ORM model for a generation (song) record.

The upstream repository shipped this as ``app/models/generation.py`` but the
root ``.gitignore`` contained a broad ``models/`` rule, so the whole package
was left out of the clone. The ignore rule is now scoped to ``/models/`` and
this file restores the original package layout.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_type: Mapped[str] = mapped_column(String(32), default="text2music", nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="simple", nullable=False)
    model_variant: Mapped[str] = mapped_column(String(16), default="turbo", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    lyrics: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrumental: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    time_signature: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cover_strength: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    output_audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cover_icon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cover_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
