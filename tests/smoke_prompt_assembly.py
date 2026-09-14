# ================================
# tests/smoke_prompt_assembly.py
#
# 프롬프트 조립 경로의 최근 변경(욕구 힌트 병합 B-1, 죽은 needs 밴드 제거 B-2,
# legacy 렌더러 삭제)이 회귀 없이 동작하는지 DB 없이 검증하는 smoke 검사.
#
# Functions
#   - _check_merge_need_hints() -> None : scene/libido 힌트 병합과 동일 NPC 충돌 결합 검증.
#   - _check_numeric_state_block_drops_needs() -> None : 욕구 수치 밴드 미렌더 + mood/stress 유지 검증.
#   - _check_build_uses_rendered_context() -> None : legacy 제거 후 build()가 rendered_context로 조립되는지 검증.
#   - _check_reproductive_state_requires_adult_engine() -> None : 생식 상태가 성인 엔진에 종속되는지 검증.
#   - _check_prose_profile_modifier_group_exclusivity() -> None : 성인 modifier 그룹 상호 배타(마지막 선택 우선)를 검증.
#   - _check_adult_modifier_render_coupling() -> None : 성인 modifier가 성인 엔진 on/off에 렌더 시점에 연동되는지 검증.
#   - _check_engine_module_policy_sync() -> None : 메인·기억 엔진 policy_group 동기화(꺼진 슬롯 유지)를 검증.
#   - _check_main_slot_restored() -> None : main 슬롯이 카탈로그 첫 자리에 복원되고 Fixed에 렌더되는지 검증.
#   - _check_output_contract_owns_engine_block_order() -> None : Dynamic 출력 계약이 엔진 블록 배치와 확장된 침묵 검증을 소유하는지 검증.
#   - _check_normalize_prose_profile_dedupes_saved_instance() -> None : 저장된 ProseProfile 인스턴스도 정규화 시 modifier 중복 제거가 적용되는지 검증.
#   - main() -> None : 전체 smoke 검사를 실행.
# ================================

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.commit import _merge_need_hints  # noqa: E402
from src.agents.context.renderer import build_rendered_dynamic_context  # noqa: E402
from src.agents.prompt_factory.builder import PromptBuilder  # noqa: E402
from src.agents.prompt_factory.engines import engine_module_catalog, normalize_engine_modules  # noqa: E402
from src.agents.prompt_factory.profiles import ProseProfile, normalize_prose_profile  # noqa: E402


def _check_merge_need_hints() -> None:
    """B-1: libido 힌트가 scene 힌트와 합쳐지고, 같은 NPC면 양쪽 모두 보존되는지 검증."""
    # libido만 있는 NPC는 그대로 통과해야 한다(이전엔 폐기되던 경로).
    merged = _merge_need_hints({"a": "hungry hint"}, {"b": "libido hint"})
    assert merged == {"a": "hungry hint", "b": "libido hint"}, merged

    # 같은 NPC가 두 종류를 동시에 넘기면 한쪽이 묻히지 않고 줄바꿈으로 결합돼야 한다.
    collide = _merge_need_hints({"a": "scene hint"}, {"a": "libido hint"})
    assert collide["a"] == "scene hint\nlibido hint", collide

    # None / 빈 입력 방어.
    assert _merge_need_hints(None, None) == {}
    assert _merge_need_hints({"a": "x"}, {"a": ""}) == {"a": "x"}
    print("[ok] _merge_need_hints: libido merge + collision + empty-guard")


def _check_numeric_state_block_drops_needs() -> None:
    """B-2: dynamic_state에 욕구 키가 있어도 needs 밴드는 렌더되지 않고 mood/stress만 남는지 검증."""
    blocks = build_rendered_dynamic_context(
        scene_state={"location": "방", "participants": ["민지"], "scene_type": "daily"},
        context_plan={"importance": 3, "query_focus": [], "skip_systems": []},
        relationship={},
        events=[],
        recall_events=[],
        personal_facts=[],
        npcs=[],
        world_context={},
        # 욕구 수치가 dynamic_state에 잘못 실려도 노출되면 안 된다.
        dynamic_state={"mood": "calm", "stress_level": 3, "hunger": 0.9, "libido": 0.85},
    )
    state_block = blocks.get("state", "")
    assert "mood" in state_block, state_block
    assert "stress" in state_block, state_block
    assert "needs" not in state_block, f"needs band should be gone: {state_block}"
    assert "hunger" not in state_block and "libido" not in state_block, state_block
    print("[ok] numeric state block: mood/stress kept, needs band removed")


