"""作品接口：创建（异步排队）、详情、阶段进度、音频下载、删除。

所有接口都通过 ``Depends(get_song_service)`` 取服务单例，路由本身不碰 DAL。
前端调用点见 frontend/src/services/api.ts；``/v1`` 前缀由 routers/__init__.py
的聚合路由统一添加，本文件只写相对路径。

接口防护（依赖层，见 deps.py）：
* router 级 ``reject_oversized_body``  -> 413；
* 读接口 ``enforce_read_policy``      -> 429；
* 创建 ``enforce_generation_policy``  -> 429（请求频率）；
* 创建另有 SongService 的幂等去重    -> 命中时返回已有作品 + ``X-Deduplicated``；
* 活跃任务闸门在 SongService 的去重之后 -> 真新建超限时 429，不影响幂等重试；
* 每日配额（``MUSIC_STUDIO_DAILY_LIMIT``）同样在去重之后 -> 用完时 429 +
  ``Retry-After``（到明日重置），并带 ``X-Daily-Remaining`` 等额度头。
"""

from __future__ import annotations

import asyncio
import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from ..deps import SongId, client_key, enforce_generation_policy, enforce_read_policy, reject_oversized_body
from ..models import GenerationCreate, GenerationDetailResponse, GenerationResponse, StageResponse
from ..services.runtime import get_song_service
from ..services.song_service import DailyQuotaExceeded, SongService, TooManyActiveJobs

router = APIRouter(
    prefix="/generations",
    tags=["generations"],
    dependencies=[Depends(reject_oversized_body)],
)


def _safe_filename(title: str, fallback: str) -> str:
    """把歌曲标题清洗成安全的下载文件名（保留中英文、数字、空格与 _-）。

    Content-Disposition 的文件名不能带路径分隔符或控制字符，故过滤掉其余
    符号并截断到 64 字；清洗后为空时退回 song_id。
    """
    base = re.sub(r"[^\w\u4e00-\u9fff _-]+", "", title, flags=re.UNICODE).strip()
    return (base[:64] or fallback).strip()


@router.post("", response_model=GenerationResponse, dependencies=[Depends(enforce_generation_policy)])
async def create_generation(
    payload: GenerationCreate,
    request: Request,
    response: Response,
    force: bool = Query(default=False, description="跳过幂等去重，强制新建一个生成任务"),
    svc: SongService = Depends(get_song_service),
) -> GenerationResponse:
    """立即返回 queued 记录；五阶段流程由后台 asyncio 任务执行。

    幂等规则：相同请求内容（或相同 ``Idempotency-Key``）在活跃任务期间 /
    去重窗口内重复提交，直接返回已有作品并带上 ``X-Deduplicated: true``，
    不重复计费、不重复生成；``force=true`` 可显式要求再生成一次。

    配额：``MUSIC_STUDIO_DAILY_LIMIT``（默认 50 首/天，0 不限）用完时返回
    429 + ``Retry-After``（到明日重置的秒数），detail 是给用户看的中文说明；
    未超额时用 ``X-Daily-Remaining`` / ``X-Daily-Reset`` 把剩余量告诉前端。

    不要把这里改成同步等待：真实生成可能耗时数十分钟，前端会超时。
    前端拿到记录后靠轮询 /v1/history 观察状态变化。
    """
    try:
        result = await svc.create(
            payload,
            client_key=client_key(request),
            idempotency_key=request.headers.get("idempotency-key"),
            force=force,
        )
    except TooManyActiveJobs as exc:
        # 并发闸门在 service 的幂等判断之后，因此走到这里一定是「真新建」。
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"}) from exc
    except DailyQuotaExceeded as exc:
        # 每日配额是「今天不能再生成」，退避时间给到明日重置，别让客户端反复重试。
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.quota.retry_after), **exc.quota.headers()},
        ) from exc
    if result.deduplicated:
        response.headers["X-Deduplicated"] = "true"
    # 正常创建 / 幂等复用都把剩余额度回给前端，用于在创作面板显示「今日还剩 X 首」
    for key, value in (result.quota.headers() if result.quota else {}).items():
        response.headers[key] = value
    return result.record


@router.get("/{song_id}", response_model=GenerationDetailResponse, dependencies=[Depends(enforce_read_policy)])
async def get_generation(
    song_id: SongId,
    svc: SongService = Depends(get_song_service),
) -> GenerationDetailResponse:
    """作品详情（含五阶段进度）；不存在返回 404。"""
    detail = await svc.get_detail(song_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    return detail


@router.get("/{song_id}/stages", response_model=list[StageResponse], dependencies=[Depends(enforce_read_policy)])
async def get_stages(
    song_id: SongId,
    svc: SongService = Depends(get_song_service),
) -> list[StageResponse]:
    """只取阶段进度，供前端高频轮询进度条时使用（响应更小）。"""
    detail = await svc.get_detail(song_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    return detail.stages


@router.get("/{song_id}/audio", dependencies=[Depends(enforce_read_policy)])
async def download_audio(
    song_id: SongId,
    svc: SongService = Depends(get_song_service),
) -> FileResponse:
    """以附件形式返回本地音频；未生成或文件丢失返回 404。

    两种 404 要区分：作品没有 audio_path 说明流程未完成，已有路径但文件
    不在说明输出目录被清理过（可考虑重新生成）。
    media_type 由扩展名推断，避免浏览器把 wav/mp3 当成二进制下载后无法播放。
    """
    record = await svc.get(song_id)
    if record is None or not record.audio_path:
        raise HTTPException(status_code=404, detail="音频尚未生成")
    path = Path(record.audio_path)
    if not await asyncio.to_thread(path.is_file):
        raise HTTPException(status_code=404, detail="音频文件不存在")
    suffix = path.suffix or ".wav"
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    filename = _safe_filename(record.title or "song", song_id) + suffix
    return FileResponse(path, media_type=media_type, filename=filename)


# @router.delete("/{song_id}", dependencies=[Depends(enforce_read_policy)],)
async def delete_generation(
    song_id: SongId,
    svc: SongService = Depends(get_song_service),
) -> dict[str, str]:
    """删除作品及其产物目录；阶段记录由数据库外键级联删除。"""
    if not await svc.delete(song_id):
        raise HTTPException(status_code=404, detail="作品不存在")
    return {"status": "deleted"}
