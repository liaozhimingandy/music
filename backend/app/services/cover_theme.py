"""service 层：根据作品 ID 稳定推导封面配色与图标（前端据此画渐变封面）。

设计为纯函数 + 确定性哈希：同一个 ID 无论刷新多少次、在哪台机器上，
都得到相同封面，不需要在数据库里额外存一列。首色 #EC4141 与前端
网易云红主题保持一致。
"""

from __future__ import annotations

import hashlib

# 前端 icon 名称，与 src/common/utils.ts 的图标映射一一对应；
# 增删条目会影响既有作品的封面，改动前请确认前端支持。
ICON_CHOICES: tuple[str, ...] = (
    "guitar",
    "music",
    "activity",
    "keyboard-music",
    "audio-waveform",
    "music-2",
    "music-4",
    "mic-vocal",
)

COLOR_CHOICES: tuple[str, ...] = (
    "#EC4141",
    "#F7776B",
    "#F5A623",
    "#E8B44A",
    "#4CAF9A",
    "#5B8FF9",
    "#8B7CF6",
    "#D96FB8",
)


def compute_theme(seed: str) -> tuple[str, str]:
    """由种子（通常是 song_id）推导 ``(封面主色, 图标名)``。

    取 md5 前 8 位选颜色、第 9~16 位选图标，两段互不干扰。
    md5 只用于取色，不涉及任何安全用途，故关闭对应的 lint 告警。
    """
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()  # noqa: S324 - 仅用于配色，非安全用途
    color = COLOR_CHOICES[int(digest[:8], 16) % len(COLOR_CHOICES)]
    icon = ICON_CHOICES[int(digest[8:16], 16) % len(ICON_CHOICES)]
    return color, icon