def _check_build_uses_rendered_context() -> None:
    """build()가 rendered_context 기반으로 Fixed/Dynamic 2-파트를 조립하는지 검증."""
    builder = PromptBuilder(
        world_config={"rating": "r18", "perspective": 3},
        char_name="민지",
        user_name="준",
        perspective=3,
    )
    rendered = build_rendered_dynamic_context(
        scene_state={"location": "교실", "participants": ["민지", "준"], "scene_type": "daily"},
        context_plan={"importance": 3, "query_focus": [], "skip_systems": []},
        relationship={"affinity": 50, "trust": 40},
        events=[],
        recall_events=[],
        personal_facts=[],
        npcs=[],
        world_context={},
        dynamic_state={"mood": "calm"},
    )
    fixed, dynamic = builder.build(
        scene_types=["daily"],
        char_data={"id": "minji", "name": "민지", "dynamic_state": {"mood": "calm"}},
        user_input="안녕?",
        location="교실",
        dt=datetime(2026, 6, 19, 9, 0),
        npcs=[],
        user_data={"id": "jun", "name": "준"},
        rendered_context=rendered,
        current_pov=None,
    )
    assert isinstance(fixed, str) and fixed.strip(), "fixed section empty"
    # Fixed는 하나의 문체 축(prose profile)과 항상 적용되는 simulation core만 쓴다.
    assert "<simulation_core>" in fixed, fixed[:500]
    assert "<prose_profile>" in fixed, fixed[:500]
    for gone in ("<pov>", "<emotion>", "<style>", "checklist"):
        assert gone not in fixed, f"fixed still carries {gone}"
    # rendered_context가 dynamic 파트에 world_context 블록으로 들어가야 한다.
    assert "<world_context>" in dynamic, dynamic[:500]
    assert "[Current Scene]" in dynamic, dynamic[:500]
    # 사용자 입력 라벨링 경로도 살아 있어야 한다.
    assert "안녕?" in dynamic, dynamic[-500:]
    # 체크리스트가 옮겨 간 동적 블록과 System_Log 출력 계약이 Dynamic에 있어야 한다.
    assert "<current_pov>" in dynamic, dynamic[:500]
    assert "<output_contract>" in dynamic, dynamic[-800:]
    assert "System_Log" in dynamic, dynamic[-800:]
    for gone in ("<dialogue_examples>", "<scene_specific_prompts>", "PRE-DRAFT", "FINAL:"):
        assert gone not in dynamic, f"dynamic still carries {gone}"
    print("[ok] PromptBuilder.build: profile fixed + System_Log dynamic assembled")


def _check_reproductive_state_requires_adult_engine() -> None:
    """생식 상태는 adult 엔진이 켜졌을 때만 Dynamic에 렌더된다."""
    char_data = {
        "id": "minji",
        "name": "민지",
        "dynamic_state": {"mood": "calm", "has_menstrual_cycle": True, "cycle_day": 14},
    }
    common = dict(
        scene_types=["daily"],
        char_data=char_data,
        user_input="안녕?",
        location="교실",
        dt=datetime(2026, 6, 19, 9, 0),
        npcs=[],
        user_data={"id": "jun", "name": "준"},
        current_pov=None,
    )
    off = PromptBuilder(
        world_config={"rating": "r18", "perspective": 3},
        char_name="민지",
        user_name="준",
        perspective=3,
    )
    _, dynamic_off = off.build(**common)
    assert "<reproductive_state>" not in dynamic_off, dynamic_off[:500]
    on = PromptBuilder(
        world_config={"rating": "r18", "perspective": 3},
        char_name="민지",
        user_name="준",
        perspective=3,
        engine_modules={"adult": "on"},
    )
    _, dynamic_on = on.build(**common)
    assert "<reproductive_state>" in dynamic_on, dynamic_on[:800]
    assert "pregnancy_risk" in dynamic_on, dynamic_on[:800]
    print("[ok] reproductive state gated on the adult engine")


def _check_prose_profile_modifier_group_exclusivity() -> None:
    """성인 modifier(adult_register)는 상호 배타적이며 마지막 선택이 남는지 검증한다."""
    profile = normalize_prose_profile({"modifiers": ["erotic_commercial", "relaxed_v1", "erotic_hentai"]})
    assert profile.modifiers == ["relaxed_v1", "erotic_hentai"], profile.modifiers
    print("[ok] normalize_prose_profile: adult_register modifiers mutually exclusive, last occurrence wins")


def _check_adult_modifier_render_coupling() -> None:
    """성인 modifier 렌더는 성인 엔진 on/off에 연동되고, 켜져 있는데 미선택이면 상업지가 기본값이다."""
    default_when_adult_on = PromptBuilder(
        world_config={"rating": "r18", "perspective": 3},
        char_name="민지",
        user_name="준",
        perspective=3,
        engine_modules={"adult": "on"},
    ).build_fixed_section()
    assert "## Adult Expression: Commercial" in default_when_adult_on, default_when_adult_on[:800]

    hentai_but_adult_off = PromptBuilder(
        world_config={"rating": "r18", "perspective": 3},
        char_name="민지",
        user_name="준",
        perspective=3,
        prose_profile={"modifiers": ["erotic_hentai"]},
    ).build_fixed_section()
    assert "## Adult Expression" not in hentai_but_adult_off, hentai_but_adult_off[:800]

    hentai_with_adult_on = PromptBuilder(
        world_config={"rating": "r18", "perspective": 3},
        char_name="민지",
        user_name="준",
        perspective=3,
        prose_profile={"modifiers": ["erotic_hentai"]},
        engine_modules={"adult": "on"},
    ).build_fixed_section()
    assert "## Adult Expression: Hentai" in hentai_with_adult_on, hentai_with_adult_on[:800]
    assert "## Adult Expression: Commercial" not in hentai_with_adult_on, hentai_with_adult_on[:800]
    print("[ok] fixed section: adult modifiers coupled to the adult engine at render time")


