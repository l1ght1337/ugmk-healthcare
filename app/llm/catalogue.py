"""Static text blocks of the system prompts, built from the knowledge base.

The output depends only on kb/ files, so every call within one deployment sends the same
system prompt and DeepSeek serves it from its context cache. Operator notes never get
here (they are staff-only).
"""

from __future__ import annotations

from pathlib import Path

from app.config import ClinicData, get_clinic_data
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
    return "\n".join(lines)


PRICE_LINES = (
    ("primary_visit", "Первичный приём травматолога-ортопеда"),
    ("repeat_visit", "Повторный (контрольный) приём травматолога-ортопеда"),
)


def render_prices(clinic: ClinicData) -> str:
    """Prices from config/prices.yaml, so answers and the booking card name the same sums."""
    lines = ["# Стоимость приёма", "", "Источник: config/prices.yaml (ответ клиники 13.11.2026)."]
    lines.append("")
    for key, title in PRICE_LINES:
        lines.append(f"- {title} — {clinic.price_text(key) or 'стоимость уточнит администратор'}.")
    lines.append(
        "- Остальные манипуляции (рентген, повязки, наложение швов, гипс, ортез, скотчкаст "
        "и т. д.) оплачиваются отдельно; их стоимость уточнит администратор."
    )
    if clinic.prices.demo:
        lines.append("- Цены демонстрационные: бот работает в тестовом режиме.")
    return "\n".join(lines)


def render_clinic_facts(clinic: ClinicData) -> str:
    """Facts kept in config/ (not in kb/info), worded as the scripts word them."""
    lines = ["# Скорая помощь и вакцина", "", "Источник: config/clinic.yaml.", ""]
    lines.append(f"- {clinic.clinic.emergency.reply}")
    if clinic.contacts.ambulance_line_phone:
        lines.append(f"- Линия скорой помощи клиники: {clinic.contacts.ambulance_line_phone}.")
    if clinic.clinic.rabies_vaccine_available:
        lines.append("- Наличие антирабической вакцины на сегодня уточнит администратор.")
    else:  # 04-11
        lines.append(
            "- Антирабической вакцины нет — доктор может обработать место укуса и сделать "
            "перевязку."
        )
    return "\n".join(lines)


def render_info(info_dir: Path = KB_DIR / "info", clinic: ClinicData | None = None) -> str:
    """kb/info/*.md in a fixed order plus prices — the only source for answers to questions."""
    parts = [p.read_text(encoding="utf-8").strip() for p in sorted(info_dir.glob("*.md"))]
    clinic = clinic or get_clinic_data()
    parts += [render_prices(clinic), render_clinic_facts(clinic)]
    return "\n\n".join(parts) + "\n"
