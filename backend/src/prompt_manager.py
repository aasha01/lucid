"""
Jinja2-based prompt manager for Lucid.

Prompts live in backend/prompts/*.j2 files so they can be edited and
iterated without touching Python code. Call reload() to pick up changes
without restarting the server.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


class PromptManager:
    def __init__(self, prompts_dir: Path = PROMPTS_DIR) -> None:
        self.prompts_dir = prompts_dir
        self._env = self._make_env()

    def render(self, template_name: str, **kwargs: object) -> str:
        """Render a .j2 template by filename with the given variables."""
        tmpl = self._env.get_template(template_name)
        return tmpl.render(**kwargs)

    def reload(self) -> None:
        """Re-read all template files from disk — no server restart needed."""
        self._env = self._make_env()

    def _make_env(self) -> Environment:
        return Environment(
            loader=FileSystemLoader(str(self.prompts_dir)),
            undefined=StrictUndefined,   # raises if a variable is missing
            trim_blocks=True,
            lstrip_blocks=True,
        )


# Module-level singleton — imported by explainer.py and main.py
prompt_manager = PromptManager()
