"""The patient simulator offline: the runner with a scripted patient, and every rule.

DeepSeek is not called here; `uv run python -m tests.simulate` is the live run.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.bot import texts
from app.config import ClinicData
from app.flow.loader import KnowledgeBase
from tests import simulate as sim
from tests.simulate import Action, Dialog, ScriptedPatient, Shown, Step, check, play, render_report
from tests.simulate_personas import PERSONAS, SECTION_5, Persona

BY_KEY = {p.key: p for p in PERSONAS}
BOOK_BY_BUTTONS = (
    ("press", "Записаться в травмпункт"),
    ("press", "Рука или нога"),
    ("press", "Похоже на вывих"),
    ("press", "Для себя"),
    ("press", "Бытовая травма"),
    ("press", "В течение 7 дней"),
    ("press", "Нет, не обращались"),
    ("press", "Завтра, 13.11"),
    ("press", "Утро 8–12"),
    ("press", "11:00"),
    ("press", "Согласен"),
    "Ильина Тамара Борисовна",
    "04.04.1975",
    "contact",
    ("press", "Самостоятельно"),
)


@pytest.fixture
def env(kb: KnowledgeBase, clinic: ClinicData, tmp_path: Path) -> sim.Env:
    return sim.Env(kb, clinic, None, tmp_path)  # no DeepSeek: buttons and word lists


def rules(dialog: Dialog, env: sim.Env) -> set[str]:
    return {v.rule for v in check(dialog, env.kb, env.clinic)}


def state(**changes: object) -> dict:
    names = ("scenario", "question", "outcome", "audience", "for_whom", "doctor", "day")
    base = {"step": "idle", "wizard": False, "moving": False, **dict.fromkeys(names)}
    return {**base, **changes}


def made(persona: Persona, *steps: Step, opening: Shown | None = None) -> Dialog:
    first = opening or Shown("Здравствуйте!", ("Записаться в травмпункт",))
    return Dialog(persona, sim.FIRST_DAY, [first], [], list(steps), ended="stop")


def step(n: int, action: Action, *replies: Shown, **changes: object) -> Step:
    return Step(n, action, list(replies), state(**changes), [], 0)


# --- the personas -------------------------------------------------------------------------------


def test_personas_are_forty_to_seventy_and_made_up() -> None:
    assert 40 <= len(PERSONAS) <= 70
    assert len(BY_KEY) == len(PERSONAS) == len({p.name for p in PERSONAS})
    assert all(p.phone.startswith("+7900") for p in PERSONAS)  # a range no operator uses
    goals = {p.goal for p in PERSONAS}
    assert goals == {
        "book", "move", "keep", "cancel", "price", "ask", "two", "several", "unknown"
    }  # fmt: skip
    assert sum(p.booked > 0 for p in PERSONAS) >= 6 and any(p.clock != "12:00" for p in PERSONAS)
    for persona in PERSONAS:
        assert persona.goal in sim.GOALS and persona.manner and persona.story
        prompt = sim.persona_prompt(persona, [])
        assert persona.name in prompt and sim.GOALS[persona.goal] in prompt


# --- the runner ---------------------------------------------------------------------------------


async def test_booking_by_buttons_passes_every_rule(env: sim.Env) -> None:
    dialog = await play(BY_KEY["buttons_only"], ScriptedPatient(*BOOK_BY_BUTTONS), env)
    assert dialog.ended == "stop" and len(dialog.steps) == len(BOOK_BY_BUTTONS)
    (booking,) = dialog.steps[-1].bookings
    assert (booking["status"], booking["date"], booking["time"]) == ("active", "13.11", "11:00")
    assert check(dialog, env.kb, env.clinic) == []
    assert dialog.steps[1].replies[-1].buttons[-1] == texts.MENU  # as the patient sees it


async def test_dialog_dropped_in_the_middle_misses_the_goal(env: sim.Env) -> None:
    dialog = await play(BY_KEY["buttons_only"], ScriptedPatient(*BOOK_BY_BUTTONS[:6]), env)
    assert rules(dialog, env) == {sim.GOAL}


async def test_the_turn_limit_ends_the_dialog(env: sim.Env) -> None:
    patient = ScriptedPatient(*["не знаю"] * 10)
    dialog = await play(BY_KEY["vague"], patient, env, turns=4)
    assert dialog.ended == "turns" and len(dialog.steps) == 4


async def test_a_route_is_a_plain_refusal_with_a_way_out(env: sim.Env) -> None:
    patient = ScriptedPatient(
        ("press", "Записаться в травмпункт"),
        ("press", "Голова, лицо, глаза, уши"),
        ("press", "Ударился головой"),
        ("press", "Для себя"),
    )
    dialog = await play(BY_KEY["head"], patient, env)
    assert dialog.steps[-1].state["outcome"] == "route" and dialog.steps[-1].bookings == []
    assert check(dialog, env.kb, env.clinic) == []


async def test_moving_and_cancelling_a_visit_booked_before(env: sim.Env) -> None:
    move = ScriptedPatient("перенеси запись на воскресенье", ("press", "День 12–18"))
    dialog = await play(BY_KEY["move_day"], move, env)
    assert len(dialog.before) == 1 and dialog.before[0]["date"] == "13.11"
    shown = dialog.steps[-1].replies[-1]
    await_time = [label for label in shown.buttons if label[:2].isdigit()]
    more = ScriptedPatient(
        "перенеси запись на воскресенье", ("press", "День 12–18"), ("press", await_time[0])
    )
    dialog = await play(replace(BY_KEY["move_day"], key="move_day_2"), more, env)
    assert dialog.steps[-1].bookings[0]["date"] == "15.11"
    assert check(dialog, env.kb, env.clinic) == []

    cancel = ScriptedPatient("отмени все", ("press", "Да, отменить все"))
    dialog = await play(BY_KEY["cancel_all"], cancel, env)
    assert [b["status"] for b in dialog.steps[-1].bookings] == ["cancelled", "cancelled"]
    assert check(dialog, env.kb, env.clinic) == []

    wrong = ScriptedPatient("отмени запись на завтра", ("press", "Да, отменить"))
    dialog = await play(BY_KEY["cancel_by_day"], wrong, env)
    # the Sunday one was to be cancelled: the visit of tomorrow is gone though nobody asked
    assert rules(dialog, env) == {sim.GOAL, sim.TOUCHED}


async def test_keeping_the_visit_after_starting_a_move(env: sim.Env) -> None:
    patient = ScriptedPatient("хочу перенести", ("press", "Оставить как есть"))
    dialog = await play(BY_KEY["move_then_keep"], patient, env)
    assert check(dialog, env.kb, env.clinic) == []


async def test_a_crash_in_the_bot_is_reported(env: sim.Env, monkeypatch) -> None:
    from app.bot.conversation import Conversation

    async def boom(self, c, text):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    monkeypatch.setattr(Conversation, "_on_text", boom)
    dialog = await play(BY_KEY["vague"], ScriptedPatient("болит"), env)
    assert dialog.ended == "crash" and "RuntimeError: boom" in dialog.steps[0].error
    assert rules(dialog, env) == {sim.CRASH}


# --- the rules on made-up transcripts -----------------------------------------------------------

PRICE = BY_KEY["price_first"]
MENU = Shown("Могу я ещё чем-нибудь Вам помочь?", ("Записаться в травмпункт",))
DAYS = Shown("Шаг 4 из 5 · День и время\n\nВыберите, пожалуйста, день приёма:", ("Завтра, 13.11",))


def test_the_same_screen_twice_without_a_line(env: sim.Env) -> None:
    again = step(1, Action("say", "хм"), DAYS, step="days", wizard=True)
    dialog = made(BY_KEY["vague"], again, opening=DAYS)
    assert sim.REPEAT in rules(dialog, env)
    with_line = step(1, Action("say", "хм"), Shown(texts.HINT_TIME), DAYS, step="days", wizard=True)
    assert sim.REPEAT not in rules(made(BY_KEY["vague"], with_line, opening=DAYS), env)


def test_a_question_left_without_an_answer(env: sim.Env) -> None:
    before = step(1, Action("press", "x"), DAYS, step="days", wizard=True)
    hint = step(2, Action("say", "а сколько стоит приём?"), Shown(texts.HINT_TIME), DAYS,
                step="days", wizard=True)  # fmt: skip
    assert sim.UNANSWERED in rules(made(BY_KEY["questions"], before, hint), env)
    answer = step(2, Action("say", "а сколько стоит приём?"), Shown("Приём — 2000 рублей."), DAYS,
                  step="days", wizard=True)  # fmt: skip
    assert sim.UNANSWERED not in rules(made(BY_KEY["questions"], before, answer), env)
    moved_on = step(2, Action("say", "можно на завтра?"), Shown("13 ноября", ("11:00",)),
                    step="times", wizard=True)  # fmt: skip
    assert sim.UNANSWERED not in rules(made(BY_KEY["questions"], before, moved_on), env)


def test_price_goal(env: sim.Env) -> None:
    told = step(1, Action("say", "сколько стоит приём?"), Shown("Приём — 2000 рублей.", ("Меню",)))
    assert check(made(PRICE, told), env.kb, env.clinic) == []
    silent = step(1, Action("say", "сколько стоит приём?"), MENU)
    assert sim.GOAL in rules(made(PRICE, silent), env)


def test_an_answer_without_a_button(env: sim.Env) -> None:
    bare = step(1, Action("say", "сколько стоит приём?"), Shown("Приём — 2000 рублей."))
    assert sim.NO_BUTTON in rules(made(PRICE, bare), env)
    crisis = step(1, Action("say", "…"), Shown(texts.CRISIS.format(phone="8-800")))
    assert sim.NO_BUTTON not in rules(made(PRICE, crisis), env)


@pytest.mark.parametrize(
    "said",
    [
        "Приложите лёд и не наступайте на ногу.",
        "Скорее всего, у вас перелом.",
        "Рекомендую выпить обезболивающее.",
        "Похоже, это растяжение связок.",
    ],
)
def test_advice_and_diagnosis_are_caught(env: sim.Env, said: str) -> None:
    dialog = made(PRICE, step(1, Action("say", "что делать?"), Shown(said, ("Меню",))))
    assert sim.ADVICE in rules(dialog, env)


def test_what_the_scripts_and_the_bot_say_is_not_advice(env: sim.Env) -> None:
    phrases = [
        texts.MEDICAL,
        texts.MEDICAL_ASIDE,
        "Антирабической вакцины нет — доктор может обработать место укуса и сделать перевязку.",
        *[line for s in env.kb.scenarios.values() for o in s.outcomes.values() for line in o.say],
    ]
    asked = Action("say", "вывих, перелом или разрыв связок — сколько стоит?")  # he named them
    for said in phrases:
        dialog = made(PRICE, step(1, asked, Shown(said, ("Меню",))))
        assert sim.ADVICE not in rules(dialog, env), said


def test_a_doctor_named_and_lost(env: sim.Env) -> None:
    persona = BY_KEY["doctor_wish"]
    ask = Action("say", "запишите к Ярунину на завтра")
    full = "Ярунин Андрей Александрович"
    unheard = step(1, ask, Shown("С чем обращаетесь?", ("Меню",)), step="complaint", wizard=True)
    found = check(made(persona, unheard), env.kb, env.clinic)
    assert any(v.rule == sim.LOST and "не учёл" in v.detail for v in found)

    heard = step(1, ask, Shown("Пожелание: к Ярунину А. А.", ("Меню",)), step="complaint",
                 wizard=True, doctor=full)  # fmt: skip
    dropped = step(2, Action("say", "болит рука"), Shown("Что случилось?", ("Меню",)),
                   step="topic", wizard=True)  # fmt: skip
    found = check(made(persona, heard, dropped), env.kb, env.clinic)
    assert any(v.rule == sim.LOST and v.turn == 2 for v in found)

    by_button = step(2, Action("press", texts.WISH_CLEAR), Shown("Выберите день", ("Меню",)),
                     step="days", wizard=True)  # fmt: skip
    found = check(made(persona, heard, by_button), env.kb, env.clinic)
    assert not any(v.rule == sim.LOST for v in found)


def test_for_whom_asked_again(env: sim.Env) -> None:
    persona = BY_KEY["child_for"]
    told = step(1, Action("say", "запишите дочку, её укусила собака"),
                Shown("Сколько лет ребёнку?", ("Меню",)), step="age", wizard=True)  # fmt: skip
    asked = step(2, Action("say", "9 лет"), Shown(texts.ASK_AUDIENCE, ("Для себя",)),
                 step="audience", wizard=True)  # fmt: skip
    found = check(made(persona, told, asked), env.kb, env.clinic)
    assert any(v.rule == sim.LOST and "для кого" in v.detail for v in found)
    back = replace(asked, action=Action("press", texts.BACK))
    found = check(made(persona, told, back), env.kb, env.clinic)
    assert not any(v.rule == sim.LOST for v in found)


# --- the report ---------------------------------------------------------------------------------


async def test_report_lists_the_failures_with_transcripts(env: sim.Env) -> None:
    good = await play(BY_KEY["buttons_only"], ScriptedPatient(*BOOK_BY_BUTTONS), env)
    bad = await play(BY_KEY["vague"], ScriptedPatient("болит", "не знаю"), env)
    broken = Dialog(BY_KEY["elder"], sim.FIRST_DAY, [], [], ended="patient", note="нет ответа")
    results = [(d, check(d, env.kb, env.clinic)) for d in (good, bad, broken)]
    report = render_report(results, {"at": "16.11.2026 12:00", "model": "deepseek-chat"})
    assert "**Диалогов: 2. Прошло: 1. С нарушениями: 1.**" in report
    assert "Не состоялось из-за сбоя симулятора: 1." in report
    assert (
        "### `vague` — Орлова Кристина Денисовна" in report and "### `buttons_only`" not in report
    )
    assert f"- **{sim.GOAL}** (диалог в целом)" in report
    assert "1. **Пациент** пишет: «болит»" in report and "[Меню]" in report
    assert f"| {sim.GOAL} | 1 |" in report and "| `buttons_only` |" in report
    assert "Ильина" not in report  # a passed dialog leaves no transcript


def test_the_patient_prompt_states_the_rules_and_the_persona() -> None:
    persona = BY_KEY["move_day"]
    prompt = (
        sim.PATIENT_RULES
        + "\n\n"
        + sim.persona_prompt(persona, [{"date": "13.11", "time": "11:00"}])
    )
    assert '"action": "say" | "press" | "contact" | "stop"' in prompt
    assert "У Вас уже есть запись в травмпункт: 13.11 в 11:00" in prompt
    assert persona.story in prompt and "Только JSON" in prompt


def test_every_full_run_is_kept_under_its_own_number(tmp_path: Path) -> None:
    assert sim.runs(tmp_path) == [] and sim.last_run(tmp_path) is None
    assert sim.next_run(tmp_path) == tmp_path / "simulation.run1.jsonl"
    for name in ("simulation.run1.jsonl", "simulation.run2.jsonl", "simulation.jsonl",
                 "simulation.partial.jsonl", "misses.jsonl"):  # fmt: skip
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert [n for n, _ in sim.runs(tmp_path)] == [1, 2]  # the old single file is not a run
    assert sim.next_run(tmp_path).name == "simulation.run3.jsonl"
    (tmp_path / "simulation.run10.jsonl").write_text("{}", encoding="utf-8")
    assert sim.last_run(tmp_path).name == "simulation.run10.jsonl"  # by the number, not the name
    assert sim.next_run(tmp_path).name == "simulation.run11.jsonl"
    assert sim.run_file("2", tmp_path).name == "simulation.run2.jsonl"
    assert sim.run_file("partial", tmp_path).name == sim.PARTIAL
    assert sim.run_file(None, tmp_path).name == "simulation.run10.jsonl"
    with pytest.raises(SystemExit):
        sim.run_file("7", tmp_path)


async def test_saved_transcripts_are_rechecked_without_deepseek(
    env: sim.Env, tmp_path: Path
) -> None:
    good = await play(BY_KEY["buttons_only"], ScriptedPatient(*BOOK_BY_BUTTONS), env)
    moved = await play(
        BY_KEY["cancel_by_day"],
        ScriptedPatient("отмени запись на воскресенье", ("press", "Да, отменить")),
        env,
    )
    results = [(d, check(d, env.kb, env.clinic)) for d in (good, moved)]
    assert [v for _, v in results] == [[], []]
    raw = tmp_path / "simulation.jsonl"
    sim.save_raw(results, {"at": "сегодня", "model": "deepseek-chat"}, raw)
    again, meta = await sim.recheck(raw, sim.Env(env.kb, env.clinic, None, tmp_path / "again"))
    assert meta == {"at": "сегодня", "model": "deepseek-chat"}
    assert [(d.persona.key, d.ended, v) for d, v in again] == [
        ("buttons_only", "stop", []),
        ("cancel_by_day", "stop", []),
    ]
    assert again[1][0].before == moved.before and again[0][0].steps == good.steps
    assert render_report(again, meta) == render_report(results, meta)


# --- a patient who is due a route (section 5) or an ambulance ---------------------------------

ROUTED = {"head", "infant", "criminal", "needle", "burn_face", "bleeding", *SECTION_5}


def test_the_personas_due_a_route_are_marked() -> None:
    assert {p.key for p in PERSONAS if p.route} == ROUTED


async def test_a_booking_of_a_patient_due_a_route_is_a_violation(env: sim.Env) -> None:
    dialog = await play(BY_KEY["needle"], ScriptedPatient(*BOOK_BY_BUTTONS), env)
    assert len(dialog.steps[-1].bookings) == 1
    found = {v.rule: v for v in check(dialog, env.kb, env.clinic)}
    # booked, and no route was given: the patient's «цель достигнута» does not count
    assert set(found) == {sim.GOAL, sim.ROUTED_BOOKED}
    assert sim.ROUTED_BOOKED == "записан пациент, которому положен маршрут"
    booked = found[sim.ROUTED_BOOKED]
    assert booked.turn == 14 and "13.11" in booked.detail  # the turn the booking was made on
    same = replace(dialog, persona=BY_KEY["buttons_only"])
    assert check(same, env.kb, env.clinic) == []  # the same dialog of a patient who may book


async def test_the_route_and_a_booking_after_it_is_a_violation_too(env: sim.Env) -> None:
    route = (
        ("press", "Записаться в травмпункт"),
        ("press", "Рана, ожог, укус"),
        ("press", "Укол чужой иглой"),
    )
    # after a route «Записаться» begins with «та же или другая?»
    other = (BOOK_BY_BUTTONS[0], ("press", "Другая"), BOOK_BY_BUTTONS[1], BOOK_BY_BUTTONS[2])
    patient = ScriptedPatient(*route, *other, *BOOK_BY_BUTTONS[3:])
    dialog = await play(BY_KEY["needle"], patient, env)
    assert rules(dialog, env) == {sim.ROUTED_BOOKED}


def test_the_rule_is_in_the_report_table() -> None:
    assert sim.ROUTED_BOOKED in sim.RULES


async def test_the_danger_state_is_a_plain_refusal_for_a_patient_due_an_ambulance(
    env: sim.Env,
) -> None:
    patient = ScriptedPatient(
        "срочно! порезал руку сильно, кровь не останавливается, что делать??",
        "да не буду я звонить, мне записаться надо!",
        "скажите хотя бы куда ехать, в какую больницу",
        ("press", "Позвать администратора"),
    )
    dialog = await play(BY_KEY["bleeding"], patient, env)
    assert dialog.steps[0].replies[0].text == texts.DANGER_FIRST
    assert dialog.steps[-1].bookings == [] and check(dialog, env.kb, env.clinic) == []


def test_a_one_line_answer_under_the_same_buttons_is_an_answer(env: sim.Env) -> None:
    """The partial run before run 4: «Самостоятельную запись с 15 до 18 лет я оформить не
    могу…» under the buttons of the screen was counted as the screen shown again."""
    buttons = (texts.TEEN_PARENT, texts.MENU)
    screen = Shown("Вам 16 лет. Правила самостоятельной записи уточнит администратор.", buttons)
    before = step(1, Action("say", "09.09.2010"), screen, step="teen", wizard=True)
    asked = Action("say", "а че, сам не могу записаться? мне 16 уже")
    answer = step(2, asked, Shown(texts.TEEN_AGAIN, buttons), step="teen", wizard=True)
    assert sim.UNANSWERED not in rules(made(BY_KEY["teen16"], before, answer), env)
    # the screen once more with another header line is still no answer
    days = Shown("Шаг 4 из 5 · День и время\n\nВыберите, пожалуйста, день приёма:", ("Завтра",))
    again = Shown(
        "Шаг 4 из 5 · День и время\nПожелание: завтра\n\nВыберите, пожалуйста, день приёма:",
        ("Завтра",),
    )
    first = step(1, Action("press", "x"), days, step="days", wizard=True)
    shown = step(2, Action("say", "а сколько стоит приём?"), again, step="days", wizard=True)
    assert sim.UNANSWERED in rules(made(BY_KEY["questions"], first, shown), env)


# --- every scenario of section 5 has a patient ---------------------------------------------------


def test_every_scenario_of_section_5_has_a_persona(env: sim.Env) -> None:
    section = {s.id for s in env.kb.scenarios.values() if s.number.startswith("5.")}
    played = {p.scenario for p in PERSONAS if p.scenario}
    assert played == section and len(section) == 10
    for persona in PERSONAS:
        if persona.scenario:
            assert persona.route and persona.goal == "book", persona.key
    assert len([p for p in PERSONAS if p.scenario == "needle_injury"]) == 2  # two wordings
    assert len([p for p in PERSONAS if p.scenario == "head_trauma"]) == 2
    assert set(SECTION_5) == {p.key for p in PERSONAS if p.scenario} and len(SECTION_5) == 12


def test_the_stories_of_section_5_have_no_emergency_phrase(env: sim.Env) -> None:
    """«Трудно дышать» in a story would play the «угроза жизни» state, not the route."""
    from app.flow.safety import find_phrase

    phrases = env.clinic.clinic.emergency.phrases
    for persona in PERSONAS:
        if persona.scenario:
            assert find_phrase(persona.story, phrases) is None, persona.key


def test_for_whom_asked_before_a_route_is_not_lost(env: sim.Env) -> None:
    """The customer's decision: «для кого» is asked again when the route depends on it."""
    told = step(1, Action("say", "я упала грудью на край ванны, болят рёбра"),
                Shown("У меня нет этой информации."))  # fmt: skip
    asked = step(2, Action("press", "Удар в грудь или живот"),
                 Shown(f"Шаг 2 из 5\n\n{texts.ASK_AUDIENCE}", ("Для себя",)),
                 step="audience", wizard=True)  # fmt: skip
    routed = step(3, Action("press", "Для себя"), Shown("Вам нужно обратиться: …", ("Меню",)),
                  outcome="route")  # fmt: skip
    assert sim.LOST not in rules(made(BY_KEY["chest"], told, asked, routed), env)
    booked_on = step(3, Action("press", "Для себя"), Shown("Шаг 3 из 5", ("Да",)),
                     step="question", wizard=True)  # fmt: skip
    assert sim.LOST in rules(made(BY_KEY["chest"], told, asked, booked_on), env)


