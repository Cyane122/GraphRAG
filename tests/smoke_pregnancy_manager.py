# ================================
# tests/smoke_pregnancy_manager.py
#
# PregnancyManager input classification smoke checks.
#
# Functions
#   - _install_stubs() -> None : Install lightweight modules to avoid DB and LLM initialization.
#   - _load_module(name: str, path: str) -> object : Load a source file without package side effects.
#   - _check(label: str, got: bool, expected: bool) -> bool : Print and return a boolean check result.
#   - main() -> int : Run PregnancyManager input classification smoke checks.
# ================================

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_stubs() -> None:
    """Install lightweight modules to avoid DB and LLM initialization."""
    config = types.ModuleType("src.config")
    config.MODEL_CLASSIFIER = "smoke-model"
    sys.modules["src.config"] = config

    database = types.ModuleType("src.core.database")
    database.async_driver = None
    database.update_dynamic_state = lambda *args, **kwargs: None
    sys.modules["src.core.database"] = database

    llm_client = types.ModuleType("src.core.llm.client")
    llm_client.extract_json_from_llm = lambda *args, **kwargs: {}
    llm_client.get_model = lambda *args, **kwargs: None
    llm_client.get_response_text = lambda response: ""
    llm_client.log_empty_response_diagnostics = lambda *args, **kwargs: None
    sys.modules["src.core.llm.client"] = llm_client

    state_package = types.ModuleType("src.simulation.state")
    state_package.__path__ = []
    extract_package = types.ModuleType("src.simulation.state.extract")
    extract_package.__path__ = []
    creator_slots = types.ModuleType("src.simulation.state.extract.creator_slots")
    creator_slots.has_dynamic_slot_signal = lambda *args, **kwargs: False
    sys.modules["src.simulation.state"] = state_package
    sys.modules["src.simulation.state.extract"] = extract_package
    sys.modules["src.simulation.state.extract.creator_slots"] = creator_slots


