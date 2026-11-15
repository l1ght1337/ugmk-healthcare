"""The bot's own patient phrases — only what the clinic scripts do not cover.

Script phrases (questions, routes, booking summary, documents) live in kb/scenarios/*.json.
All texts address the patient with «Вы» (CLAUDE.md, rule 7).
"""

# The marks of the bot's messages (docs/clinic_requests.md, item 69): an emoji is only the
# label at the start of a line, one a line at most, never inside a phrase of the scripts.
MARK_BOOKING = "📝"  # the booking: «Запись для себя», the first line of the greeting
MARK_DAY = "📅"
MARK_TIME = "🕒"
MARK_DOCTOR = "🩺"
MARK_ADDRESS = "📍"
MARK_QUESTION = "❓"  # the button «Задать вопрос»
MARK_ANSWERS = "💬"  # answers about the trauma centre
MARK_DONE = "✅"
MARK_AMBULANCE = "⚠️"  # a line with 103 or 112: always its own line, bold (app/bot/markup.py)
ASK_AUDIENCE = "Подскажите, пожалуйста, для кого запись?"
AUDIENCE_SELF = "Для себя"
AUDIENCE_CHILD = "Для ребёнка (до 18 лет)"
# two patients in one message («меня и сына», «двоих детей»): one booking at a time
ONE_BY_ONE = "Запишу по очереди: сначала одного пациента, потом второго."
ONE_BY_ONE_MANY = "Запишу по очереди: сначала одного пациента, потом остальных."  # three, more
WHO_FIRST = "Кого записать первым?"  # a button for each patient the message named
# the patient the bot is booking, when he was named in the conversation — above the step
PERSON_LINE = "Записываю {whom}"  # «Записываю Ваню: ногу подвернул», «Записываю Вас: …»
PERSON_EDIT = "Изменить"
BOOK_PERSON = "Записать {whom}"  # after the card, instead of «Записать ещё одного»
BOOK_MORE = "Записать ещё одного"  # under «Спасибо!» after the booking card
# «И сына тоже запишите» in the middle of a booking: one line, the step stays
NAMED_LATER = "{whom} запишу следующим — после этой записи."
# «Пока не записывай мужа, ещё посмотрим»: he is not booked and does not wait; one line
NOT_BOOKING = "Хорошо, {whom} не записываю. Понадобится — напишите."
# the name and the birth date were in a message before step 5: taken, not asked again
PATIENT_GIVEN = "Пациент: {name}, {birth}."
# An injury the patient himself calls old («года три назад») in a scenario of a first visit
# that does not ask when it happened: the rule of the script 03-1, and the administrator
OLD_INJURY = "В травмпункте принимаем травмы рук и ног сроком не более {days} дней."
# an injury the scripts say nothing about (config/clinic.yaml, no_route_words): a tooth
NO_ROUTE_DATA = "Куда с этим обращаться, в моих данных нет."
NEXT_PATIENT = "Спасибо! Теперь запишем второго пациента?"
NEXT_PATIENT_MORE = "Спасибо! Теперь запишем следующего пациента?"  # the third and those after
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
# The «угроза жизни» state of a conversation (config/clinic.yaml, emergency; the customer's
# wording of 14.11.2026, docs/clinic_requests.md item 65). The code picks the line by the
# words of the question; the same line is never said twice in a row.
# Every sentence with 103 or 112 is a line of its own behind ⚠️, bold (markup.ambulance).
DANGER_FIRST = "⚠️ С такими жалобами нужна скорая помощь: позвоните 103 или 112 прямо сейчас."
DANGER_NO_BOOKING = (
    "Записать Вас сейчас я не могу: травмпункт принимает по записи на время, а с такими "
    "жалобами ждать времени приёма нельзя.\n⚠️ Скорая — 103 или 112."
)
DANGER_WHERE = (
    "Адреса больницы для такого случая в моих данных нет.\n⚠️ Скорая отвезёт туда, где "
    "примут: 103 или 112."
)
# … where the scripts have addresses (an open fracture, a bite with the signs): after them
DANGER_WHERE_KNOWN = "⚠️ Скорая отвезёт туда, где примут: 103 или 112."
DANGER_ADULTS = "Взрослым:"
DANGER_CHILDREN = "Детям:"
DANGER_HOW_LONG = (
    "⚠️ Сколько ждать скорую, скажет диспетчер 103.\nЗапись в травмпункт при таких жалобах я "
    "не оформляю."
)
DANGER_CALLED = "Хорошо. Когда понадобится запись в травмпункт — напишите."
# «что делать, пока едут?», «жгут накладывать?»: no advice of the bot's own
DANGER_AID = (
    "Я бот и советов по первой помощи не даю. Что делать до приезда бригады, скажет диспетчер "
    "103 или 112."
)
DANGER_OTHER = (
    "Я бот и не могу оценить Ваше состояние, поэтому не записываю.\n⚠️ Номера скорой: 103 или "
    "112.\nМогу передать Ваше сообщение администратору."
)
# «Позвать администратора» outside the administrators' hours: before the consent and the phone
DANGER_ADMIN_LATER = (
    "Администратор ответит только после {start}.\n⚠️ Не ждите его — звоните 103 или 112."
)
# for the administrators: the first line of a request, and a line with a booking made later
DANGER_ADMIN_MARK = "⚠️ угроза жизни: {phrase}"
DANGER_WAS = "В разговоре была фраза из списка угрозы жизни: «{phrase}»"
DANGER_BY_SCENARIO = "опасные признаки по сценарию {number} «{title}»"
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
# A request to the administrators made in the middle of a booking: the booking is not made,
# and the bot says so — with «Продолжить запись» under it
NOT_BOOKED = "Запись на {when} не оформлена."
NOT_BOOKED_NO_TIME = "Запись не оформлена."
ADMIN_NOT_BOOKED = (
    "Выбранное время: {when}{doctor} — запись не оформлена, пациент обратился к администратору"
)
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
    "Мне жаль, что Вам сейчас тяжело. Я бот травмпункта и не могу помочь с этим сам.\n"
    "⚠️ Пожалуйста, позвоните 112 или на телефон доверия {phone} — там помогут."
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
    "⚠️ При угрозе жизни звоните 103 или 112."
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
# … the first sentence is the bold line, the places follow «Обратиться нужно:»
NOT_BOOKED_HERE = "С этим в травмпункт не записываем.\nОбратиться нужно:"
# «вы меня запишете?», «к вам можно?» after a route: the answer to a yes-or-no question
NOT_BOOKED_NO = "Нет, с этим в травмпункт не записываем.\nОбратиться нужно:"
QUESTIONS_TO_ADMIN = "Если остались вопросы, я передам их администратору."
# The scenario's own question (kb/scenarios, guard_question) before a booking scenario that
# DeepSeek named for a complaint with the words of section 5: «это укол чужой иглой?»
GUARD_YES = "Да"
GUARD_NO = "Нет"
# A booking started after a route (rule 5): asked once, before the scenario
SAME_OR_OTHER = "Это та же травма, о которой Вы писали, или другая?"
SAME_INJURY = "Та же"
OTHER_INJURY = "Другая"
# «ехать или ждать звонка?», «а туда как, просто приехать?» after a route: templates, the
# places of the route follow «Обратиться нужно:»; 103 / 112 is the clinic's emergency line
# A question about urgency («скорую вызывать?», «это срочно?»): the same answer everywhere,
# from the code; under it — the address of the route, if one was given in the conversation
URGENCY = (
    "Оценить состояние я не могу. Если сомневаетесь или становится хуже — звоните 103 или 112."
)
# with a request to the administrators made after a route, and with their hours
DO_NOT_WAIT = "Не ждите звонка, если становится хуже: 103 или 112."
ROUTE_NO_WAIT = "Ждать звонка администратора для этого не нужно."
ROUTE_GO = "Обратиться нужно:"
ROUTE_THERE = "Как там принимают, у меня данных нет — уточните в самом учреждении."
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
    "Я не могу давать медицинские рекомендации — это решит врач на приёме.\n"
    "⚠️ При угрозе жизни звоните 103 или 112."
)
CONFIRMED_WHEN = "Спасибо, ждём Вас {when}!"
FOR_WHOM = {
    "child": "Запись для ребёнка",
    "adult": "Запись для взрослого",
    "self": "Запись для себя",  # «меня укусила собака», «у меня болит рука»
}
# … with the child's age and name when they were said: «Запись для ребёнка, 4 месяца»,
# «Запись для ребёнка: Ваня, 10 лет»; the button under the line asks «Для кого запись?»
FOR_WHOM_NAMED = "{line}: {name}"
WHO_EDIT = "Изменить"
REQUEST_UNCLEAR = "Не совсем понял Вашу просьбу. Выберите, пожалуйста, что нужно сделать:"
ONLY_TEXT = "Я понимаю только текст и кнопки. Напишите, пожалуйста, словами или выберите кнопкой."