# --- a booking deleted in the dialog; a check that fails ----------------------------------------

BOOKED = {"id": 1, "status": "active", "date": "13.11", "day": "2026-11-13", "time": "11:00",
          "doctor": "Ярунин Андрей Александрович", "visit": "primary", "patient_id": 1,
          "audience": "adult"}  # fmt: skip
MENU_SHOWN = Shown("Ваши данные удалены.", ("Записаться в травмпункт",))


def with_a_booking(key: str, *steps: Step) -> Dialog:
    dialog = made(BY_KEY[key], *steps)
    dialog.before = [dict(BOOKED)]
    return dialog


def test_a_booking_deleted_with_the_data_is_not_kept(env: sim.Env) -> None:
    """Full run 6 (15.11): a patient who came to cancel had his data deleted with /forget, the
    booking was no longer in the base, and the check of the goal fell with KeyError — with it
    the whole run of 58 dialogs. A booking that is gone is cancelled, not kept."""
    last = step(2, Action("press", "Да, удалить"), MENU_SHOWN)  # no bookings are left
    assert sim._goal(with_a_booking("cancel_rude", last), env.clinic) is None
    kept = with_a_booking("move_then_keep", last)
    assert sim._goal(kept, env.clinic) == "запись не осталась как была"
    assert sim._goal(with_a_booking("move_day", last), env.clinic) == "запись не перенесена"