def _load_module(name: str, path: str) -> object:
    """Load a source file without package side effects."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check(label: str, got: bool, expected: bool) -> bool:
    """Print and return a boolean check result."""
    ok = got is expected
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {label}: got={got} expected={expected}")
    return ok


async def main() -> int:
    """Run PregnancyManager input classification smoke checks."""
    _install_stubs()
    organic = _load_module(
        "organic_smoke",
        "src/simulation/systems/world_dynamics/organic.py",
    )
    policy = _load_module(
        "update_policy_smoke",
        "src/simulation/state/apply/update_policy.py",
    )

    async def failed_classifier(_: str) -> dict:
        """Return an empty classifier result to force deterministic fallback."""
        return {}

    organic._classify_ejaculation = failed_classifier

    cases: list[tuple[str, str, bool]] = [
        ("ordinary", "두 사람은 카페에서 커피를 마셨다.", False),
        ("random punctuation", "...... ?! 1234", False),
        ("mixed benign keywords", "임신 가능성이라는 단어를 검색했지만 아무 일도 없었다.", False),
        ("condom purchase", "편의점에서 콘돔을 샀다.", False),
        ("condom ad", "그들은 콘돔 광고 문구를 읽었다.", False),
        ("cycle talk", "생리 주기와 배란일을 달력에 표시했다.", False),
        ("semen tissue", "정액이 묻은 휴지를 쓰레기통에 버렸다.", False),
        ("semen medical test", "정액 검사 결과를 병원에서 확인했다.", False),
        ("reported ejaculation", "그는 민지에게 사정했다고 말했다.", False),
        ("permission question", "안에 싸도 돼?", False),
        ("hypothetical worry", "안에 싸면 임신할 수도 있다고 걱정했다.", False),
        ("past recollection", "어제 그녀 안에 쌌던 일이 떠올랐다.", False),
        ("prior semen leaking", "아까 싼 정액이 아직도 흘러나와.", False),
        ("explicit unprotected", "그는 그녀의 안에 쌌다.", True),
        ("current ejaculation then leaking", "그가 사정했다. 다 담기지 못한 정액이 다리 사이로 흘러나왔다.", True),
        ("mixed question then event", "안에 싸도 돼? 그는 대답 대신 그녀의 안에 쌌다.", True),
        ("protected condom", "그는 콘돔을 끼고 그녀의 안에 쌌다.", False),
        ("broken condom", "콘돔이 찢어진 채 그는 그녀의 안에 쌌다.", True),
        ("oral", "그는 입 안에 쌌다.", False),
        ("anal", "그는 항문 안에 쌌다.", False),
        ("external", "그는 밖에 쌌다.", False),
        ("negated", "그는 안에 싸지 않았다.", False),
        ("condom broke only", "콘돔이 찢어졌지만 아직 사정하지 않았다.", False),
        ("english internal", "he came inside her", True),
        ("english protected", "he came inside the condom", False),
        ("english question", "she asked if he could cum inside", False),
    ]

    passed = [
        _check(label, await organic.detect_internal_ejaculation(text), expected)
        for label, text, expected in cases
    ]

    async def wrong_positive_classifier(_: str) -> dict:
        """Return a false-positive classifier result for guard override checks."""
        return {"vaginal": True, "condom_protected": False}

    organic._classify_ejaculation = wrong_positive_classifier
    override_cases: list[tuple[str, str, bool]] = [
        ("override permission question", "안에 싸도 돼?", False),
        ("override english question", "she asked if he could cum inside", False),
        ("override past recollection", "어제 그녀 안에 쌌던 일이 떠올랐다.", False),
        ("override negated", "그는 안에 싸지 않았다.", False),
        ("override not yet", "콘돔이 찢어졌지만 아직 사정하지 않았다.", False),
        ("override english negated", "he did not cum inside her", False),
        ("override prior semen leaking", "아까 싼 정액이 아직도 흘러나와.", False),
    ]
    for label, text, expected in override_cases:
        passed.append(_check(label, await organic.detect_internal_ejaculation(text), expected))

    async def current_false_classifier(_: str) -> dict:
        """Return a classifier result that says no current ejaculation happened."""
        return {"current_ejaculation": False, "vaginal": True, "condom_protected": False}

    organic._classify_ejaculation = current_false_classifier
    current_false_cases: list[tuple[str, str, bool]] = [
        ("classifier current false prior semen", "아까 싼 정액이 아직도 흘러나와.", False),
        ("classifier current false blocks explicit text", "그는 그녀의 안에 쌌다.", False),
    ]
    for label, text, expected in current_false_cases:
        passed.append(_check(label, await organic.detect_internal_ejaculation(text), expected))

    async def current_true_classifier(_: str) -> dict:
        """Return a classifier result that confirms current unprotected vaginal ejaculation."""
        return {"current_ejaculation": True, "vaginal": True, "condom_protected": False}

    organic._classify_ejaculation = current_true_classifier
    current_true_cases: list[tuple[str, str, bool]] = [
        (
            "classifier current true leaking after ejaculation",
            "그가 사정했다. 다 담기지 못한 정액이 다리 사이로 흘러나왔다.",
            True,
        ),
    ]
    for label, text, expected in current_true_cases:
        passed.append(_check(label, await organic.detect_internal_ejaculation(text), expected))

    async def wrong_negative_classifier(_: str) -> dict:
        """Return a false-negative classifier result for explicit-positive override checks."""
        return {"current_ejaculation": True, "vaginal": False, "condom_protected": False}

    organic._classify_ejaculation = wrong_negative_classifier
    negative_override_cases: list[tuple[str, str, bool]] = [
        ("override classifier false explicit Korean", "그는 그녀의 안에 쌌다.", True),
        ("override classifier false broken condom", "콘돔이 찢어진 채 그는 그녀의 안에 쌌다.", True),
        ("keep classifier false oral", "그는 입 안에 쌌다.", False),
        ("keep classifier false question", "안에 싸도 돼?", False),
    ]
    for label, text, expected in negative_override_cases:
        passed.append(_check(label, await organic.detect_internal_ejaculation(text), expected))

    policy_cases: list[tuple[str, str, bool]] = [
        ("organic signal", "콘돔이 찢어진 채 그는 그녀 안에 쌌다.", True),
        ("question still routes to organic guard", "안에 싸도 돼?", True),
        ("ordinary no route", "평범한 대화가 이어졌다.", False),
    ]
    passed.extend(
        _check(
            f"policy {label}",
            policy.should_run_life_depth_system("organic", text, {}, 0, 0, []),
            expected,
        )
        for label, text, expected in policy_cases
    )

    id_map = {
        "pc": "pc",
        "main_npc": "main_npc",
        "jiho": "jiho",
        "지호": "jiho",
        "minji": "minji",
        "민지": "minji",
        "sora": "sora",
        "소라": "sora",
    }
    name_map = {
        "pc": "나",
        "main_npc": "하람",
        "jiho": "지호",
        "minji": "민지",
        "sora": "소라",
    }

    async def resolve_char_id(ref: str) -> str | None:
        """Resolve smoke character references."""
        return id_map.get(ref)

    async def get_char_name(char_id: str) -> str:
        """Return smoke character display names."""
        return name_map.get(char_id, char_id)

    organic._resolve_char_id = resolve_char_id
    organic._get_char_name = get_char_name

    candidate_cases: list[tuple[str, list[str], list[str] | None, str, list[str]]] = [
        ("single partner fallback", ["minji"], None, "그녀의 안에 쌌다.", ["minji"]),
        ("mentioned partner", ["minji", "sora"], None, "민지의 안에 쌌다.", ["minji"]),
        ("npc fallback", ["minji", "sora"], None, "그녀의 안에 쌌다.", ["main_npc"]),
        ("explicit intimate dedupe", ["minji", "sora"], ["소라", "sora"], "소라의 안에 쌌다.", ["sora"]),
    ]
    for label, scene_chars, intimate_chars, text, expected in candidate_cases:
        npc_id = "pc" if label == "single partner fallback" else "main_npc"
        got = await organic._resolve_pregnancy_candidate_ids(
            npc_id,
            text,
            scene_chars,
            intimate_chars,
        )
        ok = got == expected
        print(f"[{'OK' if ok else 'FAIL'}] candidates {label}: got={got} expected={expected}")
        passed.append(ok)

    classifier_candidate_cases: list[tuple[str, list[str], list[str], str, list[str]]] = [
        (
            "recipient not ejaculator",
            ["지호", "민지", "소라"],
            ["민지"],
            "지호가 민지에게 사정했고 소라는 옆에 있었다.",
            ["minji"],
        ),
        (
            "unknown pronoun falls back to single partner",
            ["민지"],
            ["그녀"],
            "그가 그녀에게 사정했다.",
            ["minji"],
        ),
    ]
    for label, scene_chars, recipient_refs, text, expected in classifier_candidate_cases:
        got = await organic._resolve_pregnancy_candidate_ids(
            "jiho",
            text,
            scene_chars,
            None,
            recipient_refs,
        )
        ok = got == expected
        print(f"[{'OK' if ok else 'FAIL'}] classifier candidates {label}: got={got} expected={expected}")
        passed.append(ok)

    states: dict[str, dict[str, Any]] = {
        "minji": {
            "cycle_day": 14,
            "pregnant": False,
            "pregnancy_day": 0,
            "cum_shots": 0,
            "has_menstrual_cycle": True,
        },
        "jiho": {
            "cycle_day": 1,
            "pregnant": False,
            "pregnancy_day": 0,
            "cum_shots": 0,
            "has_menstrual_cycle": False,
        },
        "sora": {
            "cycle_day": 14,
            "pregnant": False,
            "pregnancy_day": 0,
            "cum_shots": 0,
            "has_menstrual_cycle": False,
        },
        "pregnant_char": {
            "cycle_day": 14,
            "pregnant": True,
            "pregnancy_day": 12,
            "cum_shots": 0,
            "has_menstrual_cycle": True,
        },
    }
    updates: list[tuple[str, dict[str, Any]]] = []

    async def get_cycle_state(char_id: str) -> dict[str, Any]:
        """Return smoke cycle state."""
        return dict(states[char_id])

    async def update_dynamic_state(char_id: str, values: dict[str, Any]) -> None:
        """Record smoke dynamic state updates."""
        updates.append((char_id, dict(values)))

    organic._classify_ejaculation = failed_classifier
    organic._get_cycle_state = get_cycle_state
    organic.update_dynamic_state = update_dynamic_state
    organic.random.random = lambda: 0.999

    no_cycle = await organic.process_ejaculation(
        "main_npc",
        "그는 소라의 안에 쌌다.",
        scene_char_ids=["소라"],
    )
    passed.append(_check("process no cycle returns none", no_cycle is not None, False))
    passed.append(_check("process no cycle no update", bool(updates), False))

    no_pregnancy = await organic.process_ejaculation(
        "main_npc",
        "그는 민지의 안에 쌌다.",
        scene_char_ids=["민지"],
    )
    passed.append(_check("process non-pregnant roll returns none", no_pregnancy is not None, False))
    passed.append(_check("process cum shot update", updates == [("minji", {"cum_shots_this_cycle": 1})], True))

    updates.clear()
    organic.random.random = lambda: 0.0
    pregnancy = await organic.process_ejaculation(
        "main_npc",
        "그는 민지의 안에 쌌다.",
        scene_char_ids=["민지"],
    )
    expected_updates = [
        ("minji", {"cum_shots_this_cycle": 1}),
        ("minji", {"pregnant": True, "pregnancy_day": 1, "cum_shots_this_cycle": 0}),
    ]
    passed.append(_check("process pregnancy message", pregnancy is not None, True))
    passed.append(_check("process pregnancy updates", updates == expected_updates, True))

    async def minji_recipient_classifier(_: str) -> dict:
        """Return a current ejaculation result whose recipient is Minji."""
        return {
            "current_ejaculation": True,
            "vaginal": True,
            "condom_protected": False,
            "recipient_refs": ["민지"],
        }

    states["minji"]["cum_shots"] = 2
    updates.clear()
    organic._classify_ejaculation = minji_recipient_classifier
    organic.random.random = lambda: 0.999
    recipient_result = await organic.process_ejaculation(
        "jiho",
        "지호가 민지에게 사정했고 소라는 옆에 있었다.",
        scene_char_ids=["지호", "민지", "소라"],
    )
    passed.append(_check("process recipient returns none on high roll", recipient_result is not None, False))
    passed.append(
        _check(
            "process recipient cum shot only",
            updates == [("minji", {"cum_shots_this_cycle": 3})],
            True,
        )
    )

    failed = len([ok for ok in passed if not ok])
    print(f"pregnancy_manager_smoke: passed={len(passed) - failed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