# --- comfort (task 3): the greeting, the summary, «зачем данные», answers, unfinished bookings
# The greeting after the phrase of script 01 (the customer's wording of 15.11.2026). The
# marks are those of app/bot/markup.py; the examples are in the text, not buttons.
GREETING_BOOK = "📝 **Запишу в травмпункт за минуту** — 5 коротких шагов."
GREETING_ASK = (
    "💬 **Отвечу на вопросы о травмпункте**: адрес, цены, парковка, документы. Если ответа у "
    "меня нет — передам вопрос администратору."
)
GREETING_HINT = (
    "Напишите своими словами, например _подвернул ногу_ или _запишите {doctor} на завтра_, — "
    "или нажмите кнопку."
)
# the buttons of an older greeting («Подвернул ногу», «Нужна перевязка», «Где парковаться?»)
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
# the topic in the summary when the patient named no injury: «Не знаю» on «На что больше
# похоже?», or a description from which DeepSeek picked the scenario (rule 1: no diagnoses)
SUMMARY_UNSURE = "не знаю, на что похоже"
SUMMARY_BY_WORDS = "травма по Вашему описанию"
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
# a text on the consent screen that is neither «согласен» nor a question DeepSeek answered
CONSENT_WHY = (
    "Согласие нужно, чтобы сохранить ФИО, дату рождения и телефон для записи и связи с Вами. "
    "Без него я не могу записать."
)
CONSENT_HINT = (
    "Чтобы продолжить, нажмите «Согласен» или «Не согласен» — или напишите «согласен», «назад» "
    "либо «меню»."
)
# «согласна. А телефон сейчас сказать?»: the answer, and the consent stays a button
CONSENT_PHONE_NEXT = "Телефон — следующим шагом. Чтобы продолжить, нажмите «Согласен»."
# «мне 13, я сам записываюсь» on the consent of a child's representative
CONSENT_BY_PARENT = (
    "Согласие за ребёнка младше 15 лет даёт родитель или законный представитель. Если его нет "
    "рядом, позовите администратора."
)
# 15–17 and «для себя»: the clinic's rule for a visit without a parent is not known yet
# (docs/clinic_requests.md, item 10) — the bot neither asks for «я законный представитель» nor
# books; the request goes to an administrator
# the second sentence of that screen; alone — the answer to «а если мама сама запишет?»
TEEN_PARENT_CAN = "Если рядом родитель или законный представитель, запись может оформить он."
TEEN = (
    "Вам {age}. Правила самостоятельной записи с 15 до 18 лет уточнит администратор{passed}. "
    + TEEN_PARENT_CAN
)
TEEN_PASSED = " — я передал ему обращение, с Вами свяжутся"
TEEN_PARENT = "Запись оформляет родитель"
TEEN_AGAIN = "Самостоятельную запись с 15 до 18 лет я оформить не могу — это решит администратор."
TEEN_REASON = (
    "Пациент {age} записывается сам: уточнить правила самостоятельной записи с 15 до 18 лет. "
    "Выбранное время: {when} (не забронировано)"
)
TEEN_NO_TIME = "не выбрано"
# «буду ждать звонка», «родитель не рядом» on that screen: before «Ваше обращение № N уже…»
TEEN_WAIT = "Хорошо, ждите звонка."
# the third «не понял» in a row on any screen: said plainly, with a way to a person
STUCK = (
    "Похоже, я Вас не понимаю. Выберите, пожалуйста, вариант кнопкой — или позовите администратора."
)
# The third answer in a row that is not understood on a step of a booking: the same screen
# under this line, with «Позвать администратора» among its buttons — the screen is not
# taken away from a patient who is one press from his booking
CANNOT_READ = "Не могу разобрать ответ. Нажмите кнопку под сообщением или позовите администратора."
# The same answer or screen is due for the third time in a row: one line instead of it,
# with «Передать вопрос администратору»; after a route — with DO_NOT_WAIT
STUCK_ASK = "Похоже, я не могу ответить на этот вопрос. Передать его администратору?"
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
# for the administrators, with a booking: what the conversation before it had
ADMIN_BOOKING_NOTE = "Запись № {number} ({when}, {doctor})."
# a route of a booking scenario (a burn of the face, a cosmetic suture), then a booking of
# the same scenario with another answer
ROUTE_ANSWER_CHANGED = (
    "После маршрута ответ изменён: было «{before}», стало «{after}» (вопрос: «{ask}»)"
)
ROUTE_AUDIENCE = {"child": "для ребёнка", "adult": "для взрослого"}
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
# a name with no word of the list of first names and no patronymic: asked, not taken silently
NAME_CONFIRM = "Пациент: {name} — верно?"
NAME_YES = "Да, верно"
NAME_AGAIN = "Ввести заново"
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
# «мне самой ехать или ждать звонка?» from a patient with a visit ahead: the code answers
BOOKING_HOLDS = (
    "Запись действует: {when}. Приходите к этому времени; ответ администратора на запись не влияет."
)
KEPT_WHEN = "Хорошо, запись на {date} в {time} остаётся."  # «спасибо, её не трогаем»
# «я уже записан, новую запись не надо» in the middle of a new booking
NO_NEW_BOOKING = "Хорошо, новую запись не оформляю."
# «Переношу запись с субботы, 14 ноября, 11:30.» — above the day and the time of the move
# The first line of every screen of a move: whose booking it is — «Перенос записи:
# Серябкина Л. В., вс 15.11, 19:30»
MOVE_HEADER = "Перенос записи: {patient}, {day}, {time}"
# «перенесите на пораньше», «на попозже» (config/clinic.yaml, move_relative): the wish line of
# the move, and the plain «no» when the day of the visit has no such time
MOVE_WISH = {"earlier": "раньше {time}", "later": "позже {time}"}
MOVE_NONE = {
    "earlier": "Раньше {time} {day} свободного времени нет.",
    "later": "Позже {time} {day} свободного времени нет.",
}
# Several things in one message (app/flow/actions.py): one at a time, the next is announced.
# «Сначала перенесу Вашу запись, потом запишу сына.» — «Теперь запишу сына.»
ACTIONS_FIRST = "Сначала {first}, потом {then}."
ACTIONS_THEN = ", потом "
ACTION_NEXT = "Теперь {what}."
# what cannot be told apart, or more than three things: nothing is done until a button
ACTIONS_TWO = "Понял два дела: {first} и {second}. С чего начать?"
ACTIONS_MANY = "Понял несколько дел: {things}. С чего начать?"
ACTIONS_REST = "Осталось: {things}. С чего продолжить?"
# the menu or «Оставить как есть» in the middle: what is left undone, in one line
ACTIONS_UNDONE = "Не сделано: {things}."
# the words for a thing: what the bot will do, what it is called, the button for it
ACTION_WILL = {"move": "перенесу {booking}", "cancel": "отменю {booking}", "book": "запишу {whom}"}
ACTION_NOUN = {"move": "перенос {of}", "cancel": "отмена {of}", "book": "запись {whose}"}
ACTION_BUTTON = {
    "move": "Перенести {booking}",
    "cancel": "Отменить {booking}",
    "book": "Записать {whom}",
}
ACTION_BOOKING = "запись {whose}"  # «перенесу запись мужа»
ACTION_BOOKING_OF = "записи {whose}"  # «отмена записи мужа»
ACTION_YOURS = "Вашу запись"
ACTION_YOURS_OF = "Вашей записи"
ACTION_ANY = "запись"  # nobody is named: «перенесу запись»
ACTION_ANY_OF = "записи"
ACTION_YOU = "Вас"  # «запишу Вас»
ACTION_ME = "меня"  # the button: «Записать меня»
ACTION_NEW_WILL = "оформлю новую запись"
ACTION_NEW = "новая запись"
ACTION_NEW_YOURS = "Ваша новая запись"
ACTION_NEW_BUTTON = "Новая запись"
NO_SUCH_BOOKING = "Такой записи не нашлось."
# the first line of «Отменить запись на … в …?» in a queue of things: whose booking it is
CANCEL_HEADER = "Отмена записи: {patient}, {day}, {time}"
# A complaint, or somebody who is not the patient of the booking, on a screen of its move:
# the move is closed — «Запись Лены Владимировны оставил как есть: вс 15.11, 19:30.»
MOVE_LEFT = "Запись {whose} оставил как есть: {day}, {time}."
KEEP_AS_IS = "Оставить как есть"
MOVE_WHICH = "Какую запись переносим?"
MOVE_ITEM = "{day}, {time} — {patient}"  # «Чт, 12.11, 13:00 — Петров И.»
# «Готово, запись Лены Владимировны перенесена с 19:30 на 19:00.»; another day — «с вс 15.11,
# 19:30 на пн 16.11, 10:00»
MOVED = "Готово, запись {whose} перенесена с {old} на {new}. Врач: {doctor}."
BOOKING_GONE = "Эта запись уже неактуальна."