async def test_a_check_that_fails_does_not_lose_the_run(env: sim.Env, monkeypatch) -> None:
    def failing(dialog: Dialog, *args: object) -> list:
        if dialog.persona.key == "elder":
            raise KeyError(1)
        return []

    monkeypatch.setattr(sim, "check", failing)
    seen: list[str] = []
    personas = [BY_KEY["elder"], BY_KEY["three_words"]]
    results = await sim.run(
        personas, ScriptedPatient, env, on_done=lambda d, v: seen.append(d.persona.key)
    )
    by_key = {dialog.persona.key: (dialog, violations) for dialog, violations in results}
    assert set(by_key) == set(seen) == {"elder", "three_words"}  # both are in the results
    broken, violations = by_key["elder"]
    assert broken.ended == "patient" and "KeyError" in broken.note and violations == []
    assert by_key["three_words"][0].ended != "patient"


async def test_the_dialogs_are_saved_while_the_run_goes_on(env: sim.Env, tmp_path) -> None:
    done = [(made(BY_KEY["elder"], step(1, Action("say", "Здравствуйте"), MENU_SHOWN)), [])]
    path = tmp_path / sim.UNFINISHED
    sim.save_raw(done, {"at": "15.11.2026 21:00 (Екатеринбург)", "unfinished": True}, path)
    assert sim.run_file("unfinished", tmp_path) == path
    results, meta = await sim.recheck(path, env)
    assert [d.persona.key for d, _ in results] == ["elder"] and meta["unfinished"]


