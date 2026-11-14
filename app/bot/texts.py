"""The bot's own patient phrases — only what the clinic scripts do not cover.

Script phrases (questions, routes, booking summary, documents) live in kb/scenarios/*.json.
All texts address the patient with «Вы» (CLAUDE.md, rule 7).
"""

ASK_AUDIENCE = "Подскажите, пожалуйста, для кого запись?"
AUDIENCE_SELF = "Для себя"
AUDIENCE_CHILD = "Для ребёнка (до 18 лет)"
# two patients in one message («меня и сына», «двоих детей»): one booking at a time
ONE_BY_ONE = (
    "Запишу по очереди: сначала одного пациента, потом второго — после записи нажмите "
    "«Записать ещё одного»."
)
BOOK_MORE = "Записать ещё одного"  # under «Спасибо!» after the booking card
NEXT_PATIENT = "Спасибо! Теперь запишем второго пациента?"
WHOM_ADULT = "Для взрослого"  # «для кого» of one more booking: «Для себя» would not fit
ASK_CHILD_AGE = "Сколько лет ребёнку? Если меньше года — сколько месяцев?"
ASK_CHILD_AGE_AGAIN = "Напишите, пожалуйста, возраст ребёнка цифрами, например: 7 лет или 4 месяца."
CLARIFY = "Уточните, пожалуйста: {ask}"
PICK_BUTTON = "Не получилось разобрать ответ. Выберите, пожалуйста, вариант:"
PICK_TOPIC = "Выберите, пожалуйста, что случилось:"

PRICE_UNKNOWN = "уточнит администратор"
DEMO_BOOKING = "Тестовая запись: в расписание клиники не попадает."

# After a handoff: the reason from the scenario outcome (patient_reason), then the next step.
HANDOFF = (
    "Я передал обращение № {number} администратору — он свяжется с Вами{when} по телефону, "
    "который Вы указали."
)
HANDOFF_LATER = " в рабочее время (с {start} до {end})"
OFFER_ADMIN = "Если удобнее, я могу передать Ваше обращение администратору — с Вами свяжутся."
EMERGENCY_ALSO = "Также можно обратиться:"
AMBULANCE_LINE = "Линия скорой помощи клиники: {phone}."
ROUTE_LEAD = "С такой травмой Вам нужно обратиться:"  # script branch has no phrase

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

# --- stage 4: menu and dialog ---------------------------------------------------------

MENU_BOOK = "Записаться в травмпункт"
MENU_ASK = "Задать вопрос"
MENU_MY = "Мои записи"
MENU_ADMIN = "Позвать администратора"
MENU_ICONS = {"book": "📝", "ask": "❓", "my": "📅", "admin": "👤"}  # main menu buttons
MENU = "Меню"  # next to «Назад» on every screen of a booking or a move
# the booking left for the menu: the first button of the main menu while it is fresh
RESUME = "Продолжить запись"
RESUME_GONE = "Начатая запись уже неактуальна. Начнём заново."

ASK_COMPLAINT = "С чем обращаетесь? Что произошло? Опишите своими словами или выберите из списка."
CHILD_CONFIRM = "Правильно понимаю: запись для ребёнка, {age}?"
CHILD_YES = "Да, ребёнок {age}"
CHILD_NO = "Нет"
ASK_ADMIN_REASON = "Пациент просит связаться с администратором"
MENU_HOME = "В главное меню"

# --- free text that is not a complaint (part B) ------------------------------------------
OFFTOPIC = "Я помогаю только с записью в травмпункт и вопросами о нём."
NOT_OUR_PROFILE = (
    "С этим вопросом не в травмпункт. Мы принимаем травмы рук и ног давностью до 7 дней, "
    "ожоги и укусы животных."
)
CRISIS = (
    "Мне жаль, что Вам сейчас тяжело. Я бот травмпункта и не могу помочь с этим сам. "
    "Пожалуйста, позвоните 112 или на телефон доверия {phone} — там помогут."
)
CRISIS_REASON = "Сообщение о риске для жизни"
CRISIS_ADMIN = (
    "⚠️ Сообщение о риске для жизни — не обычное обращение.\n"
    "Пользователь Telegram: {who} (id {id}){phone}\n"
    "Сообщение: «{text}»\n"
    "Бот ответил: позвонить 112 или на телефон доверия {line}."
)
CRISIS_MORE = "Ещё сообщение о риске для жизни от этого пользователя: «{text}»"
CRISIS_NO_FORWARD = (
    "Это сообщение о риске для жизни, а не обращение: ответ через бота пациенту не уходит."
)

