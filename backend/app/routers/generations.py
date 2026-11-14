"""作品接口：创建（异步排队）、详情、阶段进度、音频下载、删除。

所有接口都通过 ``runtime.get_song_service()`` 取单例，路由本身不碰 DAL。
前端调用点见 frontend/src/services/api.ts。
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..models import GenerationCreate, GenerationDetailResponse, GenerationResponse, StageResponse
from ..services.runtime import get_song_service

router = APIRouter(prefix="/api/generations", tags=["generations"])


def _safe_filename(title: str, fallback: str) -> str:
    """把歌曲标题清洗成安全的下载文件名（保留中英文、数字、空格与 _-）。

    Content-Disposition 的文件名不能带路径分隔符或控制字符，故过滤掉其余
    符号并截断到 64 字；清洗后为空时退回 song_id。
    """
    base = re.sub(r"[^\w\u4e00-\u9fff _-]+", "", title, flags=re.UNICODE).strip()
    return (base[:64] or fallback).strip()


@router.post("", response_model=GenerationResponse)
def create_generation(payload: GenerationCreate) -> GenerationResponse:
    """立即返回 queued 记录；五阶段流程在后台线程执行。

    不要把这里改成同步等待：真实生成可能耗时数十分钟，前端会超时。
    前端拿到记录后靠轮询 /api/history 观察状态变化。
    """
    return get_song_service().create(payload)


@router.get("/{song_id}", response_model=GenerationDetailResponse)
def get_generation(song_id: str) -> GenerationDetailResponse:
    """作品详情（含五阶段进度）；不存在返回 404。"""
    detail = get_song_service().get_detail(song_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    return detail


@router.get("/{song_id}/stages", response_model=list[StageResponse])
def get_stages(song_id: str) -> list[StageResponse]:
    """只取阶段进度，供前端高频轮询进度条时使用（响应更小）。"""
    detail = get_song_service().get_detail(song_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    return detail.stages


@router.get("/{song_id}/audio")
def download_audio(song_id: str) -> FileResponse:
    """以附件形式返回本地音频；未生成或文件丢失返回 404。

    两种 404 要区分：作品没有 audio_path 说明流程未完成，已有路径但文件
    不在说明输出目录被清理过（可考虑重新生成）。
    media_type 由扩展名推断，避免浏览器把 wav/mp3 当成二进制下载后无法播放。
    """
    record = get_song_service().get(song_id)
    if record is None or not record.audio_path:
        raise HTTPException(status_code=404, detail="音频尚未生成")
    path = Path(record.audio_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    suffix = path.suffix or ".wav"
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    filename = _safe_filename(record.title or "song", song_id) + suffix
    return FileResponse(path, media_type=media_type, filename=filename)


@router.delete("/{song_id}")
def delete_generation(song_id: str) -> dict[str, str]:
    """删除作品及其产物目录；阶段记录由数据库外键级联删除。"""
    if not get_song_service().delete(song_id):
        raise HTTPException(status_code=404, detail="作品不存在")
    return {"status": "deleted"}