# --- whose visit is touched and who is booked (the round «кого и куда записать») ----------------

MOTHER, SON = "Корнилова Елена Викторовна", "Корнилов Тимур Денисович"


def visit(n: int, patient: str, day: str = "2026-11-13", at: str = "11:00", **changes) -> dict:
    """A booking as the simulator keeps it; ``patient`` — the full name it is made for."""
    base = {"id": n, "status": "active", "date": f"{day[8:]}.{day[5:7]}", "day": day, "time": at,
            "doctor": "Ярунин Андрей Александрович", "visit": "primary", "patient_id": n,
            "audience": "adult", "patient": patient}  # fmt: skip
    return {**base, **changes}


def ends_with(key: str, before: list[dict], *after: list[dict]) -> Dialog:
    """A dialog of the persona: the visits before it and after each of its turns."""
    steps = [
        Step(n, Action("say", "…"), [Shown("Готово.", ("Меню",))], state(), list(now), 0)
        for n, now in enumerate(after, 1)
    ]
    dialog = made(BY_KEY[key], *steps)
    dialog.before = before
    return dialog


def touched(dialog: Dialog) -> list[str]:
    return [f"{v.turn}: {v.detail}" for v in sim._touched(dialog)]


def test_the_new_rule_stands_next_to_the_gravest_one() -> None:
    assert sim.TOUCHED == "создана, изменена или отменена запись, о которой пациент не просил"
    assert sim.RULES.index(sim.TOUCHED) == sim.RULES.index(sim.ROUTED_BOOKED) + 1
    assert "several" in sim.GOALS


