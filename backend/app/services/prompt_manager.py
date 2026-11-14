"""service 层：Prompt 模板管理器。

只读取 ``backend/app/prompts`` 下的模板，**绝不修改模板内容**。
目录约定：``prompts/<阶段名>/system.md`` 与 ``user.md``，阶段名见
constants.STAGE_ORDER。system.md 原样载入，user.md 用 Jinja2 渲染变量。

沿用原型的 ``StrictUndefined``：模板变量缺失时立即报错，避免静默产出
残缺提示词（这类错误很难在最终音频里被发现）。
新增阶段时只需在 prompts 下加目录，并在 constants 注册，无需改本类。
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..config import settings


class PromptManager:
    """按阶段名加载 / 渲染提示词的入口，供五个阶段函数复用。

    Args:
        prompt_dir: 模板根目录；缺省取 settings.prompts_dir，
            测试可传入临时目录做隔离。
    """

    def __init__(self, prompt_dir: Path | None = None) -> None:
        self.prompt_dir = Path(prompt_dir or settings.prompts_dir)
        if not self.prompt_dir.is_dir():
            raise FileNotFoundError(f"Prompt 目录不存在：{self.prompt_dir}")
        self.env = Environment(
            loader=FileSystemLoader(str(self.prompt_dir)),
            undefined=StrictUndefined,  # 缺变量直接抛错，不静默填空
            autoescape=False,           # 提示词是纯文本，不做 HTML 转义
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def load(self, name: str) -> str:
        """读取原始 system prompt（不渲染），保持模板内容原封不动。"""
        path = self.prompt_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Prompt 不存在：{path}")
        return path.read_text(encoding="utf-8")

    def render(self, name: str, **variables: object) -> str:
        """渲染 user.md 并去掉首尾空白。

        变量由调用方（stage_steps.py）按模板要求传入，字典 / 列表一般先
        json.dumps 成结构化文本，方便 LLM 稳定解析。
        """
        return self.env.get_template(name).render(**variables).strip()

    def get_stage(self, stage: str, **variables: object) -> dict[str, str]:
        """返回某个阶段的 system / user 两条 prompt。

        Returns:
            ``{"system_prompt": ..., "user_prompt": ...}``，键名与
            LLMClient.chat 的调用约定一致。
        """
        return {
            "system_prompt": self.load(f"{stage}/system.md"),
            "user_prompt": self.render(f"{stage}/user.md", **variables),
        }
