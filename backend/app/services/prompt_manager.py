"""service 层：Prompt 模板管理器。

只读取 backend/app/prompts 下的模板，**不修改模板内容**。
沿用原型的 StrictUndefined：模板变量缺失时立即报错，避免静默产出错误提示词。
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..config import settings


class PromptManager:
    def __init__(self, prompt_dir: Path | None = None) -> None:
        self.prompt_dir = Path(prompt_dir or settings.prompts_dir)
        if not self.prompt_dir.is_dir():
            raise FileNotFoundError(f"Prompt 目录不存在：{self.prompt_dir}")
        self.env = Environment(
            loader=FileSystemLoader(str(self.prompt_dir)),
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def load(self, name: str) -> str:
        """读取原始 system prompt（不渲染）。"""
        path = self.prompt_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Prompt 不存在：{path}")
        return path.read_text(encoding="utf-8")

    def render(self, name: str, **variables: object) -> str:
        return self.env.get_template(name).render(**variables).strip()

    def get_stage(self, stage: str, **variables: object) -> dict[str, str]:
        """返回某个阶段的 system / user 两条 prompt。"""
        return {
            "system_prompt": self.load(f"{stage}/system.md"),
            "user_prompt": self.render(f"{stage}/user.md", **variables),
        }
