"""service 层：根据作品 ID 稳定推导封面配色与图标（前端据此画渐变封面）。"""

from __future__ import annotations

import hashlib

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
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()  # noqa: S324 - 仅用于配色，非安全用途
    color = COLOR_CHOICES[int(digest[:8], 16) % len(COLOR_CHOICES)]
    icon = ICON_CHOICES[int(digest[8:16], 16) % len(ICON_CHOICES)]
    return color, icon