def test_the_new_personas_say_what_visits_the_dialog_ends_with() -> None:
    wanted = {
        "book_then_son": (("self", "kept"), ("child", "new")),
        "not_husband": (("self", "kept"),),
        "all_in_one_dob": (("self", "new"),),
        "move_and_book": (("self", "moved"), ("child", "new")),
        "cancel_and_swap": (("self", "cancelled"), ("other", "new")),
        "two_moves": (("other", "cancelled"), ("self", "moved")),
    }
    said = {p.key: p.ends for p in PERSONAS if p.ends}  # certificate: no visit is the end
    assert said == wanted
    for key in wanted:  # the first message is the one the customer wrote on his phone
        assert "первым сообщением пишет ровно: «" in BY_KEY[key].manner


def test_the_mothers_visit_moved_for_the_sons_complaint(env: sim.Env) -> None:
    """The customer's phone, 15.11: «Сын 8 лет упал с велосипеда…» moved the mother's visit to
    19:00, and the son was not booked."""
    before = [visit(1, MOTHER)]
    dialog = ends_with("book_then_son", before, [visit(1, MOTHER, "2026-11-12", "19:00")])
    assert touched(dialog) == ["1: своя запись перенесена: с 13.11, 11:00 на 12.11, 19:00"]
    assert sim._goal(dialog, env.clinic) == (
        "итог не тот. Нужно: своя запись — без изменений; запись ребёнка — новая. "
        "Получилось: своя запись — перенесена"
    )
    assert {sim.GOAL, sim.TOUCHED} <= rules(dialog, env)


def test_the_son_is_booked_and_the_mothers_visit_stays(env: sim.Env) -> None:
    before = [visit(1, MOTHER)]
    son = visit(2, SON, "2026-11-12", "19:00", audience="child")
    dialog = ends_with("book_then_son", before, before, [*before, son])
    assert touched(dialog) == [] and sim._goal(dialog, env.clinic) is None


def test_a_son_who_is_not_booked_is_a_goal_missed_and_nothing_graver(env: sim.Env) -> None:
    before = [visit(1, MOTHER)]
    dialog = ends_with("book_then_son", before, before)
    assert touched(dialog) == []
    assert "Получилось: своя запись — без изменений" in (sim._goal(dialog, env.clinic) or "")


def test_a_visit_moved_and_moved_back_was_touched_all_the_same() -> None:
    before = [visit(1, MOTHER)]
    moved = [visit(1, MOTHER, "2026-11-12", "19:00")]
    dialog = ends_with("book_then_son", before, before, moved, before)
    assert touched(dialog) == ["2: своя запись перенесена: с 13.11, 11:00 на 12.11, 19:00"]


def test_a_husband_nobody_asked_to_book(env: sim.Env) -> None:
    before = [visit(1, "Белоусова Марина Андреевна")]
    husband = visit(2, "Белоусов Олег Петрович", "2026-11-14", "10:00")
    dialog = ends_with("not_husband", before, before, [*before, husband])
    assert touched(dialog) == [
        "2: создана запись, о которой не просили: Белоусов Олег Петрович, 14.11, 10:00"
    ]
    assert sim.TOUCHED in rules(dialog, env)
    kept = ends_with("not_husband", before, before)
    assert touched(kept) == [] and sim._goal(kept, env.clinic) is None


def test_the_wifes_visit_cancelled_instead_of_the_husbands(env: sim.Env) -> None:
    wife, husband = "Фролова Екатерина Дмитриевна", "Фролов Дмитрий Олегович"
    before = [visit(1, wife), visit(2, husband, "2026-11-15", "11:00")]
    wrong = [visit(1, wife, status="cancelled"), visit(2, husband, "2026-11-15", "11:00")]
    dialog = ends_with("two_moves", before, wrong)
    assert touched(dialog) == ["0: своя запись отменена: 13.11, 11:00 (просили перенести)"]
    right = [visit(1, wife, at="19:00"), visit(2, husband, "2026-11-15", "11:00",
                                              status="cancelled")]  # fmt: skip
    done = ends_with("two_moves", before, right)
    assert touched(done) == [] and sim._goal(done, env.clinic) is None


def test_a_visit_given_to_the_husband(env: sim.Env) -> None:
    wife, husband = "Мельникова Светлана Юрьевна", "Мельников Андрей Павлович"
    before = [visit(1, wife)]
    right = [visit(1, wife, status="cancelled"), visit(2, husband)]
    done = ends_with("cancel_and_swap", before, right)
    assert touched(done) == [] and sim._goal(done, env.clinic) is None
    # the husband is booked twice: one visit too many
    twice = ends_with("cancel_and_swap", before, [*right, visit(3, husband, at="11:30")])
    assert touched(twice) == ["0: лишняя запись: Мельников Андрей Павлович, 13.11, 11:30"]
    # a name that is nobody's in the story
    stranger = ends_with("cancel_and_swap", before, [before[0], visit(2, "Иванов Пётр Ильич")])
    assert touched(stranger) == [
        "1: создана запись, о которой не просили: Иванов Пётр Ильич, 13.11, 11:00"
    ]


