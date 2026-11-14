"""All button labels on one page for review: ``uv run python -m app.bot.labels --write``.

Generated from kb/scenarios (question buttons, topic menu) and app/bot/texts.py, so the
document always matches the bot; a test fails if docs/button_labels.md is out of date.
"""

from __future__ import annotations

import argparse

from app.bot import texts
from app.bot.conversation import _menu
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
        "(поле `label` у вариантов ответа, `yes_label` / `no_label`, `choices`, `_menu.json`) "
        "и `app/bot/texts.py`. Подпись кнопки — не длиннее 40 символов; до 18 символов кнопки "
        "стоят по две в ряд, длиннее — по одной, чтобы подпись не обрезалась на телефоне. "
        "Цвета (Bot API 9.4): синий — главное действие, зелёный — подтверждение, красный — "
        "отмена и удаление; в старых версиях Telegram кнопки обычные.",
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
        "## Темы (что случилось, а не диагнозы)",
        "Когда область тела известна из жалобы, темы других областей не показываются. "
        "Пять групп — только по кнопке «Выбрать из списка», пока жалобы нет.",
        "",
        "| Меню | Текст | Кнопки |",
        "|---|---|---|",
    ]
    for name, menu in kb.topics.menus.items():
        buttons = " · ".join(f"«{item.caption}»" for item in menu.items)
        lines.append(f"| {name} | {menu.text} | {buttons} |")
    lines += [
        "",
        "## Остальные кнопки",
        "| Где | Кнопки |",
        "|---|---|",
        _row("Главное меню", [b.label for row in _menu() for b in row]),
        _row(
            "После «Записаться в травмпункт»: группы тем сразу, под «С чем обращаетесь?»",
            [item.caption for item in kb.topics.menus[kb.topics.root].items],
        ),
        _row(
            "Нижний ряд каждого экрана записи и переноса, в одну строку (на первом экране, где "
            "возвращаться некуда, — только «Меню»)",
            [texts.BACK, texts.MENU],
        ),
        _row(
            "Главное меню, пока начатая запись не старше 45 минут: первая кнопка",
            [texts.RESUME],
        ),
        _row("Для кого запись", [texts.AUDIENCE_SELF, texts.AUDIENCE_CHILD]),
        _row(
            "Ребёнок и возраст названы в жалобе",
            [texts.CHILD_YES.format(age="8 лет"), texts.CHILD_NO],
        ),
        _row(
            "День приёма (по две в ряд)",
            [
                "Сегодня, 12.11",
                "Завтра, 13.11",
                "Сб, 14.11",
                texts.EARLIER,
                texts.LATER,
                texts.MENU_ADMIN,
            ],
        ),
        _row(
            "Первая кнопка на экране дня (у контрольного приёма и у дня, который назвал "
            "пациент, — на экране времени)",
            [
                texts.NEAREST.format(when="сегодня, 14:30"),
                texts.NEAREST.format(when=f"{texts.TONIGHT}, 01:00"),
                texts.NEAREST.format(when="вс, 15.11, 08:00"),
            ],
        ),
        _row(
            "Время суток (только где есть свободное время)",
            [texts.PERIOD_MORNING, texts.PERIOD_DAY, texts.PERIOD_EVENING, texts.PERIOD_NIGHT],
        ),
        _row("Время (по четыре в ряд; сверху — врач смены)", ["13:30", "14:00", "14:30", "15:00"]),
        _row(
            "Приветствие: примеры над главным меню",
            [label for _, label in texts.EXAMPLES],
        ),
        _row(
            "Сводка ответов перед выбором времени",
            [texts.SUMMARY_EDIT, texts.MORE],
        ),
        _row(
            "Ответ на вопрос о травмпункте",
            [f"{texts.HELPED_ICON} {texts.HELPED}", texts.MENU_ADMIN, texts.MENU_HOME],
        ),
        _row(
            "Напоминание о брошенной записи (через 15 минут, не ночью)",
            [texts.UNFINISHED_GO, texts.UNFINISHED_AGAIN],
        ),
        _row("Контрольный приём: время около +10 дней", [texts.OTHER_DAY]),
        _row(
            "Пациент назвал врача, день или время: шаг «День и время»",
            [texts.OTHER_DAY, texts.WISH_CLEAR],
        ),
        _row(
            "Пожелание выполнить нельзя (врач не дежурит, времени нет)",
            [
                texts.WISH_NEAREST_DOCTOR.format(doctor="Ярунин", when="вс, 15.11, 20:00"),
                texts.WISH_OTHER_DOCTOR.format(day="Завтра, 14.11"),
                texts.OTHER_DAY,
                texts.WISH_CLEAR,
            ],
        ),
        _row(
            "Время названо точно («в 14:30»)",
            [texts.WISH_NEAR_TIME.format(wanted="14:30", time="15:00")],
        ),
        _row(
            "Такого врача нет: врачи из расписания",
            ["Кузнецов Н. А.", "Ярунин А. А.", texts.ANY_DOCTOR, texts.MENU_HOME],
        ),
        _row(
            "Ответ на вопрос о расписании",
            [
                texts.BOOK_THIS_DAY,
                texts.BOOK_THIS_DOCTOR,
                texts.BOOK_THIS_TIME,
                texts.MENU_HOME,
            ],
        ),
        _row(
            "Ответ на «какие у вас врачи?»",
            [texts.MENU_BOOK, "Кузнецов Н. А.", "Ярунин А. А.", texts.MENU_HOME],
        ),
        _row(
            "Расписание врача по кнопке с его фамилией", [texts.BOOK_THIS_DOCTOR, texts.MENU_HOME]
        ),
        _row("Карточка записи", [texts.MOVE, texts.CANCEL]),
        _row("Длинная фраза скрипта (коротко)", [texts.MORE]),
        _row("Напоминание за 2 часа", [texts.HOW_TO_GET, texts.CONFIRM, texts.MOVE, texts.CANCEL]),
        _row("Согласие на обработку персональных данных", [texts.CONSENT_YES, texts.CONSENT_NO]),
        _row(
            "Кого записываем", ["Петров Иван, 12.03.1985 (сохранённый пациент)", texts.PATIENT_NEW]
        ),
        _row("Телефон (клавиатура Telegram)", [texts.CONTACT_BUTTON, texts.BACK, texts.MENU]),
        _row("После карточки записи: оплата", [texts.PAYMENT_SELF, texts.PAYMENT_INSURANCE]),
        _row(
            "Маршрут с вариантом «передать администратору»", [texts.HANDOFF_YES, texts.HANDOFF_NO]
        ),
        _row("Вопрос без ответа в справке", [texts.ASK_ADMIN_QUESTION, texts.MENU_HOME]),
        _row("Просьба «соедините с администратором» текстом", [texts.MENU_ADMIN, texts.MENU_HOME]),
        _row(
            "«Я не могу давать медицинские рекомендации…»: «Записаться» ведёт в сценарий или "
            "темы травмы, названной в вопросе",
            [texts.MENU_BOOK, texts.MENU_HOME],
        ),
        _row("Мои записи", [texts.MOVE, texts.CANCEL]),
        _row(
            "Перенос записи: внизу каждого экрана выбора дня и времени",
            [texts.KEEP_AS_IS, texts.BACK, texts.MENU],
        ),
        _row(
            "«Перенеси на завтра», когда записей несколько: какую переносим",
            [
                texts.MOVE_ITEM.format(day="Чт, 12.11", time="13:00", patient="Петров И. С."),
                texts.MENU_HOME,
            ],
        ),
        _row(
            "Подтверждение текстом («подтверждаю», «да, приду»)",
            [texts.MOVE, texts.CANCEL, texts.MENU_HOME],
        ),
        _row(
            "«Задать вопрос», «хочу пожаловаться», «Есть вопрос» после приёма: бот ждёт текст",
            [texts.MENU_HOME],
        ),
        _row(
            "Возраст, ФИО или дата рождения не разобраны: бот просит ещё раз",
            [texts.BACK, texts.MENU],
        ),
        _row("Отмена записи", [texts.CANCEL_YES, texts.CANCEL_KEEP]),
        _row(
            "«Отмени запись», когда записей несколько: какую отменить",
            [
                texts.CANCEL_PICK.format(day="вс, 15.11", time="21:00"),
                texts.CANCEL_ALL,
                texts.MENU_HOME,
            ],
        ),
        _row("«Отмени все»: «Отменить все записи?»", [texts.CANCEL_ALL_YES, texts.CANCEL_KEEP]),
        _row(
            "У пациента уже есть запись на первичный приём: вопрос перед выбором времени",
            [texts.ALREADY_MOVE, texts.ALREADY_AGAIN],
        ),
        _row("Напоминания за 24 и за 2 часа", [texts.CONFIRM, texts.MOVE, texts.CANCEL]),
        _row("«Как прошёл приём?»", [texts.AFTER_VISIT_OK, texts.AFTER_VISIT_QUESTION]),
        _row("Контрольный приём", [texts.CONTROL_YES, texts.CONTROL_NO]),
        _row(
            "«Как Вы себя чувствуете?» (после контрольного)",
            [texts.FEELING_OK, texts.AFTER_VISIT_QUESTION],
        ),
        _row("Оценка сервиса", ["1", "2", "3", "4", "5"]),
        _row("Ответ администратора", [texts.ADMIN_ANSWER_REPLY]),
        _row("/forget — удалить данные и отозвать согласие", [texts.FORGET_YES, texts.FORGET_NO]),
        _row("Чат администраторов: запрос доступа нового пользователя", [texts.ACCESS_ALLOW]),
        _row("Чат администраторов: обращение пациента", [texts.ADMIN_CLOSE]),
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