# --- answers that are templates, not data (docs/faq_matrix.md) -----------------------------
MEDICAL = (
    "Я не могу давать медицинские рекомендации — это решит врач на приёме. Записать Вас?\n\n"
    "При угрозе жизни звоните 103 или 112."
)
ABOUT_BOT = (
    "Я виртуальный помощник травмпункта «УГМК-Здоровье», а не человек: помогаю записаться на "
    "приём и отвечаю на вопросы о травмпункте. Фамилию, имя, отчество, дату рождения и телефон "
    "я спрашиваю только для записи — они хранятся у клиники; удалить их можно командой "
    "/forget. Если нужен человек — нажмите «Позвать администратора»."
)
FEEDBACK = "Спасибо, что написали. Передать Ваше сообщение администратору?"
FEEDBACK_REASON = "Отзыв пациента: жалоба или благодарность"
OTHER_DEPARTMENT = (
    "Я помогаю только с травмпунктом. О других отделениях клиники подскажут по телефону "
    "клиники: {phone}."
)
OTHER_DEPARTMENT_NO_PHONE = (
    "Я помогаю только с травмпунктом. Телефона других отделений клиники у меня нет — могу "
    "передать вопрос администратору."
)
OTHER_DEPARTMENT_REASON = "Вопрос о другом отделении клиники"
MENU_FORGET = "Удалить мои данные"

ASK_INFO = "Напишите, пожалуйста, Ваш вопрос о травмпункте."
INFO_UNKNOWN = "У меня нет этой информации."
INFO_DOWN = "Сейчас не получается ответить на вопрос."
OFFER_HANDOFF = "Передать вопрос администратору?"
# a question typed on «Передать администратору?» that the reference does not answer
ADMIN_WILL_ANSWER = "На этот вопрос ответит администратор."
# the same complaint again right after its route: said plainly, with a way on
NOT_BOOKED_HERE = "С этим в травмпункт не записываем. Обратиться нужно:"  # the places follow
QUESTIONS_TO_ADMIN = "Если остались вопросы, я передам их администратору."
# «когда позвонит администратор?»: the hours from config/contacts.yaml
HANDOFF_WHEN = (
    "Администраторы отвечают с {start} до {end} — в это время с Вами свяжутся. Точное время "
    "назвать не могу."
)
ASK_ADMIN_QUESTION = "Передать вопрос администратору"
HANDOFF_YES = "Передать администратору"
HANDOFF_NO = "Не нужно"
QUESTION_REASON = "Вопрос пациента о травмпункте"
FLOW_BROKEN = (
    "Не получилось продолжить по сценарию. Можно вернуться в главное меню или позвать "
    "администратора."
)

# --- requests in the patient's own words (docs/intent_matrix.md) ----------------------------
FLOW_STOPPED = "Хорошо, остановились: запись не оформлена."
WELCOME = "Пожалуйста! Могу я ещё чем-нибудь Вам помочь?"
ASK_FEEDBACK = "Напишите, пожалуйста, что случилось — я передам Ваше сообщение администратору."
ASIDE_UNKNOWN = "На этот вопрос у меня нет данных — его можно задать администратору."
MEDICAL_ASIDE = (
    "Я не могу давать медицинские рекомендации — это решит врач на приёме. При угрозе жизни "
    "звоните 103 или 112."
)
CONFIRMED_WHEN = "Спасибо, ждём Вас {when}!"
FOR_WHOM = {
    "child": "Запись для ребёнка",
    "adult": "Запись для взрослого",
    "self": "Запись для себя",  # «меня укусила собака», «у меня болит рука»
}
REQUEST_UNCLEAR = "Не совсем понял Вашу просьбу. Выберите, пожалуйста, что нужно сделать:"
ONLY_TEXT = "Я понимаю только текст и кнопки. Напишите, пожалуйста, словами или выберите кнопкой."

