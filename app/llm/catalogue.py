"""Static text blocks of the system prompts, built from the knowledge base.

The output depends only on kb/ files, so every call within one deployment sends the same
system prompt and DeepSeek serves it from its context cache. Operator notes never get
here (they are staff-only).
"""

from __future__ import annotations

from pathlib import Path

from app.flow.loader import KB_DIR, KnowledgeBase
from app.flow.schema import Scenario


def _order(scenario: Scenario) -> tuple[int, ...]:
    return tuple(int(part) for part in scenario.number.split("."))


def render_catalogue(kb: KnowledgeBase) -> str:
    lines = ["# Каталог сценариев", ""]
    for sc in sorted(kb.scenarios.values(), key=_order):
        lines.append(f"## {sc.id} — {sc.number} {sc.title}")
        lines.append(f"Когда выбирать: {sc.router_hint}")
        for q in sc.questions:
            lines.append(f"- вопрос {q.id}: «{q.ask}»")
            for f in q.fields:
                if f.type == "bool":
                    text = f"  - поле {f.name} (true/false): {f.description}"
                else:
                    values = "; ".join(f"{o.value} — {o.meaning}" for o in f.options)
                    text = f"  - поле {f.name} ({f.description}): {values}"
                if f.default is not None:
                    text += " Если пациент об этом сам не говорит — не указывай поле."
                lines.append(text)
        lines.append("")
    lines.append('# Запросы без сценария (intent = "unscripted")')
    lines += [f"- {request}" for request in kb.common.unscripted_requests]
    return "\n".join(lines) + "\n"


def render_info(info_dir: Path = KB_DIR / "info") -> str:
    """kb/info/*.md in a fixed order — the only source for answers to questions."""
    parts = [p.read_text(encoding="utf-8").strip() for p in sorted(info_dir.glob("*.md"))]
    return "\n\n".join(parts) + "\n"
