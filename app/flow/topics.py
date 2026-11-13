"""Topics by context (part C): what the patient already said narrows the buttons.

* the body area (limb, head, neck_back, chest_abdomen, genital) comes from DeepSeek
  (route) or, without it, from a word list below; once known, topics of other areas are
  never shown;
* route also names 3–5 candidate scenarios: the bot opens the narrowest menu that holds
  them all (e.g. «подвернул ногу» → «Похоже на вывих / перелом / связки / Не знаю»).

No LLM and no Telegram here — plain functions over kb/scenarios/_menu.json.
"""

from __future__ import annotations

import re

from app.flow.loader import KnowledgeBase
from app.flow.schema import Area, FactValue, MenuItem

# Word stems after lower-casing and «ё» → «е». Whole words only; keep them unambiguous
# («ручка» is a pen, «грудничок» is a baby, «лицензия» is not a face).
AREA_WORDS: dict[Area, tuple[str, ...]] = {
    "limb": (
        r"рук[аиеуой]?", r"руками?", r"ручк[аиеу]", r"ног[аиеуой]?", r"ногами?", r"ножк[аиеу]",
        r"пал(ец|ьц\w*)", r"пальчик\w*", r"кист[ьиюей]", r"запясть\w*", r"лок(оть|т\w+)",
        r"плеч\w*", r"предплечь\w*", r"колен\w*", r"коленк\w*", r"стоп[аыеуой]", r"пятк\w*",
        r"лодыж\w*", r"голен\w*", r"бедр\w*", r"щиколот\w*", r"ладон\w*", r"сустав\w*",
        r"ключиц\w*", r"ахилл\w*", r"мениск\w*",
    ),
    "head": (
        r"голов\w*", r"лиц[оауе]", r"лицом", r"глаз\w*", r"век[оау]?", r"ух[оау]", r"ухом",
        r"уш[иейамх]\w*", r"нос[ау]?", r"носом", r"губ[аыуе]?", r"зуб\w*", r"челюст\w*",
        r"лоб", r"лб[ау]", r"щек[аиу]", r"бров\w*", r"висок", r"виск\w*", r"затыл\w*",
        r"макушк\w*", r"сотряс\w*", r"горл[оау]",
    ),
    "neck_back": (
        r"ше[яиюей]", r"шейн\w*", r"спин[аыеуой]", r"спиной", r"поясниц\w*", r"позвоноч\w*",
        r"копчик\w*", r"лопатк\w*",
    ),
    "chest_abdomen": (
        r"груд[ьи]", r"грудью", r"грудн\w+ клетк\w*", r"живот\w*", r"ребр\w*", r"ребер",
        r"бок[уе]?",
    ),
    "genital": (r"половы\w*", r"мошонк\w*", r"яичк\w*"),
}  # fmt: skip

# Topic words → candidate scenarios when DeepSeek is not available.
CANDIDATE_WORDS: dict[str, tuple[str, ...]] = {
    "dislocation": (r"вывих\w*", r"вывихнул\w*", r"выбил\w* сустав\w*"),
    "fracture": (r"перелом\w*", r"слома\w*", r"трещин\w*"),
    "ligament_rupture": (r"связк\w*", r"растяжен\w*", r"растянул\w*"),
    "burns": (r"ожог\w*", r"обожг\w*", r"обжег\w*", r"обжечь", r"ошпар\w*", r"кипят\w*"),
    "animal_bite": (r"укус\w*", r"укусил\w*", r"покусал\w*", r"цапнул\w*"),
    "cut_sutures": (r"порез\w*", r"порезал\w*", r"рассеч\w*", r"ран[аыуе]", r"шов", r"швы"),
    "cast_apply": (r"гипс\w*",),
    "cast_removal": (r"сня\w* гипс\w*", r"снять гипс\w*"),
    "orthosis": (r"ортез\w*",),
    "scotchcast": (r"скотчкаст\w*",),
    "repeat_visit": (r"повторн\w* при[её]м\w*", r"контрольн\w* при[её]м\w*"),
    "dressing": (r"перевязк\w*", r"повязк\w*"),
    "pin_removal": (r"спиц\w*", r"металлоконструкц\w*", r"пластин\w*"),
    "head_trauma": (r"сотряс\w*",),
}  # fmt: skip
# Twisting a joint can be any of these three (the «Упал, ударился, подвернул» menu).
SPRAIN_WORDS = (r"подвернул\w*", r"подвернул\w* ног\w*", r"потянул\w*", r"ушиб\w*", r"упал\w*")
FALL_SCENARIOS = ("dislocation", "fracture", "ligament_rupture")


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower().replace("ё", "е")).split())