# --- comfort (task 3): the greeting, the summary, «зачем данные», answers, unfinished bookings
# One example in the text — the one that is not a button below it.
GREETING_HINT = (
    "Запишу в травмпункт за минуту — 5 коротких шагов. Напишите своими словами, например "
    "«запишите {doctor} на завтра», или нажмите кнопку."
)
EXAMPLES = (
    ("ankle", "Подвернул ногу"),
    ("dressing", "Нужна перевязка"),
    ("parking", "Где парковаться?"),
)
SUMMARY = "Ваши ответы: {answers}."
SUMMARY_EDIT = "Изменить ответы"
SUMMARY_SELF = "для себя"
SUMMARY_ADULT = "для взрослого"
SUMMARY_CHILD = "для ребёнка"
# Answers that mean nothing without their question («да», «нет», «нет, не обращались») — in
# the bot's own words, by the fact the button sets: (field, value) → words. Not script phrases.
SUMMARY_ANSWERS = {
    ("seen_doctor", "no"): "к врачу не обращались",
    ("seen_doctor", "our_trauma_center"): "уже обращались в ваш травмпункт",
    ("seen_doctor", "other_doctor"): "обращались к другому врачу",
    ("danger_signs", "True"): "есть перечисленные признаки",
    ("danger_signs", "False"): "перечисленных признаков нет",
    ("first_dressing_here", "True"): "первую перевязку делали в вашем травмпункте",
    ("first_dressing_here", "False"): "первую перевязку делали не у вас",
    ("motor_function", "True"): "конечность двигается",
    ("motor_function", "False"): "конечность не двигается",
    ("suture_type", "surgical"): "нужен хирургический шов",
    ("open_or_displaced", "True"): "открытый перелом или перелом со смещением",
    ("had_injury", "True"): "была травма",
    ("had_injury", "False"): "травмы не было",
    ("help_provided", "no"): "помощь ещё не оказана",
    ("help_provided", "yes"): "помощь уже оказана",
    ("puncture_before_here", "True"): "пункцию у вас уже делали",
    ("puncture_before_here", "False"): "пункция впервые",
    ("first_visit_here", "True"): "первично лечились в вашем травмпункте",
    ("first_visit_here", "False"): "первично лечились в другом месте",
    ("sick_leave_open", "True"): "больничный открыт",
    ("sick_leave_open", "False"): "больничный не открыт",
}
WHY_NAME = "Эти данные нужны только для записи и хранятся у клиники."
WHY_PHONE = "Номер нужен, чтобы администратор мог связаться с Вами по записи."
HELPED = "Помогло"
HELPED_ICON = "👍"
HELPED_THANKS = "Спасибо! Могу я ещё чем-нибудь Вам помочь?"
UNFINISHED = "Вы начали запись в травмпункт и не закончили её. Продолжить с того же места?"
UNFINISHED_GO = "Продолжить"
UNFINISHED_AGAIN = "Начать заново"

MORE_TIME = "Другое время"
NO_SLOTS = (
    "К сожалению, свободного времени в ближайшие дни нет. Подобрать время поможет администратор."
)
SLOT_TAKEN = "Это время, к сожалению, уже заняли. Выберите, пожалуйста, другое:"
# a text on a step with buttons that neither the code nor DeepSeek understood: what can be
# typed there, in one line
HINT_TIME = (
    "Не понял сообщение. Можно написать день и время, например «завтра вечером», либо «назад» "
    "или «меню» — или выберите кнопкой."
)
HINT_BUTTONS = (
    "Не понял сообщение. Выберите, пожалуйста, вариант кнопкой или напишите «назад» либо «меню»."
)

