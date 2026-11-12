"""The knowledge base built from kb/text must be complete, consistent and not invented."""

import itertools
from datetime import time
from pathlib import Path

import pytest

from app.config import ClinicFlags, get_clinic_data
from app.flow.loader import KB_DIR, KnowledgeBase, load_kb, verify_sources
from app.flow.schema import AUDIENCES, Branch, Scenario


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return load_kb()


SCENARIO_IDS = sorted(load_kb().scenarios)


def test_every_section_4_and_5_script_has_a_scenario(kb: KnowledgeBase) -> None:
    scripts = {p.stem for p in (KB_DIR / "text").glob("0[45]-*.md")}
    assert scripts == {s.script for s in kb.scenarios.values()}
    assert len(kb.scenarios) == 21


def test_kinds(kb: KnowledgeBase) -> None:
    booking = {s.number for s in kb.scenarios.values() if s.kind == "booking"}
    assert booking == {"4.1", "4.2", "4.3", "4.5", "4.6", "4.7", "4.8", "4.9", "4.11", "4.12"}


def test_places_and_addresses_are_quoted_from_scripts(kb: KnowledgeBase) -> None:
    assert verify_sources(kb) == []


def _combinations(scenario: Scenario, branches: tuple[Branch, ...], own_fields: list[str]):
    fields = scenario.fields()
    keys = sorted({k for b in branches for k in b.when} | set(own_fields))
    domains = [AUDIENCES if k == "audience" else fields[k].values() for k in keys]
    for values in itertools.product(*domains):
        yield dict(zip(keys, values, strict=True))


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_branches_cover_every_answer(kb: KnowledgeBase, scenario_id: str) -> None:
    """For any audience and any allowed answer some branch applies — no dead ends."""
    sc = kb.scenario(scenario_id)
    for facts in _combinations(sc, sc.entry, []):
        assert any(b.matches(facts) for b in sc.entry), (sc.id, "entry", facts)
    for q in sc.questions:
        own = [f.name for f in q.fields]
        for facts in _combinations(sc, q.branches, own):
            assert any(b.matches(facts) for b in q.branches), (sc.id, q.id, facts)


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_branches_only_use_own_fields_and_audience(kb: KnowledgeBase, scenario_id: str) -> None:
    sc = kb.scenario(scenario_id)
    for q in sc.questions:
        own = {f.name for f in q.fields} | {"audience"}
        for b in q.branches:
            assert set(b.when) <= own, (sc.id, q.id, b.when)
    for b in sc.entry:
        assert set(b.when) <= {"audience"}


def test_assault_leads_to_criminal_trauma(kb: KnowledgeBase) -> None:
    for sid in ("dislocation", "fracture", "ligament_rupture"):
        sc = kb.scenario(sid)
        branch = next(b for b in sc.question("how").branches if b.when.get("assault") is True)
        assert branch.scenario == "criminal_trauma"


def test_already_treated_here_leads_to_repeat_visit(kb: KnowledgeBase) -> None:
    """Customer rule: seen in our trauma centre for this injury → 04-09."""
    for sid in ("dislocation", "fracture", "burns", "animal_bite"):
        branch = next(
            b
            for b in kb.scenario(sid).question("seen_doctor").branches
            if b.when.get("seen_doctor") == "our_trauma_center"
        )
        assert branch.scenario == "repeat_visit"
        assert branch.carry == {"first_visit_here": True}


def test_repeat_visit_slot_rules(kb: KnowledgeBase) -> None:
    sc = kb.scenario("repeat_visit")
    q = sc.question("sick_leave")
    policy = {
        b.when["sick_leave_open"]: sc.outcomes[b.outcome].booking.slot_policy for b in q.branches
    }
    assert policy == {True: "near_repeat_window", False: "repeat_window"}


def test_price_keys_exist_in_prices_yaml(kb: KnowledgeBase) -> None:
    prices = get_clinic_data().prices
    for sc in kb.scenarios.values():
        for outcome in sc.outcomes.values():
            if outcome.booking:
                assert outcome.booking.price_key in prices, (sc.id, outcome.booking.price_key)


