"""dal 层：任务产物（阶段 JSON / 文本 / 音频）的文件存取。

原子写入（临时文件 + replace）避免进程中断留下半截 JSON；
阶段产物存在即可跳过重跑，这是断点续跑的依据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..constants import FINAL_AUDIO_NAME


class ArtifactStore:
    def __init__(self, task_dir: Path, *, resume: bool = False) -> None:
        self.task_dir = Path(task_dir)
        if resume:
            if not self.task_dir.is_dir():
                raise FileNotFoundError(f"任务目录不存在：{self.task_dir}")
        else:
            self.task_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, filename: str) -> Path:
        path = self.task_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def save_text(self, content: str, filename: str) -> Path:
        path = self._path(filename)
        path.write_text(content, encoding="utf-8")
        return path

    def load_text(self, filename: str) -> str:
        return self._path(filename).read_text(encoding="utf-8")

    def save_json(self, data: Any, filename: str) -> Path:
        path = self._path(filename)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return path

    def load_json(self, filename: str) -> Any:
        path = self._path(filename)
        if not path.exists():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def exists(self, filename: str) -> bool:
        return (self.task_dir / filename).is_file()

    def audio_path(self, suffix: str = ".wav") -> Path:
        return self._path(f"{FINAL_AUDIO_NAME}{suffix}")