CONSENT = (
    "Для записи нужны фамилия, имя, отчество и дата рождения пациента, а также Ваш номер "
    "телефона. Эти данные хранятся только у клиники и нужны для записи и связи с Вами."
)
CONSENT_HANDOFF = (
    "Чтобы администратор мог с Вами связаться, нужен Ваш номер телефона. Он хранится только "
    "у клиники."
)
CONSENT_WITHDRAW = (
    "Согласие можно отозвать в любой момент командой /forget — бот удалит Ваши данные и записи."
)
CONSENT_CONFIRM = "Нажимая «Согласен», я подтверждаю, что:"
CONSENT_ITEM_PD = "• даю согласие на обработку персональных данных"
CONSENT_ITEM_GUARDIAN = "• я законный представитель ребёнка"
CONSENT_POLICY = "Политика обработки персональных данных: {url}"
CONSENT_YES = "Согласен"
CONSENT_NO = "Не согласен"
CONSENT_DECLINED = (
    "Без согласия на обработку персональных данных я не смогу записать Вас или передать "
    "обращение администратору. Если передумаете — нажмите «Записаться в травмпункт»."
)
FORGET_ASK = (
    "Удалить Ваши данные из бота? Удалятся номер телефона, сохранённые пациенты и все записи "
    "(предстоящие записи отменятся, время освободится), согласие будет отозвано."
)
FORGET_YES = "Да, удалить"
FORGET_NO = "Нет"
FORGOTTEN = "Готово: Ваши данные и записи удалены, согласие отозвано."
FORGET_KEPT = "Хорошо, данные остаются."
FORGET_ADMIN = (
    "Пользователь Telegram id {user} отозвал согласие: его данные и записи удалены из бота. "
    "В этом чате при необходимости удалите вручную: {items}."
)
FORGET_HANDOFFS = "обращения {numbers}"
FORGET_BOOKINGS = "карточки записей {numbers}"

# --- bookings in the admin chat (BOOKINGS_TO_ADMIN_CHAT) -----------------------------------
ADMIN_BOOKING = (
    "Запись № {number}{demo}\n"
    "Дата и время: {when}\n"
    "Врач: {doctor}\n"
    "Приём: {visit}\n"
    "Тема: {topic}\n"
    "Пациент: {patient}\n"
    "Телефон: {phone}\n"
    "Оплата: {payment}\n"
    "Telegram: {telegram}"
)
ADMIN_BOOKING_DEMO = " (демо)"
VISIT_KIND = {"primary": "первичный", "repeat": "повторный"}
PAYMENT_KIND = {"self": "самостоятельно", "insurance": "по страховке", None: "не выбрана"}
PATIENT_LINE = "{name}, {birth} — {who}"
ADMIN_MOVED = "Перенесена на {when}. Врач: {doctor}."
ADMIN_CANCELLED = "Отменена пациентом."
ADMIN_SURVEY = {
    "ok": "«Как прошёл приём?» — «Всё хорошо».",
    "question": "«Как прошёл приём?» — «Есть вопрос» (обращение администратору).",
    "missed": "«Как прошёл приём?» — «Не был на приёме».",
}
ADMIN_FEELING = {
    "ok": "«Как Вы себя чувствуете?» — «Хорошо».",
    "question": "«Как Вы себя чувствуете?» — «Есть вопрос».",
}
ADMIN_RATING = "Оценка сервиса: {score} из 5."
ADMIN_CARD_NOTE = "Это карточка записи: ответ на неё пациенту не пересылается."
TODAY_TITLE = "Записи на {days}:"
TODAY_DAY = "\n{date}"
TODAY_ITEM = "• {time} — № {number}, {patient}, {topic}, {visit}. Врач: {doctor}"
TODAY_EMPTY = "На {days} записей нет."
TODAY_OFF = "Записи в этот чат не отправляются: включите BOOKINGS_TO_ADMIN_CHAT=true в .env."
# an exception inside a handler: the patient is told, the administrators get a short note
FAILED = "Что-то пошло не так, попробуйте ещё раз."
ADMIN_FAILURE = (
    "Ошибка в боте: {error} ({where}). Пациент получил «Что-то пошло не так, попробуйте ещё "
    "раз». Подробности — в логе бота."
)
# limits (BOOKING_LIMIT, LLM_PER_MINUTE)
TOO_MANY_BOOKINGS = (
    "У Вас уже есть предстоящие записи: {limit}. Больше записать не могу — отмените или "
    "перенесите одну из них либо позовите администратора."
)
TOO_MANY_MESSAGES = (
    "Слишком много сообщений подряд. Подождите, пожалуйста, минуту — или выберите кнопкой."
)
# /misses in the administrators' group: the journal of fallback answers (app/bot/misses.py)
MISSES_OFF = "Журнал непонятого выключен: задайте MISSES_LOG в .env."
MISSES_EMPTY = "Журнал непонятого пуст."
MISSES_DAY = "Непонятое за сутки: {count}"
MISSES_LAST = "Последние {count}:"
PATIENT_PICK = "Кого записываем?"
PATIENT_NEW = "Другой пациент"
ASK_NAME = "Начнём с ФИО."
ASK_NAME_AGAIN = (
    "Напишите, пожалуйста, фамилию, имя и отчество полностью, например: Иванов Иван Иванович."
)
# the second name in a row that could not be read: another line and a way out
NAME_HELP = (
    "Не получается разобрать ФИО. Напишите, пожалуйста, одним сообщением только три слова: "
    "фамилию, имя и отчество. Или позовите администратора."
)
# how the full name was read (any word order): shown above the request for the birth date
PATIENT_IS = "Пациент: {name}. Если неверно, нажмите «Назад»."
ASK_BIRTH = "Дата рождения пациента — например, 12.03.2015."
# the birth date does not fit «для себя / для ребёнка» or the age said: the scenario is led
# again with the right age, nothing is booked on the wrong branch
AGE_IS_CHILD = "По дате рождения пациенту {age} — это запись для ребёнка. Проверю ответы ещё раз."
AGE_IS_ADULT = "По дате рождения пациенту {age} — это запись для взрослого. Проверю ответы ещё раз."
AGE_DIFFERS = "По дате рождения ребёнку {age}, а не {said}. Проверю ответы ещё раз."
ASK_BIRTH_AGAIN = (
    "Не получилось разобрать дату. Напишите, пожалуйста, в формате ДД.ММ.ГГГГ, "
    "например: 12.03.2015."
)
ASK_CONTACT = (
    "Поделитесь, пожалуйста, номером телефона — нажмите кнопку «Поделиться контактом» внизу. "
    "Или напишите номер."
)
ASK_CONTACT_AGAIN = (
    "Не получилось разобрать номер. Нажмите, пожалуйста, «Поделиться контактом» внизу или "
    "напишите номер цифрами, например: +7 912 345-67-89."
)
CONTACT_BUTTON = "Поделиться контактом"
CONTACT_NOT_OWN = "Пожалуйста, отправьте свой номер кнопкой «Поделиться контактом»."
PAYMENT_SELF = "Самостоятельно"
PAYMENT_INSURANCE = "По страховке"
THANKS = "Спасибо!"