def test_notice_flags_exist_in_clinic_yaml(kb: KnowledgeBase) -> None:
    for sc in kb.scenarios.values():
        for notice in sc.notices:
            assert set(notice.when_config) <= set(ClinicFlags.model_fields), sc.id


def test_handoffs_name_who_and_what(kb: KnowledgeBase) -> None:
    for sc in kb.scenarios.values():
        for name, outcome in sc.outcomes.items():
            if outcome.handoff_reason:
                assert outcome.handoff_reason.startswith(("Ребёнок", "Взрослый")), (sc.id, name)


def test_booking_summaries_have_all_details(kb: KnowledgeBase) -> None:
    for sc in kb.scenarios.values():
        if sc.kind == "booking":
            for placeholder in ("{patient}", "{date}", "{time}", "{price}", "{address}"):
                assert placeholder in sc.summary, (sc.id, placeholder)
            assert "вход через детское отделение" in sc.summary
            assert "самостоятельно или по страховке" in sc.summary


def _patient_texts(kb: KnowledgeBase) -> list[str]:
    texts: list[str] = []
    for sc in kb.scenarios.values():
        texts += [sc.summary or "", *(n.text for n in sc.notices)]
        texts += [p.text for p in sc.operator_phrases.values()]
        texts += [q.ask for q in sc.questions]
        texts += [line for o in sc.outcomes.values() for line in o.say]
    common = kb.common.model_dump()
    texts += [v["text"] for v in common.values() if isinstance(v, dict) and "text" in v]
    return texts


def test_no_price_gaps_copied_into_patient_texts(kb: KnowledgeBase) -> None:
    """Script gaps like «(…..) рублей» never reach the patient; prices come from config."""
    for text in _patient_texts(kb):
        assert "…..)" not in text and "(ВРЕМЯ)" not in text and "(АДРЕС)" not in text, text
    for path in (KB_DIR / "info").glob("*.md"):
        body = path.read_text(encoding="utf-8").replace("«(…..) рублей»", "")
        assert "…..)" not in body, path.name


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (time(4, 59), "Доброй ночи"),
        (time(5, 0), "Доброе утро"),
        (time(11, 59), "Доброе утро"),
        (time(12, 0), "Добрый день"),
        (time(18, 0), "Добрый вечер"),
        (time(22, 59), "Добрый вечер"),
        (time(23, 0), "Доброй ночи"),
        (time(0, 30), "Доброй ночи"),
    ],
)
def test_greeting_hours(kb: KnowledgeBase, moment: time, expected: str) -> None:
    assert kb.common.greeting_for(moment) == expected


@pytest.mark.parametrize("name", ["travmpunkt.md", "parkovka.md", "dokumenty.md", "bolnichnyy.md"])
def test_info_files_present(name: str) -> None:
    text = (Path(KB_DIR) / "info" / name).read_text(encoding="utf-8")
    assert text.startswith("# ") and "Источник" in text


def test_sick_leave_jumps_to_repeat_visit_only_for_open_sick_leave(kb: KnowledgeBase) -> None:
    """Stage 1 review: «помощь уже оказана» → planned visit; the one exception → 04-09."""
    jumps = [b for b in kb.scenario("sick_leave").all_branches() if b.scenario]
    assert [(b.scenario, b.when, b.carry) for b in jumps] == [
        (
            "repeat_visit",
            {"help_provided": "sick_leave_open_here"},
            {"first_visit_here": True, "sick_leave_open": True},
        )
    ]


def test_ligament_phrase_has_no_suspicion(kb: KnowledgeBase) -> None:
    sc = kb.scenario("ligament_rupture")
    for name in ("insists_child", "insists_adult"):
        outcome = sc.outcomes[name]
        assert "подозрением" not in " ".join(outcome.say)
        assert outcome.verify and "согласовать" in outcome.verify


def test_shared_field_must_match(tmp_path: Path) -> None:
    import shutil

    from app.flow.loader import KnowledgeBaseError

    shutil.copytree(KB_DIR / "scenarios", tmp_path / "scenarios")
    path = tmp_path / "scenarios" / "04-01_vyvikhi.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace("на тренировке", "в спортзале"), encoding="utf-8"
    )
    with pytest.raises(KnowledgeBaseError, match=r"differs from _common\.json"):
        load_kb(tmp_path)
