from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.logging import get_logger

log = get_logger(__name__)

SEVERITY_EMOJI = {
    "debug": "🔍",
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "🔴",
    "critical": "🚨",
}


class Renderer:
    """Renders provider templates with a per-event context.

    Providers declare a mapping of slot -> template file (see
    ``BaseProvider.templates``). Each slot produces one string that the
    provider consumes when building its payload.
    """

    def __init__(self, templates_dir: str | Path) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(default=False, enabled_extensions=("html", "htm")),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, event: dict[str, Any], templates: dict[str, str]) -> dict[str, str]:
        context = self._context(event)
        rendered: dict[str, str] = {}
        for slot, template_name in templates.items():
            try:
                rendered[slot] = self._env.get_template(template_name).render(context).strip()
            except Exception:
                log.exception(
                    "template_render_failed",
                    extra={"template": template_name, "event_id": event.get("id")},
                )
                raise
        return rendered

    def render_text(self, template: str, event: dict[str, Any]) -> str:
        """Render an inline template string (used for remediation URLs/bodies)."""
        return self._env.from_string(template).render(self._context(event))

    def _context(self, event: dict[str, Any]) -> dict[str, Any]:
        severity = str(event.get("severity", "info"))
        return {
            "event": event,
            "severity": severity,
            "emoji": SEVERITY_EMOJI.get(severity, ""),
        }