MY_EMPTY = "У Вас нет предстоящих записей."
MY_TITLE = "Ваши записи:"
MY_ONE = "Ваша запись:"
MY_ITEM = "{date} в {time} — {patient}. Врач: {doctor}."
MOVE = "Перенести"
CANCEL = "Отменить"
CANCEL_ASK = "Отменить запись на {date} в {time}?"
CANCEL_YES = "Да, отменить"
CANCEL_KEEP = "Нет, оставить"
CANCELLED = "Запись отменена, время освобождено."
# «отмени запись», «отмени запись на воскресенье», «отмени все»: never without a «да»
MY_LINE = "• {date} в {time} — {patient}. Врач: {doctor}."
CANCEL_WHICH = "Какую запись отменить?"
CANCEL_NOT_FOUND = "Такой записи не нашлось. Вот все Ваши записи — какую отменить?"
CANCEL_PICK = "Отменить: {day}, {time}"  # «Отменить: вс, 15.11, 21:00»
CANCEL_ALL = "Отменить все записи"
CANCEL_ALL_ASK = "Отменить все записи?"
CANCEL_ALL_YES = "Да, отменить все"
CANCELLED_ALL = "Все записи отменены, время освобождено."
# a first visit is being booked while the same patient has one ahead (not a control visit)
ALREADY_BOOKED = (
    "У Вас уже есть запись: {date} в {time}, {patient}. Перенести её или записать ещё раз?"
)
ALREADY_MOVE = "Перенести ту запись"
ALREADY_AGAIN = "Записать ещё раз"
KEPT = "Хорошо, запись остаётся."
# «я уже записан, новую запись не надо» in the middle of a new booking
NO_NEW_BOOKING = "Хорошо, новую запись не оформляю."
# «Переношу запись с субботы, 14 ноября, 11:30.» — above the day and the time of the move
MOVING = "Переношу запись {weekday}, {date}, {time}."
KEEP_AS_IS = "Оставить как есть"
MOVE_WHICH = "Какую запись переносим?"
MOVE_ITEM = "{day}, {time} — {patient}"  # «Чт, 12.11, 13:00 — Петров И.»
MOVED = "Готово, запись перенесена на {date} в {time}. Врач: {doctor}."
BOOKING_GONE = "Эта запись уже неактуальна."

