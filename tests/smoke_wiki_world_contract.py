# ================================
# tests/smoke_wiki_world_contract.py
#
# 저작된 모든 Wiki 월드·시나리오에 대해 Actor prompt의 월드 무관 계약을 검증합니다.
#
# Functions
#   - _authored_world_roots() -> list[Path] : 저작된 Wiki 월드 루트를 자동 발견합니다.
#   - _scenario_roots(world_root: Path) -> list[Path] : 월드 아래 시나리오 루트를 자동 발견합니다.
#   - _load_default_scene_type() -> str : 분류기 호출을 피할 유효한 scene type key를 읽습니다.
#   - _copy_world_to_temp_vault(world_root: Path, temporary_directory: str) -> Path : 월드 하나를 격리된 임시 vault로 복사합니다.
#   - _assert_contract(condition: bool, world_id: str, scenario_id: str, contract: str, detail: str) -> None : 실패 메시지에 월드·시나리오·계약명을 포함합니다.
#   - _count_opening_tag(prompt: str, tag: str) -> int : prompt 안의 opening tag 개수를 셉니다.
#   - _assert_no_heading_selector_leaks(combined_prompt: str, setup: WikiConversationSetup, scenario_ids: set[str]) -> None : 제목 줄에 남은 시나리오 분기 선택기를 검증합니다.
#   - _assert_prompt_leaks(combined_prompt: str, setup: WikiConversationSetup, scenario_ids: set[str]) -> None : 저장소 내부 식별자와 authoring placeholder 누출을 검증합니다.
#   - _assert_prompt_block_counts(vault_root: Path, setup: WikiConversationSetup, bundle: WikiPromptBundle) -> None : vault에서 유도한 자산 수와 prompt block 개수를 비교합니다.
#   - _validate_world_scenarios(world_root: Path, default_scene_type: str) -> int : 월드 하나의 모든 시나리오를 임시 vault에서 검증합니다.
#   - main() -> None : 저작된 모든 Wiki 월드의 prompt 계약 회귀 게이트를 실행합니다.
# ================================

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wiki.context import initialize_wiki_thread
from src.wiki.models import WikiConversationSetup, WikiPromptBundle
from src.wiki.prompt_contract import validate_wiki_prompt_bundle
from src.wiki.runtime import build_wiki_prompt_bundle


_SCENE_TYPES_PATH = ROOT / "src" / "wiki" / "prompts" / "scene_types.json"
_WORLDS_ROOT = ROOT / "wiki_v2" / "worlds"
_PLACEHOLDER_TEXTS = (
    "아직 정해지지",
    "플레이 중 확정",
    "구체적인 값은 미정",
    "세부 값은 미정",
)


def _authored_world_roots() -> list[Path]:
    """저작된 Wiki 월드 루트를 `world.md` 기준으로 자동 발견합니다."""
    return sorted(world_path.parent for world_path in _WORLDS_ROOT.glob("*/world.md"))


def _scenario_roots(world_root: Path) -> list[Path]:
    """월드 아래 `scenario.md`가 있는 시나리오 루트만 안정된 순서로 반환합니다."""
    return sorted(scenario_path.parent for scenario_path in world_root.glob("scenarios/*/scenario.md"))


def _load_default_scene_type() -> str:
    """분류기를 우회할 공용 scene type key를 scene_types.json에서 읽습니다."""
    raw_catalog = json.loads(_SCENE_TYPES_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_catalog, dict) or not raw_catalog:
        raise AssertionError("scene_types.json must contain at least one scene type")
    if "daily" in raw_catalog:
        return "daily"
    return sorted(str(key) for key in raw_catalog)[0]


def _copy_world_to_temp_vault(world_root: Path, temporary_directory: str) -> Path:
    """월드 하나를 `worlds/<world_id>` 형태의 격리된 임시 vault로 복사합니다."""
    vault_root = Path(temporary_directory) / "wiki_v2"
    destination = vault_root / "worlds" / world_root.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(world_root, destination)
    return vault_root


def _assert_contract(
    condition: bool,
    world_id: str,
    scenario_id: str,
    contract: str,
    detail: str,
) -> None:
    """실패 시 어느 월드·시나리오·계약이 깨졌는지 바로 알 수 있게 합니다."""
    if not condition:
        raise AssertionError(f"{world_id}/{scenario_id}: {contract} - {detail}")


def _count_opening_tag(prompt: str, tag: str) -> int:
    """주어진 XML-like opening tag의 출현 횟수를 셉니다."""
    return prompt.count(f"<{tag}>")


def _assert_no_heading_selector_leaks(
    combined_prompt: str,
    setup: WikiConversationSetup,
    scenario_ids: set[str],
) -> None:
    """제목 줄에 남은 시나리오/common 선택기가 물질화에서 제거됐는지 검증합니다."""
    forbidden_headings = {heading.casefold() for heading in scenario_ids}
    forbidden_headings.add("common")
    for line in combined_prompt.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading_text = stripped.lstrip("#").strip()
        _assert_contract(
            heading_text.casefold() not in forbidden_headings,
            setup.world_id,
            setup.scenario_id,
            "forbidden heading selector",
            f"prompt leaked heading selector {heading_text!r}",
        )