def _check_engine_module_policy_sync() -> None:
    """메인·기억 엔진은 policy_group("impersonation")을 공유하고, 꺼진 슬롯은 켜지지 않는다."""
    synced = normalize_engine_modules({"main": "anti", "memory": "impersonation"})
    assert synced["memory"] == "anti", synced

    memory_stays_off = normalize_engine_modules({"main": "base", "memory": "off"})
    assert memory_stays_off["memory"] == "off", memory_stays_off

    memory_leads_when_main_off = normalize_engine_modules({"main": "off", "memory": "impersonation"})
    assert memory_leads_when_main_off["memory"] == "impersonation", memory_leads_when_main_off
    print("[ok] normalize_engine_modules: main/memory policy_group sync restored")


def _check_main_slot_restored() -> None:
    """main 슬롯이 카탈로그 첫 자리에 복원되고, 선택 시 Fixed에 <engine_module name=\"main\">으로 렌더된다."""
    catalog = engine_module_catalog()
    assert catalog["slots"][0]["id"] == "main", catalog["slots"][0]

    fixed = PromptBuilder(
        world_config={"rating": "r18", "perspective": 3},
        char_name="민지",
        user_name="준",
        perspective=3,
        engine_modules={"main": "base"},
    ).build_fixed_section()
    assert '<engine_module name="main">' in fixed, fixed[:800]
    print("[ok] engine module catalog: main slot restored at index 0 and rendered")


def _check_output_contract_owns_engine_block_order() -> None:
    """Dynamic <output_contract>이 엔진 블록 배치 순서와 확장된 침묵 검증 항목을 소유하는지 검증한다."""
    builder = PromptBuilder(
        world_config={"rating": "r18", "perspective": 3},
        char_name="민지",
        user_name="준",
        perspective=3,
    )
    rendered = build_rendered_dynamic_context(
        scene_state={"location": "교실", "participants": ["민지", "준"], "scene_type": "daily"},
        context_plan={"importance": 3, "query_focus": [], "skip_systems": []},
        relationship={"affinity": 50, "trust": 40},
        events=[],
        recall_events=[],
        personal_facts=[],
        npcs=[],
        world_context={},
        dynamic_state={"mood": "calm"},
    )
    _, dynamic = builder.build(
        scene_types=["daily"],
        char_data={"id": "minji", "name": "민지", "dynamic_state": {"mood": "calm"}},
        user_input="안녕?",
        location="교실",
        dt=datetime(2026, 6, 19, 9, 0),
        npcs=[],
        user_data={"id": "jun", "name": "준"},
        rendered_context=rendered,
        current_pov=None,
    )
    assert "engine-module order" in dynamic, dynamic[-1000:]
    assert "then IMMEDIATELY write the Korean prose scene." not in dynamic, dynamic[-1000:]
    print("[ok] output contract: owns engine-module block placement and absorbed silent-verify checks")


def _check_normalize_prose_profile_dedupes_saved_instance() -> None:
    """저장된 ProseProfile 인스턴스를 다시 넘겨도(회의 없이) modifier 그룹 중복 제거가 적용되는지 검증한다.

    구제 이전에 저장된 대화/메시지는 group 불변식이 없던 시절 여러 erotic_*
    modifier를 동시에 가질 수 있었다. normalize_prose_profile은 ProseProfile
    인스턴스를 그대로 반환하지 않고 model_dump()를 거쳐 같은 정규화 경로를 타야 한다.
    """
    saved = ProseProfile(modifiers=["erotic_commercial", "erotic_hentai"])
    normalized = normalize_prose_profile(saved)
    assert normalized.modifiers == ["erotic_hentai"], normalized.modifiers

    fixed = PromptBuilder(
        world_config={"rating": "r18", "perspective": 3},
        char_name="민지",
        user_name="준",
        perspective=3,
        prose_profile=saved,
        engine_modules={"adult": "on"},
    ).build_fixed_section()
    assert "## Adult Expression: Hentai" in fixed, fixed[:800]
    assert "## Adult Expression: Commercial" not in fixed, fixed[:800]
    print("[ok] normalize_prose_profile: saved ProseProfile instances are re-deduped, not returned unchanged")


def main() -> None:
    """전체 smoke 검사를 실행한다."""
    _check_merge_need_hints()
    _check_numeric_state_block_drops_needs()
    _check_build_uses_rendered_context()
    _check_reproductive_state_requires_adult_engine()
    _check_prose_profile_modifier_group_exclusivity()
    _check_adult_modifier_render_coupling()
    _check_engine_module_policy_sync()
    _check_main_slot_restored()
    _check_output_contract_owns_engine_block_order()
    _check_normalize_prose_profile_dedupes_saved_instance()
    print("\nALL PASS: smoke_prompt_assembly")


if __name__ == "__main__":
    main()