# Reminders name the day explicitly («в пятницу, 13 ноября, в 14:30»), so the text stays
# true when /demo_next sends them early.
REMINDER_DAY = (
    "Напоминаем: {when} — приём травматолога-ортопеда в травмпункте. "
    "Пациент: {patient}. Врач: {doctor}."
)
REMINDER_TWO_HOURS = "Скоро приём в травмпункте: {when}. Адрес: {address}, {entrance}."
WEEKDAYS_ON = (
    "в понедельник",
    "во вторник",
    "в среду",
    "в четверг",
    "в пятницу",
    "в субботу",
    "в воскресенье",
)
CONFIRM = "Подтверждаю"
CONFIRMED = "Спасибо, ждём Вас!"
AFTER_VISIT = "Как прошёл приём?"
AFTER_VISIT_OK = "Всё хорошо"
AFTER_VISIT_QUESTION = "Есть вопрос"
AFTER_VISIT_MISSED = "Не был на приёме"
MISSED = "Жаль, что не получилось прийти. Записать Вас заново?"
GLAD = "Рады, что всё хорошо!"
OFFER_CONTROL = (
    "Контрольный приём обычно назначают примерно через {days}, точный срок определяет врач. "
    "Записать Вас на контрольный приём?"
)
CONTROL_NEAR = "Предлагаю время около {date}."
OTHER_DAY = "Другой день"
CONTROL_YES = "Записаться на контрольный приём"
CONTROL_NO = "Не нужно"
ASK_VISIT_QUESTION = "Напишите, пожалуйста, Ваш вопрос — я передам его администратору."
VISIT_QUESTION_REASON = "Вопрос после приёма {date} (запись № {booking})"
FEELING = "Как Вы себя чувствуете?"
FEELING_OK = "Хорошо"
RATE = "Оцените, пожалуйста, наш сервис от 1 до 5:"
RATED = "Спасибо за оценку!"

ADMIN_ANSWER = "Ответ администратора:\n{text}"
ADMIN_ANSWER_REPLY = "Ответить администратору"
ASK_ADMIN_REPLY = "Напишите ответ — я передам его администратору."
ADMIN_REPLY_SENT = "Ответ передан администратору."
HANDOFF_ALREADY = (
    "Ваше обращение № {number} уже у администратора — он свяжется с Вами{when} по телефону, "
    "который Вы указали."
)
HANDOFF_ADDED = (
    "Я добавил это к Вашему обращению № {number} — администратор свяжется с Вами{when} "
    "по телефону, который Вы указали."
)
HANDOFF_LIMIT = (
    "За сутки Вы уже отправили администратору {count} обращения — с Вами свяжутся по ним."
)
HANDOFF_LIMIT_PHONE = " Если вопрос срочный, позвоните в клинику: {phone}."
ADMIN_ADDENDUM = "Дополнение к обращению № {number}"
ADMIN_CLOSE = "Закрыть обращение"
ADMIN_CLOSED = "Обращение закрыто ({by})."
ADMIN_UNAVAILABLE = (
    "Сейчас не получается передать обращение администратору. Попробуйте, пожалуйста, позже."
)