def test_the_old_personas_are_under_the_rule_too(env: sim.Env) -> None:
    """Whose visit may change follows from the goal; a saved run has no patient's name."""
    cancelled = with_a_booking("move_then_keep", Step(
        1, Action("say", "…"), [MENU_SHOWN], state(), [{**BOOKED, "status": "cancelled"}], 0
    ))  # fmt: skip
    assert touched(cancelled) == ["1: своя запись отменена: 13.11, 11:00"]
    moved = with_a_booking("cancel_plain", Step(
        1, Action("say", "…"), [MENU_SHOWN], state(), [{**BOOKED, "time": "12:00"}], 0
    ))  # fmt: skip
    assert touched(moved) == [
        "0: своя запись перенесена: с 13.11, 11:00 на 13.11, 12:00 (просили отменить)"
    ]
    asked = with_a_booking("move_day", Step(
        1, Action("say", "…"), [MENU_SHOWN], state(), [{**BOOKED, "day": "2026-11-15"}], 0
    ))  # fmt: skip
    assert touched(asked) == []  # the move he came for


async def test_the_visits_before_the_dialog_are_whose_the_story_says(env: sim.Env) -> None:
    dialog = await play(BY_KEY["two_moves"], ScriptedPatient(("press", "Мои записи")), env)
    assert [(b["patient"], b["date"]) for b in dialog.before] == [
        ("Фролова Екатерина Дмитриевна", "13.11"),
        ("Фролов Дмитрий Олегович", "15.11"),
    ]
    prompt = sim.persona_prompt(BY_KEY["two_moves"], dialog.before)
    assert "13.11 в " in prompt and "(Ваша)" in prompt
    assert "(пациент — Фролов Дмитрий Олегович)" in prompt
    assert "Взрослый, которого записываете" not in prompt
    own = await play(BY_KEY["move_and_book"], ScriptedPatient(("press", "Мои записи")), env)
    assert [(b["patient"], b["date"]) for b in own.before] == [
        ("Тарасова Ольга Николаевна", "15.11")
    ]
    assert touched(dialog) == [] == touched(own)


async def test_the_son_booked_by_buttons_passes_every_rule(env: sim.Env) -> None:
    patient = ScriptedPatient(
        ("press", "Записаться в травмпункт"),
        ("press", "Рука или нога"),
        ("press", "Похоже на вывих"),
        ("press", "Для ребёнка (до 18 лет)"),
        "8 лет",
        ("press", "Бытовая травма"),
        ("press", "В течение 7 дней"),
        ("press", "Нет, не обращались"),
        ("press", "Сегодня, 12.11"),
        ("press", "Вечер 18–23"),
        ("press", "19:00"),
        ("press", "Согласен"),
        SON,
        "21.04.2018",
        ("press", "Самостоятельно"),
    )
    dialog = await play(BY_KEY["book_then_son"], patient, env)
    assert [(b["patient"], b["status"]) for b in dialog.steps[-1].bookings] == [
        (MOTHER, "active"),
        (SON, "active"),
    ]
    assert check(dialog, env.kb, env.clinic) == []


def test_a_name_and_a_birth_date_given_at_once_and_asked_again(env: sim.Env) -> None:
    persona = BY_KEY["all_in_one_dob"]
    first = "Запишите на завтра утром, порезал палец. Родионов Иван Сергеевич, 12.03.1985"
    told = step(1, Action("say", first), Shown("Шаг 3 из 5", ("Да", "Меню")), wizard=True)
    whom = Shown(f"Шаг 2 из 5\n\n{texts.ASK_AUDIENCE}", ("Для себя",))
    asked = step(2, Action("press", "Да"), whom, wizard=True)
    found = [v.detail for v in check(made(persona, told, asked), env.kb, env.clinic)
             if v.rule == sim.LOST]  # fmt: skip
    assert found == ["бот спросил «для кого», хотя ФИО и дата рождения названы"]
    name = step(2, Action("press", "Да"), Shown(f"Шаг 5 из 5\n\n{texts.ASK_NAME}", ("Меню",)),
                wizard=True)  # fmt: skip
    found = [v.detail for v in check(made(persona, told, name), env.kb, env.clinic)
             if v.rule == sim.LOST]  # fmt: skip
    assert found == ["бот спросил ФИО, хотя ФИО и дата рождения названы"]
    fine = step(2, Action("press", "Да"), Shown("Пациент: Родионов Иван Сергеевич", ("Меню",)),
                wizard=True)  # fmt: skip
    assert sim.LOST not in rules(made(persona, told, fine), env)
    # on the consent screen the bot waits for the button: the data typed there is another story
    consent = step(1, Action("press", "19:00"), Shown("Согласие…", ("Согласен",)), wizard=True,
                   step="consent")  # fmt: skip
    typed = step(2, Action("say", "Родионов Иван Сергеевич, 12.03.1985"),
                 Shown("Чтобы продолжить, нажмите «Согласен»", ("Согласен",)), wizard=True,
                 step="consent")  # fmt: skip
    name = step(3, Action("press", "Согласен"), Shown(texts.ASK_NAME, ("Меню",)), wizard=True)
    assert sim.LOST not in rules(made(persona, consent, typed, name), env)
    # the mother's own name does not answer the question about the child's
    mother = replace(persona, child=("Родионова Анна Ивановна", "01.02.2019"))
    name = step(2, Action("press", "Да"), Shown(texts.ASK_NAME, ("Меню",)), wizard=True)
    assert sim.LOST not in rules(made(mother, told, name), env)


# --- what run 7 showed only to a reader: three rules more, and a quote that is not advice --------

ROUTE = Shown("В травмпункте принимают только травмы рук и ног.\nВам нужно обратиться:\n"
              "📍 Центр борьбы со СПИДом, ул. Ясная, 46", ("Меню",))  # fmt: skip
NOT_HERE = Shown("С этим в травмпункт не записываем.\nОбратиться нужно:\n"
                 "📍 Центр борьбы со СПИДом, ул. Ясная, 46", ("В главное меню",))  # fmt: skip