# Reminders name the day explicitly («в пятницу, 13 ноября, в 14:30»), so the text stays
# true when /demo_next sends them early. When and what stand in the first, bold line: a push
# notification and the list of chats show only the beginning of a message. The date and the
# time are not repeated on lines of their own.
REMINDER_DAY = "Напоминаем: {when} — приём травматолога-ортопеда в травмпункте."
REMINDER_TWO_HOURS = "Скоро приём в травмпункте: {when}."
REMINDER_PATIENT = "Пациент"
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
# «уже у администратора» is not said twice in a row: the second time — what was passed
HANDOFF_NOTHING_NEW = (
    "Администратору передано: {what}. Нового сказать не могу — он свяжется с Вами{when} по "
    "телефону, который Вы указали."
)
PASSED_ANSWERS = "Ваше сообщение и ответы на мои вопросы"
PASSED_MESSAGE = "Ваше сообщение «{text}»"
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
SHIFT_DOCTOR = "🩺 Врач смены: {doctor} ({span})"
SHIFT_DOCTORS = "🩺 Врачи смены:"
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
WAY = "{address}."  # «📍 ул. Шейнкмана, 113/2, вход через детское отделение.»

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
# service notes: shown as a quote (app/bot/markup.py, rules)
QUOTED = (BOT_DEMO_NOTE, DEMO_BOOKING, WHY_NAME, WHY_PHONE, CONSENT_WITHDRAW)
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