DEMO_NONE = "Запланированных уведомлений нет."
DEMO_ONLY = "Команда доступна только в демо-режиме."
NOT_ALLOWED = "Бот работает в тестовом режиме и пока доступен только участникам теста."
ACCESS_REQUESTED = "Я передал запрос администраторам — когда доступ откроют, бот пришлёт сообщение."
ACCESS_GRANTED = "Доступ к тестовому боту открыт. Нажмите /start, чтобы начать."
ACCESS_ALLOW = "Разрешить"
STALE = "Эта кнопка уже неактуальна."
PICK_BUTTON_SHORT = "Выберите, пожалуйста, вариант кнопкой."
WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
WEEKDAYS_FROM = ("с понедельника", "со вторника", "со среды", "с четверга", "с пятницы",
                 "с субботы", "с воскресенья")  # fmt: skip

# --- part C: booking wizard ----------------------------------------------------------------

BACK = "Назад"
STEP = "Шаг {n} из {total} · {name}"
STAGES = ("Что случилось", "Для кого запись", "Уточняющие вопросы", "День и время",
          "Данные пациента")  # fmt: skip
DESCRIBE = "Опишите, пожалуйста, своими словами, что случилось."
# a description that did not name a topic: what is missing, never «опишите своими словами»
TOPIC_UNCLEAR = (
    "Не удалось понять, что случилось. Выберите, пожалуйста, вариант кнопкой или напишите "
    "подробнее: что болит и как это произошло."
)
# the same step is shown again: a line why, so the patient is not left guessing
GOING_ON = "Продолжаем запись. Остановились на этом шаге:"
SAME_STEP = "Мы на этом же шаге. Выберите, пожалуйста, вариант кнопкой."
NONE_FITS = "Если ни один вариант не подходит, нажмите «Другое»."
TODAY = "Сегодня"
TOMORROW = "Завтра"
TONIGHT = "сегодня ночью"  # in the evening: a time before 8:00 of the next day
DAY_PICK = "Выберите, пожалуйста, день приёма:"
DAY_GONE = "На этот день свободного времени уже нет. Выберите, пожалуйста, другой день:"
TIME_PICK = "Выберите, пожалуйста, время:"
PERIOD_PICK = "Выберите, пожалуйста, время суток:"
PERIOD_MORNING = "Утро 8–12"
PERIOD_DAY = "День 12–18"
PERIOD_EVENING = "Вечер 18–23"
PERIOD_NIGHT = "Ночь 23–8"
NEAREST = "Ближайшее: {when}"
EARLIER = "‹ Раньше"
LATER = "Позже ›"
SHIFT_DOCTOR = "Врач смены: {doctor} ({span})"
SHIFT_DOCTORS = "Врачи смены:"
SHIFT_FROM = "с {time}"
SHIFT_UNTIL = "до {time}"
REPEAT_CABINET_DOCTOR = "Повторный приём в кабинете «{cabinet}»: {doctor}"

# --- wishes: the doctor, the day and the time the patient asked for (app/flow/wishes.py) ----
WISH = "Пожелание: {wish}"
WISH_TODAY = "сегодня, {date}"
WISH_TOMORROW = "завтра, {date}"
WISH_PERIODS = {"morning": "утром", "day": "днём", "evening": "вечером", "night": "ночью"}
WISH_AFTER = "после {time}"
WISH_BEFORE = "до {time}"
WISH_BETWEEN = "с {start} до {end}"
WISH_AT = "около {time}"
WISH_CLEAR = "Убрать пожелание"
WISH_MOVE = "При переносе сразу покажу время по пожеланию: {wish}."
WISH_NEAR_TIME = "Ближе всего к {wanted}: {time}"
NO_SUCH_DOCTOR = "Такого врача в травмпункте нет. У нас принимают:"
ANY_DOCTOR = "Любой врач"
# the wish cannot be met: said plainly, with the ways out as buttons
WISH_DOCTOR_OFF = {
    "primary": "{day}, {doctor} не дежурит.",
    "repeat": "{day}, {doctor} не ведёт повторный приём.",
}
WISH_DOCTOR_FULL = "{day}, {doctor} принимает, но свободного времени уже нет."
WISH_DAY_FULL = "{day}, свободного времени нет."
WISH_DAY_CLOSED = "Запись на {date} пока не открыта: расписание есть по {last}."
WISH_TIME_GONE = (
    "{day}, {time} свободного времени по Вашему пожеланию нет. Есть другое время в этот день:"
)
WISH_TIME_NEVER = (
    "В ближайшие дни {time} свободного времени по Вашему пожеланию нет. Показываю другое время."
)
WISH_DOCTOR_NONE = "{doctor}: свободного времени в ближайшие дни нет."
WISH_NEAREST_DOCTOR = "{doctor}: {when}"  # «Ярунин: сегодня, 13:00»
WISH_OTHER_DOCTOR = "{day} — другой врач"
WISH_ANOTHER_DOCTOR = "Выбрать другого врача"

