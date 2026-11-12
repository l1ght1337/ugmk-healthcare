"""The bot's own patient phrases — only what the clinic scripts do not cover.

Script phrases (questions, routes, booking summary, documents) live in kb/scenarios/*.json.
All texts address the patient with «Вы» (CLAUDE.md, rule 7).
"""

ASK_AUDIENCE = "Подскажите, пожалуйста, запись для Вас или для ребёнка (до 18 лет)?"
ASK_CHILD_AGE = "Сколько лет ребёнку? Если меньше года — сколько месяцев?"
CLARIFY = "Уточните, пожалуйста: {ask}"

PRICE_UNKNOWN = "уточнит администратор"
DEMO_BOOKING = (
    "Это тестовая запись: бот работает в демо-режиме, и запись не попадает в настоящее "
    "расписание травмпункта."
)
DEMO_GREETING = (
    "Бот работает в тестовом режиме: записи демонстрационные и не попадают в настоящее "
    "расписание травмпункта."
)

HANDOFF = "Передаю Ваше обращение администратору. С Вами свяжутся{when}."
HANDOFF_LATER = " в рабочее время — с {start} до {end}"
OFFER_ADMIN = "Если удобнее, я могу передать Ваше обращение администратору — с Вами свяжутся."
EMERGENCY_ALSO = "Также можно обратиться:"
ROUTE_LEAD = "Вам нужно обратиться:"  # route outcomes whose script branch has no phrase

BOOKED = {"male": "записан", "female": "записана", None: "записан(а)"}

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