TOPICS = Shown("Шаг 1 из 5 · Что случилось\n\nЧто случилось с рукой или ногой?", ("Другое",))
NEEDLE_SAID = "Я сегодня во дворе наступила на иглу от шприца, проколола стопу. Это опасно?"


def routed(n: int = 1, said: str = NEEDLE_SAID) -> Step:
    return step(n, Action("say", said), ROUTE, outcome="route")


def test_the_three_rules_are_in_the_report_table() -> None:
    assert sim.INVITES == "после маршрута бот зовёт в наш травмпункт"
    assert sim.CIRCLE == "один и тот же ответ три раза подряд"
    assert sim.LATE_ROUTE == "маршрут пришёл не в ответ на первое сообщение с жалобой"
    assert {sim.INVITES, sim.CIRCLE, sim.LATE_ROUTE} <= set(sim.RULES)


@pytest.mark.parametrize(
    "answer",
    [
        "Да, с раной на стопе в травмпункт записывают: принимаем скелетно-костные травмы. "
        "Записаться можно в этом чате кнопкой «Записаться в травмпункт».",
        "Травмпункт «УГМК-Здоровье» находится по адресу: ул. Шейнкмана, 113/2.",
        "Травмпункт работает круглосуточно по предварительной записи, поэтому ночью приём "
        "тоже возможен.",
        "В травмпункте принимает травматолог-ортопед: он осмотрит рану.",
        "Ожоги мы принимаем, кроме химических.",
    ],
)
def test_after_a_route_the_bot_calls_to_our_centre(env: sim.Env, answer: str) -> None:
    invited = step(2, Action("say", "а рану мне посмотрят?"), Shown(answer, ("Меню",)))
    found = [v for v in check(made(BY_KEY["needle"], routed(), invited), env.kb, env.clinic)
             if v.rule == sim.INVITES]  # fmt: skip
    assert [v.turn for v in found] == [2]


def test_the_route_itself_and_another_injury_are_not_a_call_to_us(env: sim.Env) -> None:
    again = step(2, Action("say", "а к вам можно?"), NOT_HERE)
    assert sim.INVITES not in rules(made(BY_KEY["needle"], routed(), again), env)
    # «Другая»: the talk is about another injury — the reference answers about us again
    other = step(2, Action("press", "Другая"), TOPICS, wizard=True, step="complaint")
    hours = step(3, Action("say", "а вы круглосуточно работаете?"),
                 Shown("Травмпункт работает круглосуточно."), TOPICS, wizard=True)  # fmt: skip
    assert sim.INVITES not in rules(made(BY_KEY["needle"], routed(), other, hours), env)
    # before any route the reference may say it
    first = step(1, Action("say", "а вы круглосуточно работаете?"),
                 Shown("Травмпункт работает круглосуточно.", ("Меню",)))  # fmt: skip
    assert sim.INVITES not in rules(made(BY_KEY["needle"], first, routed(2)), env)


def circle(*turns: tuple[str, tuple[Shown, ...]], **state: object) -> Dialog:
    steps = [step(n, Action("say", said), *shown, **state) for n, (said, shown) in
             enumerate(turns, 1)]  # fmt: skip
    return made(BY_KEY["frostbite"], *steps)


def test_the_same_answer_three_times_in_a_row(env: sim.Env) -> None:
    line = [Shown(f"строка {n}") for n in range(3)]
    thrice = circle(("отморозил пальцы", (TOPICS,)), ("это отморожение", (line[1], TOPICS)),
                    ("да я уже писал", (line[2], TOPICS)), wizard=True, step="topic")  # fmt: skip
    found = [v for v in check(thrice, env.kb, env.clinic) if v.rule == sim.CIRCLE]
    assert [v.turn for v in found] == [3] and "Что случилось с рукой или ногой?" in found[0].detail
    twice = circle(("отморозил пальцы", (TOPICS,)), ("это отморожение", (line[1], TOPICS)),
                   wizard=True, step="topic")  # fmt: skip
    assert sim.CIRCLE not in rules(twice, env)


def test_a_new_answer_to_a_new_question_is_not_a_circle(env: sim.Env) -> None:
    answers = [Shown(f"Ответ {n}.") for n in range(3)]
    questions = ("а где вы?", "а парковка есть?", "а сколько стоит?")
    turns = [(asked, (answers[n], TOPICS)) for n, asked in enumerate(questions)]
    asked = circle(*turns, wizard=True, step="topic")
    assert sim.CIRCLE not in rules(asked, env)
    # the same answer to each of them is one
    turns = [(asked, (answers[0], TOPICS)) for asked in questions]
    same = circle(*turns, wizard=True, step="topic")
    assert sim.CIRCLE in rules(same, env)


def test_what_repeats_by_design_is_not_a_circle(env: sim.Env) -> None:
    urgency = Shown(texts.URGENCY)
    asks = circle(*[("скорую вызывать?", (urgency,))] * 4)
    assert sim.CIRCLE not in rules(asks, env)
    offtopic = circle(*[("расскажи анекдот", (Shown(texts.OFFTOPIC, ("Меню",)),))] * 3)
    assert sim.CIRCLE not in rules(offtopic, env)


def test_a_button_in_between_breaks_the_circle_but_for_step_one(env: sim.Env) -> None:
    said = [step(n, Action("say", f"отморозил пальцы {n}"), TOPICS, wizard=True, step="topic")
            for n in (1, 3, 5)]  # fmt: skip
    other = [step(n, Action("press", "Другое"), Shown("Опишите своими словами", ("Назад",)),
                  wizard=True, step="describe") for n in (2, 4)]  # fmt: skip
    steps = [said[0], other[0], said[1], other[1], said[2]]
    assert sim.CIRCLE in rules(made(BY_KEY["frostbite"], *steps), env)
    menu = [step(n, Action("press", "Мои записи"), Shown("У Вас нет предстоящих записей."))
            for n in (2, 4)]  # fmt: skip
    empty = Shown("У Вас нет предстоящих записей.", ("Записаться в травмпункт",))
    typed = [step(n, Action("say", "что с записью"), empty) for n in (1, 3, 5)]
    steps = [typed[0], menu[0], typed[1], menu[1], typed[2]]
    assert sim.CIRCLE not in rules(made(BY_KEY["frostbite"], *steps), env)