# questions about the schedule: answered by code from the schedule provider
SHIFTS_TITLE = "{day}, в травмпункте принимают:"
SHIFT_LINE = "• {span} — {doctor}"
SHIFTS_REPEAT = "Повторные приёмы, кабинет «{cabinet}»: {doctor} ({span})"
SHIFTS_NONE = "Расписания на {date} пока нет: оно есть по {last}."
SPAN_ALL_DAY = "круглосуточно"
REPEAT_ONLY = "{span}, повторные приёмы"
DOCTOR_DAYS = "{doctor} принимает в ближайшие дни:"
DOCTOR_DAY_LINE = "• {day} — {spans}"
DOCTOR_ON_DAY = "{day}, {doctor} принимает: {spans}."
DOCTOR_OFF_DAY = "{day}, {doctor} не принимает."
DOCTOR_NO_DAYS = "{doctor}: в расписании на ближайшие дни приёма нет."
# «какие у вас врачи?»: everyone from the data, with the post from kb/info/travmpunkt.md
DOCTORS_TITLE = "В травмпункте принимают:"
DOCTORS_LINE = "• {name} — {role}"
DOCTORS_NOW = "Сейчас дежурит: {doctors}."
DOCTORS_HINT = "Расписание врача — кнопкой с его фамилией."
FREE_ON = "{when} есть свободное время: {times}."
FREE_NONE = "{when} свободного времени нет."
FREE_MORE = "{times} и позже"
FREE_NOTE = "Точное время покажу после вопросов о травме."
BOOK_THIS_DAY = "Записаться на этот день"
BOOK_THIS_DOCTOR = "Записаться к этому врачу"
BOOK_THIS_TIME = "Записаться на это время"

CARD_TITLE = "Запись в травмпункт"
CARD_DATE = "Дата"
CARD_TIME = "Время"
CARD_DOCTOR = "Врач"
CARD_ADDRESS = "Адрес"
CARD_BRING = "Что взять с собой"
CARD_ICONS = {
    "title": "✅", "date": "📅", "time": "🕒", "doctor": "🩺", "address": "📍", "price": "💳",
    "bring": "🧾",
}  # fmt: skip
PAYMENT_QUESTION = "Оплачивать планируете самостоятельно или по страховке?"
VENUE_TITLE = "Травмпункт «УГМК-Здоровье»"
MORE = "Подробнее"
HOW_TO_GET = "Как пройти"
WAY = "{address}, {entrance}."

# Bot profile (BotFather fields set at startup; the avatar and picture come from the customer).
BOT_DESCRIPTION = (
    "Виртуальный помощник травмпункта «УГМК-Здоровье», Екатеринбург, {address}.\n\n"
    "Помогу записаться на приём травматолога-ортопеда, напомню о визите и отвечу на вопросы "
    "о травмпункте: адрес, часы работы, парковка, документы.\n\n"
    "При угрозе жизни звоните 103 или 112."
)
BOT_SHORT_DESCRIPTION = (
    "Запись в травмпункт «УГМК-Здоровье», напоминания о приёме и ответы на вопросы."
)
BOT_DEMO_NOTE = "Тестовый режим: записи демонстрационные."
COMMANDS = (
    ("start", "Начать"),
    ("book", "Записаться в травмпункт"),
    ("my", "Мои записи"),
    ("ask", "Задать вопрос"),
    ("admin", "Позвать администратора"),
    ("menu", "Главное меню"),
    ("forget", "Удалить мои данные и отозвать согласие"),
)
DEMO_COMMAND = ("demo_next", "Демо: следующее уведомление")
