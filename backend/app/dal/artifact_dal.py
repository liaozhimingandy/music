"""dal 层：任务产物（阶段 JSON / 文本 / 音频）的文件存取。

原子写入（临时文件 + replace）避免进程中断留下半截 JSON；
阶段产物存在即可跳过重跑，这是断点续跑的依据。

目录约定：``data/outputs/<song_id>/``，文件名由 constants.STAGE_OUTPUTS 定义。
该目录已被 .gitignore 忽略，属于运行产物而非源码。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..constants import FINAL_AUDIO_NAME


class ArtifactStore:
    """单个任务目录下的产物读写器。

    Args:
        task_dir: 该作品的输出目录。
        resume: 为 True 时要求目录必须已存在（续跑场景），否则新建。
    """

    def __init__(self, task_dir: Path, *, resume: bool = False) -> None:
        self.task_dir = Path(task_dir)
        if resume:
            # 续跑时若目录丢失，说明产物已不在，直接失败比静默重建更安全。
            if not self.task_dir.is_dir():
                raise FileNotFoundError(f"任务目录不存在：{self.task_dir}")
        else:
            self.task_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, filename: str) -> Path:
        """拼出产物绝对路径，并确保父目录存在（允许文件名带子目录）。"""
        path = self.task_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def save_text(self, content: str, filename: str) -> Path:
        """写入 UTF-8 文本，返回落盘路径。"""
        path = self._path(filename)
        path.write_text(content, encoding="utf-8")
        return path

    def load_text(self, filename: str) -> str:
        """读取 UTF-8 文本；文件不存在时由 Path 抛出 FileNotFoundError。"""
        return self._path(filename).read_text(encoding="utf-8")

    def save_json(self, data: Any, filename: str) -> Path:
        """原子写入 JSON：先写 .tmp 再 replace，避免中断产生半截文件。

        这是断点续跑能信任产物文件的前提——只要文件存在，内容必然完整。
        """
        path = self._path(filename)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return path

    def load_json(self, filename: str) -> Any:
        """读取并解析阶段 JSON；缺失时明确报错，由调用方决定是否重跑。"""
        path = self._path(filename)
        if not path.exists():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def exists(self, filename: str) -> bool:
        """产物是否已落盘——WorkflowService 用它判断该阶段能否跳过。"""
        return (self.task_dir / filename).is_file()

    def audio_path(self, suffix: str = ".wav") -> Path:
        """最终音频的目标路径，文件名由 constants.FINAL_AUDIO_NAME 统一定义。"""
        return self._path(f"{FINAL_AUDIO_NAME}{suffix}")