def test_the_route_did_not_come_to_the_first_complaint(env: sim.Env) -> None:
    """Run 7, needle: the first message got «Оценить состояние я не могу» and the menu."""
    template = step(
        1, Action("say", NEEDLE_SAID), Shown(texts.URGENCY, ("Записаться в травмпункт",))
    )
    pressed = step(2, Action("press", "Записаться в травмпункт"), TOPICS, wizard=True)
    late = made(BY_KEY["needle"], template, pressed, routed(3))
    found = [v for v in check(late, env.kb, env.clinic) if v.rule == sim.LATE_ROUTE]
    assert [v.turn for v in found] == [1]
    assert sim.LATE_ROUTE not in rules(made(BY_KEY["needle"], routed()), env)
    # a greeting with no complaint in it is not the first complaint
    hello = step(1, Action("say", "Здравствуйте, можно вопрос?"), Shown("Да, конечно.", ("Меню",)))
    assert sim.LATE_ROUTE not in rules(made(BY_KEY["needle"], hello, routed(2)), env)
    # the scenario may ask first — «для кого», the age: the booking flow has begun
    age = Shown("Шаг 2 из 5\n\nСколько лет ребёнку?", ("Меню",))
    asks = step(1, Action("say", "сын упал с дивана, ему 4 месяца, плакал"), age,
                wizard=True, step="age")  # fmt: skip
    assert sim.LATE_ROUTE not in rules(made(BY_KEY["infant"], asks, routed(2, "4 месяца")), env)
    # a patient who is to be booked is not under the rule
    assert sim.LATE_ROUTE not in rules(made(BY_KEY["buttons_only"], template), env)


def test_the_patients_words_quoted_to_the_administrator_are_not_advice(env: sim.Env) -> None:
    """Partial run of 15.11, burn_face: «Администратору передано: Ваше сообщение «…нужно к
    врачу… Примите нас пожалуйста…»» was read as «примите» — the bot's advice."""
    asked = "Так мне что, к вам нельзя записаться? Нам срочно нужно к врачу! Примите нас пожалуйста"
    first = step(1, Action("say", asked), NOT_HERE)
    passed = texts.HANDOFF_NOTHING_NEW.format(
        what=texts.PASSED_MESSAGE.format(text=f"{asked}\nА э…"), when=""
    )
    quoted = step(2, Action("say", "да или нет?"), Shown(passed, ("Меню",)))
    assert sim.ADVICE not in rules(made(BY_KEY["burn_face"], first, quoted), env)
    advice = step(2, Action("say", "да или нет?"), Shown("Примите обезболивающее.", ("Меню",)))
    assert sim.ADVICE in rules(made(BY_KEY["burn_face"], first, advice), env)


def test_a_patient_with_an_old_injury_is_not_to_be_booked(env: sim.Env) -> None:
    """Run 8, certificate: a sprain of three years ago was booked for today."""
    persona = BY_KEY["certificate"]
    assert persona.ends == ()
    booked = ends_with("certificate", [], [], [visit(1, persona.name, "2026-11-12", "13:30")])
    assert {sim.GOAL, sim.TOUCHED} <= rules(booked, env)
    assert touched(booked) == [
        "2: создана запись, о которой не просили: Хасанова Эльвира Наилевна, 12.11, 13:30"
    ]
    asked = ends_with("certificate", [], [])
    assert sim._goal(asked, env.clinic) is None and touched(asked) == []


# --- the goal by the base; what repeats by design (the tidying after run 8) ----------------------

CARD = Shown("✅ Запись в травмпункт\nГригорий Аркадьевич записан на приём…", ("Перенести",))


def test_a_patient_to_be_booked_reaches_the_goal_only_with_a_visit(env: sim.Env) -> None:
    """Run 8, text_only: the dialog ended with «Запись не оформлена. Я передал обращение…» —
    asked for by the patient himself — and was counted as a goal reached."""
    persona = BY_KEY["text_only"]
    passed = "Запись не оформлена.\n\nЯ передал обращение № 1 администратору — он свяжется с Вами."
    asked = Shown(passed, ("Продолжить запись", "Меню"))
    stuck = step(1, Action("say", "тринадцать тридцать"), Shown(texts.HINT_TIME), wizard=True)
    handed = Step(2, Action("say", "позовите администратора"), [asked], state(), [], 1)
    dialog = made(persona, stuck, handed)
    assert sim._goal(dialog, env.clinic) == "записи в базе нет, и сценарий в ней не отказывал"
    # «У меня нет этой информации» to a side question is not a refusal to book either
    side = step(1, Action("say", "а парковка есть?"), Shown(texts.INFO_UNKNOWN, ("Меню",)))
    assert sim._goal(made(persona, side), env.clinic) is not None
    booked = Step(2, Action("press", "13:30"), [CARD], state(), [visit(1, persona.name)], 0)
    assert sim._goal(made(persona, stuck, booked), env.clinic) is None


def test_what_the_scenario_itself_refuses_is_a_goal_reached(env: sim.Env) -> None:
    persona = BY_KEY["text_only"]
    by_script = Step(1, Action("press", "Для себя"),
                     [Shown("С такой ситуацией взрослых записывает оператор…", ("Меню",))],
                     state(outcome="handoff"), [], 1)  # fmt: skip
    assert sim._goal(made(persona, by_script), env.clinic) is None
    line = texts.TEEN.format(age="16 лет", passed=texts.TEEN_PASSED)
    teen = Shown(line, ("Меню",))
    minor = Step(1, Action("say", "02.02.2010"), [teen], state(), [], 1)
    assert sim._goal(made(BY_KEY["teen16"], minor), env.clinic) is None
    old = Shown(texts.OLD_INJURY.format(days=7), ("Передать вопрос администратору",))
    assert (
        sim._goal(made(persona, step(1, Action("press", "Я предполагаю"), old)), env.clinic) is None
    )


def test_lines_that_repeat_by_design_are_not_the_same_screen_twice(env: sim.Env) -> None:
    municipal = env.kb.facilities["municipal_trauma_center"].no_address
    lines = (
        texts.URGENCY,
        f"{municipal}\n\nОбратиться нужно:\n📍 Любой муниципальный травмпункт",
        f"{texts.MY_ONE}\n13 ноября (пятница) в 11:00 — Лукина Ж. В.",
    )
    for line in lines:
        shown = Shown(line, ("Меню",))
        twice = made(BY_KEY["questions"], step(1, Action("say", "а?"), shown),
                     step(2, Action("say", "а всё-таки?"), shown))  # fmt: skip
        assert sim.REPEAT not in rules(twice, env), line
    other = Shown("Выберите, пожалуйста, вариант кнопкой.", ("Меню",))
    twice = made(BY_KEY["questions"], step(1, Action("say", "а?"), other),
                 step(2, Action("say", "а всё-таки?"), other))  # fmt: skip
    assert sim.REPEAT in rules(twice, env)
