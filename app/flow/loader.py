"""Load and cross-check the knowledge base (kb/scenarios/*.json).

Files starting with ``_`` are shared data: ``_facilities.json`` (places patients are
routed to) and ``_common.json`` (phrases shared by all booking scripts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import TypeAdapter

from app.config import PROJECT_ROOT
from app.flow.schema import CommonPhrases, Facility, Scenario

KB_DIR = PROJECT_ROOT / "kb"


class KnowledgeBaseError(ValueError):
    """The knowledge base files are inconsistent."""


@dataclass(frozen=True)
class KnowledgeBase:
    scenarios: dict[str, Scenario]
    facilities: dict[str, Facility]
    common: CommonPhrases

    def scenario(self, scenario_id: str) -> Scenario:
        return self.scenarios[scenario_id]


def load_kb(kb_dir: Path = KB_DIR) -> KnowledgeBase:
    scenario_dir = kb_dir / "scenarios"
    scenarios: dict[str, Scenario] = {}
    errors: list[str] = []

    for path in sorted(scenario_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            scenario = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if not _file_matches(scenario, path.stem):
            errors.append(f"{path.name}: script {scenario.script!r} must match the file name")
        if scenario.id in scenarios:
            errors.append(f"{path.name}: duplicate scenario id {scenario.id!r}")
        scenarios[scenario.id] = scenario

    facility_list = TypeAdapter(list[Facility]).validate_json(
        (scenario_dir / "_facilities.json").read_text(encoding="utf-8")
    )
    facilities = {f.id: f for f in facility_list}
    if len(facilities) != len(facility_list):
        errors.append("_facilities.json: duplicate facility ids")
    common = CommonPhrases.model_validate_json(
        (scenario_dir / "_common.json").read_text(encoding="utf-8")
    )

    errors += _cross_check(scenarios, facilities)
    errors += _check_shared_fields(scenarios, common)
    if errors:
        raise KnowledgeBaseError("\n".join(errors))
    return KnowledgeBase(scenarios=scenarios, facilities=facilities, common=common)


def _file_matches(scenario: Scenario, stem: str) -> bool:
    """One file per script; a scenario built from a clinic answer is «<script number>-N_…»."""
    if scenario.decision is None:
        return stem == scenario.script
    return stem.startswith(scenario.script.split("_")[0] + "-")


@lru_cache(maxsize=1)
def get_kb() -> KnowledgeBase:
    return load_kb()


def _cross_check(scenarios: dict[str, Scenario], facilities: dict[str, Facility]) -> list[str]:
    errors: list[str] = []
    for sc in scenarios.values():
        for branch in sc.all_branches():
            if branch.scenario is None:
                continue
            target = scenarios.get(branch.scenario)
            if target is None:
                errors.append(f"{sc.id}: jump to unknown scenario {branch.scenario!r}")
                continue
            target_fields = target.fields()
            for key, value in branch.carry.items():
                spec = target_fields.get(key)
                if spec is None or not spec.accepts(value):
                    errors.append(f"{sc.id}: bad carry {key}={value!r} into {target.id}")
        for name, outcome in sc.outcomes.items():
            for dest in outcome.destinations:
                if dest.facility not in facilities:
                    errors.append(f"{sc.id}.{name}: unknown facility {dest.facility!r}")
    return errors


def _check_shared_fields(scenarios: dict[str, Scenario], common: CommonPhrases) -> list[str]:
    errors: list[str] = []
    for sc in scenarios.values():
        for name, spec in sc.fields().items():
            shared = common.shared_fields.get(name)
            if shared is not None and spec != shared.field:
                errors.append(f"{sc.id}: field {name!r} differs from _common.json shared_fields")
    return errors


# --- source verification (used by tests: nothing in kb/ may be invented) ---------------


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def script_text(script: str, kb_dir: Path = KB_DIR) -> str:
    return normalize_ws((kb_dir / "text" / f"{script}.md").read_text(encoding="utf-8"))


def digits(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def verify_sources(kb: KnowledgeBase, kb_dir: Path = KB_DIR) -> list[str]:
    """Check that every place and address is quoted from the clinic's scripts."""
    errors: list[str] = []
    for sc in kb.scenarios.values():
        if not (kb_dir / "text" / f"{sc.script}.md").is_file():
            errors.append(f"{sc.id}: no source script kb/text/{sc.script}.md")
    for fac in kb.facilities.values():
        path = kb_dir / "text" / f"{fac.source}.md"
        if not path.is_file():
            errors.append(f"facility {fac.id}: unknown source {fac.source!r}")
            continue
        text = script_text(fac.source, kb_dir)
        for quote in fac.quotes:
            if normalize_ws(quote) not in text:
                errors.append(f"facility {fac.id}: quote not found in {fac.source}: {quote!r}")
        quoted_digits = digits(" ".join(fac.quotes))
        if missing := digits(f"{fac.name} {fac.address or ''}") - quoted_digits:
            errors.append(f"facility {fac.id}: numbers {sorted(missing)} are not in the quotes")
    return errors
