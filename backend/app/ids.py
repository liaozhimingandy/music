"""横切工具：全局 ID 生成（UUIDv7，RFC 9562），替代原先的 UUIDv4。

为什么用 v7
-----------
* 前 48 位是毫秒级 Unix 时间戳，**按生成时间有序**；作为 ``songs.id``
  主键写进 SQLite 时 B-tree 局部性好，``ORDER BY id`` 与
  ``ORDER BY created_at`` 天然同序，作品库倒序/分页更稳定。
* 后 74 位仍是随机数，全局唯一性与 v4 相当，且不可被轻易枚举（防爬）。
* ``.hex`` 依然是 32 位小写十六进制，长度和字符集与旧 ID 完全一致：
  数据库列、URL 路径、前端契约、``data/outputs/<id>/`` 目录名都无需改动。

实现说明
--------
Python 3.12 标准库还没有 ``uuid.uuid7``，本项目也不想为一个 ID 引入第三方
依赖，因此按 RFC 9562 的位布局自行实现：

    0                   1                   2                   3
    | unix_ts_ms (48b) | ver(4b) | rand_a(12b) | var(2b) | rand_b(62b) |

同一毫秒内把 74 位随机部分当计数器自增，并用模块级 ``threading.Lock``
保护，保证多任务（含 ``asyncio.to_thread`` 里的线程）并发创建时严格单调、
不会碰撞。时间戳也永不倒退：系统时钟回拨时沿用上一次的毫秒值。

本模块不依赖 dal / services / routers，任何层都可以只读引用。
"""

from __future__ import annotations

import re
import secrets
import threading
import time
import uuid

# 位掩码：拼装 128 位整数时用，避免手抖写错位数。
_MASK_48 = (1 << 48) - 1
_MASK_12 = (1 << 12) - 1
_MASK_62 = (1 << 62) - 1
_MASK_74 = (1 << 74) - 1

# 同毫秒内的递增状态；用线程锁而非 asyncio.Lock，是因为 ID 可能由
# to_thread 里的文件/网络代码生成，跨线程也要安全。
_state_lock = threading.Lock()
_last_ms = -1
_last_rand_a = 0
_last_rand_b = 0

# 路径参数白名单：兼容历史 uuid4 记录（同为 32 位 hex），只校验字符集，
# 不校验版本号，避免升级后旧作品的详情 / 音频接口 404。
_SONG_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def uuid7() -> uuid.UUID:
    """生成一个单调递增的 UUIDv7 对象。

    返回值满足 ``value.version == 7``，且 ``value.hex`` 的字典序与生成
    时间顺序一致。并发安全：同毫秒内递增随机部分，时钟回拨也不会倒序。
    """
    global _last_ms, _last_rand_a, _last_rand_b

    now_ms = time.time_ns() // 1_000_000
    with _state_lock:
        if now_ms > _last_ms:
            # 新毫秒：重新取随机数，时间戳推进。
            stamp_ms = now_ms
            rand_a = secrets.randbits(12)
            rand_b = secrets.randbits(62)
        else:
            # 同一毫秒（或时钟回拨）：把 rand_a|rand_b 整体当 74 位计数器 +1，
            # 既保持时间前缀不变，又保证 UUID 严格递增不重复。
            counter = (((_last_rand_a << 62) | _last_rand_b) + 1) & _MASK_74
            rand_a = (counter >> 62) & _MASK_12
            rand_b = counter & _MASK_62
            stamp_ms = _last_ms
            if counter == 0:
                # 极端情况（同一毫秒用完 2^74 个 ID）：借用下一个毫秒。
                stamp_ms = _last_ms + 1
        _last_ms, _last_rand_a, _last_rand_b = stamp_ms, rand_a, rand_b

    value = (
        ((stamp_ms & _MASK_48) << 80)   # 48 位毫秒时间戳
        | (0x7 << 76)                   # 4 位版本号 = 0111
        | ((rand_a & _MASK_12) << 64)   # 12 位 rand_a
        | (0b10 << 62)                  # 2 位 variant = 10（RFC 4122）
        | (rand_b & _MASK_62)           # 62 位 rand_b
    )
    return uuid.UUID(int=value)


def new_song_id() -> str:
    """返回新作品 ID：UUIDv7 的 32 位小写 hex（与旧 uuid4().hex 完全同形）。"""
    return uuid7().hex


def new_provider_task_id() -> str:
    """离线 mock 音乐任务的短 ID（取 UUIDv7 前 12 位即可，仅用于演示展示）。"""
    return f"mock-{new_song_id()[:12]}"


def is_song_id(value: str) -> bool:
    """校验路径里的作品 ID 是否为合法的 32 位小写 hex。

    这是防止 ``song_id`` 被构造成 ``..``、绝对路径等做目录穿越的第一道
    关卡；SongService.delete 里还有一层 outputs 根目录校验做兜底。
    """
    return bool(_SONG_ID_RE.fullmatch(value))