def _assert_prompt_leaks(
    combined_prompt: str,
    setup: WikiConversationSetup,
    scenario_ids: set[str],
) -> None:
    """월드 무관 prompt에 내부 저장소 메타데이터와 authoring placeholder가 남지 않았는지 봅니다."""
    world_id = setup.world_id
    scenario_id = setup.scenario_id
    lowered_prompt = combined_prompt.lower()
    literal_checks = (
        ("forbidden literal", ".md"),
        ("forbidden literal", "<wiki_"),
        ("forbidden literal", "scene/current.md"),
        ("forbidden literal", "characters/"),
        ("forbidden literal", "thread_id"),
        ("forbidden literal", "scenario_lore"),
        ("forbidden literal", "시나리오 특징"),
        ("forbidden literal", "시나리오 한정"),
        ("forbidden id", setup.thread_id),
        ("forbidden id", setup.world_id),
        ("forbidden id", setup.pc_id),
        ("forbidden id", setup.npc_id),
    )
    for contract, forbidden in literal_checks:
        _assert_contract(
            forbidden not in combined_prompt,
            world_id,
            scenario_id,
            contract,
            f"prompt leaked {forbidden!r}",
        )
    # 부분 문자열이 아니라 단어 경계로 본다. 산문에 쓰이는 일반 영단어("parallel
    # threads")는 런타임 용어 노출이 아니므로 통과시키고, "thread_id"나 "this thread"
    # 같은 실제 노출은 계속 잡는다.
    for forbidden in ("thread", "wiki", "scenario"):
        _assert_contract(
            re.search(rf"\b{forbidden}\b", lowered_prompt) is None,
            world_id,
            scenario_id,
            "forbidden word",
            f"prompt leaked {forbidden!r}",
        )
    _assert_no_heading_selector_leaks(combined_prompt, setup, scenario_ids)
    for placeholder in _PLACEHOLDER_TEXTS:
        _assert_contract(
            placeholder not in combined_prompt,
            world_id,
            scenario_id,
            "authoring placeholder",
            f"prompt leaked placeholder {placeholder!r}",
        )


def _assert_prompt_block_counts(
    vault_root: Path,
    setup: WikiConversationSetup,
    bundle: WikiPromptBundle,
) -> None:
    """vault 자산 수와 prompt block 수가 어긋나지 않는지 검증합니다."""
    world_root = vault_root / "worlds" / setup.world_id
    thread_root = vault_root / "threads" / setup.thread_id

    expected_location_count = len(list((world_root / "locations").glob("*.md")))
    expected_organization_count = len(list((world_root / "organizations").glob("*.md")))
    expected_character_count = len(list((thread_root / "characters").glob("*.md")))

    actual_location_count = _count_opening_tag(bundle.fixed_prompt, "location_information")
    actual_organization_count = _count_opening_tag(
        bundle.fixed_prompt,
        "organization_information",
    )
    actual_character_profile_count = _count_opening_tag(bundle.fixed_prompt, "character_profile")
    actual_character_state_count = _count_opening_tag(
        bundle.dynamic_prompt,
        "current_character_state",
    )

    world_id = setup.world_id
    scenario_id = setup.scenario_id
    _assert_contract(
        actual_location_count == expected_location_count,
        world_id,
        scenario_id,
        "fixed location count",
        f"expected {expected_location_count}, got {actual_location_count}",
    )
    _assert_contract(
        actual_organization_count == expected_organization_count,
        world_id,
        scenario_id,
        "fixed organization count",
        f"expected {expected_organization_count}, got {actual_organization_count}",
    )
    _assert_contract(
        actual_character_profile_count == expected_character_count,
        world_id,
        scenario_id,
        "fixed character profile count",
        f"expected {expected_character_count}, got {actual_character_profile_count}",
    )
    _assert_contract(
        actual_character_state_count == expected_character_count,
        world_id,
        scenario_id,
        "dynamic character state count",
        f"expected {expected_character_count}, got {actual_character_state_count}",
    )


def _validate_world_scenarios(world_root: Path, default_scene_type: str) -> int:
    """월드 하나를 격리된 임시 vault에 복사해 모든 시나리오의 prompt 계약을 검증합니다."""
    scenario_roots = _scenario_roots(world_root)
    world_id = world_root.name
    scenario_ids = {scenario_root.name for scenario_root in scenario_roots}

    with TemporaryDirectory(prefix=f"smoke_wiki_world_contract_{world_id}_") as temporary_directory:
        vault_root = _copy_world_to_temp_vault(world_root, temporary_directory)
        for scenario_root in scenario_roots:
            scenario_id = scenario_root.name
            setup = initialize_wiki_thread(
                vault_root,
                world_id,
                scenario_id,
                f"contract_{world_id}_{scenario_id}",
            )
            bundle = build_wiki_prompt_bundle(
                vault_root,
                setup,
                user_input="프롬프트 계약 검증용 입력입니다.",
                recent_story="",
                scene_types=[default_scene_type],
            )
            validate_wiki_prompt_bundle(bundle)
            combined_prompt = "\n".join(
                (bundle.fixed_prompt, bundle.genre_prompt, bundle.dynamic_prompt)
            )
            _assert_prompt_leaks(combined_prompt, setup, scenario_ids)
            _assert_prompt_block_counts(vault_root, setup, bundle)
    return len(scenario_roots)


def main() -> None:
    """저작된 모든 Wiki 월드·시나리오에 대해 prompt 계약 회귀 게이트를 실행합니다."""
    world_roots = _authored_world_roots()
    default_scene_type = _load_default_scene_type()
    scenario_count = 0
    for world_root in world_roots:
        scenario_count += _validate_world_scenarios(world_root, default_scene_type)
    print(
        "smoke_wiki_world_contract: ok "
        f"({len(world_roots)} worlds, {scenario_count} scenarios)"
    )


if __name__ == "__main__":
    main()
