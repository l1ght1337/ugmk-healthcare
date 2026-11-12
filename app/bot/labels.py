"""All button labels on one page for review: ``uv run python -m app.bot.labels --write``.

Generated from kb/scenarios (question buttons, topic menu) and app/bot/texts.py, so the
document always matches the bot; a test fails if docs/button_labels.md is out of date.
"""

from __future__ import annotations

import argparse

from app.bot import render, texts
from app.config import PROJECT_ROOT
from app.flow.loader import KnowledgeBase, load_kb

DOC = PROJECT_ROOT / "docs" / "button_labels.md"


def _row(where: str, labels: list[str]) -> str:
    return f"| {where} | {' · '.join(f'«{label}»' for label in labels)} |"


def render_labels(kb: KnowledgeBase) -> str:
    lines = [
        "# Подписи кнопок бота",
        "",
        "Файл собирается командой `uv run python -m app.bot.labels --write` из `kb/scenarios/` "
        "(поле `label` у вариантов ответа, `yes_label` / `no_label`, `choices`, `menu_label`) "
        "и `app/bot/texts.py`. Подпись кнопки — не длиннее 40 символов.",
        "",
        "## Вопросы сценариев",
        "| Сценарий | Вопрос | Кнопки |",
        "|---|---|---|",
    ]
    ordered = sorted(kb.scenarios.values(), key=lambda s: tuple(map(int, s.number.split("."))))
    for sc in ordered:
        for q in sc.questions:
            buttons = " · ".join(f"«{c.label}»" for c in q.buttons())
            lines.append(f"| {sc.number} {sc.title} | {q.ask} | {buttons} |")
    lines += [
        "",
        "## Меню тем (если жалобу не удалось разобрать)",
        " · ".join(f"«{c.label}»" for c in render.topic_menu(kb).choices),
        "",
        "## Остальные кнопки",
        "| Где | Кнопки |",
        "|---|---|",
        _row("Главное меню", [texts.MENU_BOOK, texts.MENU_ASK, texts.MENU_MY, texts.MENU_ADMIN]),
        _row("После «Записаться в травмпункт»", [texts.TOPIC_LIST]),
        _row("Для кого запись", [texts.AUDIENCE_SELF, texts.AUDIENCE_CHILD]),
        _row(
            "Ребёнок и возраст названы в жалобе",
            [texts.CHILD_YES.format(age="8 лет"), texts.CHILD_NO],
        ),
        _row(
            "Свободное время",
            [
                "Пт 13.11, 14:30 · Гусев А. В. (день, время, врач)",
                texts.MORE_TIME,
                texts.MENU_ADMIN,
            ],
        ),
        _row("Согласие на обработку персональных данных", [texts.CONSENT_YES, texts.CONSENT_NO]),
        _row(
            "Кого записываем", ["Петров Иван, 12.03.1985 (сохранённый пациент)", texts.PATIENT_NEW]
        ),
        _row("Телефон (клавиатура Telegram)", [texts.CONTACT_BUTTON]),
        _row("После подтверждения записи", [texts.PAYMENT_SELF, texts.PAYMENT_INSURANCE]),
        _row("Маршрут или вопрос без ответа", [texts.HANDOFF_YES, texts.HANDOFF_NO]),
        _row("Мои записи", [texts.MOVE, texts.CANCEL]),
        _row("Отмена записи", [texts.CANCEL_YES, texts.CANCEL_KEEP]),
        _row("Напоминания за 24 и за 2 часа", [texts.CONFIRM, texts.MOVE, texts.CANCEL]),
        _row("«Как прошёл приём?»", [texts.AFTER_VISIT_OK, texts.AFTER_VISIT_QUESTION]),
        _row("Контрольный приём", [texts.CONTROL_YES, texts.CONTROL_NO]),
        _row(
            "«Как Вы себя чувствуете?» (после контрольного)",
            [texts.FEELING_OK, texts.AFTER_VISIT_QUESTION],
        ),
        _row("Оценка сервиса", ["1", "2", "3", "4", "5"]),
        _row("Ответ администратора", [texts.ADMIN_ANSWER_REPLY]),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Button labels for review")
    parser.add_argument(
        "--write", action="store_true", help=f"write {DOC.relative_to(PROJECT_ROOT)}"
    )
    args = parser.parse_args()
    text = render_labels(load_kb())
    if args.write:
        DOC.write_text(text, encoding="utf-8")
        print(f"written {DOC}")
    else:
        print(text)


if __name__ == "__main__":
    main()
