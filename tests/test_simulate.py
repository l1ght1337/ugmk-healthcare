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


def test_personas_are_forty_to_sixty_and_made_up() -> None:
    assert 40 <= len(PERSONAS) <= 60
    assert len(BY_KEY) == len(PERSONAS) == len({p.name for p in PERSONAS})
    assert all(p.phone.startswith("+7900") for p in PERSONAS)  # a range no operator uses
    goals = {p.goal for p in PERSONAS}
    assert goals == {"book", "move", "keep", "cancel", "price", "ask", "two", "unknown"}
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
    assert rules(dialog, env) == {sim.GOAL}  # the Sunday one was to be cancelled


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
    other = (BOOK_BY_BUTTONS[0], BOOK_BY_BUTTONS[1], BOOK_BY_BUTTONS[2], ("press", "Другая"))
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
