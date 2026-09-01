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
    """legacy 렌더러 제거 후 build()가 rendered_context 기반으로 3-파트를 조립하는지 검증."""
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
    fixed, genre, dynamic = builder.build(
        scene_types=["daily"],
        char_data={"id": "minji", "name": "민지", "dynamic_state": {"mood": "calm"}},
        recent_story="",
        user_input="안녕?",
        location="교실",
        dt=datetime(2026, 6, 19, 9, 0),
        npcs=[],
        user_data={"id": "jun", "name": "준"},
        rendered_context=rendered,
        current_pov=None,
    )
    assert isinstance(fixed, str) and fixed.strip(), "fixed section empty"
    assert isinstance(genre, str)
    # rendered_context가 dynamic 파트에 world_context 블록으로 들어가야 한다.
    assert "<world_context>" in dynamic, dynamic[:500]
    assert "[Current Scene]" in dynamic, dynamic[:500]
    # 사용자 입력 라벨링 경로도 살아 있어야 한다.
    assert "안녕?" in dynamic, dynamic[-500:]
    print("[ok] PromptBuilder.build: rendered_context assembled, no legacy path needed")


def main() -> None:
    """전체 smoke 검사를 실행한다."""
    _check_merge_need_hints()
    _check_numeric_state_block_drops_needs()
    _check_build_uses_rendered_context()
    print("\nALL PASS: smoke_prompt_assembly")


if __name__ == "__main__":
    main()