def _has(text: str, stems: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b(?:{stem})\b", text) for stem in stems)


def guess_area(text: str) -> Area | None:
    """The body area named in the text, if exactly one is named."""
    t = _normalize(text)
    found = [area for area, stems in AREA_WORDS.items() if _has(t, stems)]
    return found[0] if len(found) == 1 else None


def guess_candidates(text: str) -> list[str]:
    t = _normalize(text)
    found = [sid for sid, stems in CANDIDATE_WORDS.items() if _has(t, stems)]
    if "cast_removal" in found and "cast_apply" in found:
        found.remove("cast_apply")
    if _has(t, SPRAIN_WORDS):
        found += [sid for sid in FALL_SCENARIOS if sid not in found]
    return found


def visible(
    kb: KnowledgeBase, menu_id: str, area: str | None, _seen: frozenset[str] = frozenset()
) -> list[MenuItem]:
    """Items of a menu that fit the body area (submenus — if anything inside is shown)."""
    shown = []
    for item in kb.topics.menus[menu_id].items:
        if item.scenario is not None:
            ok = kb.scenario(item.scenario).fits(area)
        elif item.menu is not None:
            ok = item.menu not in _seen and bool(visible(kb, item.menu, area, _seen | {menu_id}))
        else:
            ok = True  # «Опишите своими словами», «Позвать администратора»
        if ok:
            shown.append(item)
    return shown


def scenarios_under(
    kb: KnowledgeBase, menu_id: str, area: str | None, _seen: frozenset[str] = frozenset()
) -> set[str]:
    if menu_id in _seen:
        return set()
    found: set[str] = set()
    for item in kb.topics.menus[menu_id].items:
        if item.scenario is not None and kb.scenario(item.scenario).fits(area):
            found.add(item.scenario)
        elif item.menu is not None:
            found |= scenarios_under(kb, item.menu, area, _seen | {menu_id})
    return found


def start_menu(kb: KnowledgeBase, area: str | None) -> str:
    """The first screen of booking: the area's menu once the area is known, else the groups."""
    return kb.topics.areas.get(area, kb.topics.root) if area else kb.topics.root  # type: ignore[call-overload]


def best_menu(kb: KnowledgeBase, area: str | None, candidates: list[str]) -> str | None:
    """The narrowest menu under the area's menu that offers every fitting candidate.

    None when there is nothing to go on (no area, no candidate under the five groups).
    """
    start = start_menu(kb, area)
    wanted = {c for c in candidates if c in kb.scenarios and kb.scenario(c).fits(area)}
    wanted &= scenarios_under(kb, start, area)
    if not wanted:
        return start if area else None
    holding: list[tuple[int, int, str]] = []  # (topics in it, -depth, menu): smallest wins
    stack = [(start, 0)]
    seen: set[str] = set()
    while stack:
        menu_id, depth = stack.pop()
        if menu_id in seen:
            continue
        seen.add(menu_id)
        under = scenarios_under(kb, menu_id, area)
        if wanted <= under:
            holding.append((len(under), -depth, menu_id))
        for item in visible(kb, menu_id, area):
            if item.menu is not None:
                stack.append((item.menu, depth + 1))
    best = min(holding)[2]
    if best == kb.topics.root and not area:
        return None
    return best


def area_facts(kb: KnowledgeBase, scenario_id: str, area: str | None) -> dict[str, FactValue]:
    """Answers the area already gives: a burn on a hand is «другая часть тела», not the face."""
    if area is None:
        return {}
    return dict(kb.topics.area_facts.get(area, {}).get(scenario_id, {}))  # type: ignore[call-overload]
