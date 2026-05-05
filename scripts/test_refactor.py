# scripts/test_refactor.py
# 실행: python scripts/test_refactor.py
#
# Step 4 리팩토링 후 import 체인 및 순수 함수 동작 확인.
# LLM / Neo4j 없이 실행 가능.

import sys, traceback
from pathlib import Path
from datetime import datetime

# 프로젝트 루트를 sys.path에 추가 (scripts/ 에서 실행 시 src 패키지를 찾기 위해)
sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = "[PASS]"
FAIL = "[FAIL]"

results: list[tuple[str, bool, str]] = []


def test(name: str):
    def decorator(fn):
        try:
            fn()
            results.append((name, True, ""))
        except Exception as e:
            results.append((name, False, traceback.format_exc()))
    return decorator


# ════════════════════════════════════════════════════════════
# 1. Import 체인
# ════════════════════════════════════════════════════════════

@test("import: src.agents.manager")
def _():
    from src.agents.manager import (
        load_world_instance, CLASSIFIER_MODEL,
        _try_rule_based, detect_present_npcs,
    )

@test("import: src.agents.actor")
def _():
    from src.agents.actor import run_actor, ACTOR_MODEL

@test("import: src.agents.resolver")
def _():
    from src.agents.resolver import resolve_action, SETTLE_LEVELS, NEED_ACTION_HINTS

@test("import: src.agents.prompt_factory.builder")
def _():
    from src.agents.prompt_factory.builder import PromptBuilder, build_genre_section

@test("import: src.agents.prompt_factory.ooc_handler")
def _():
    from src.agents.prompt_factory.ooc_handler import is_ooc, _SYSTEM_PROMPT

@test("import: src.agents.prompt_factory (패키지)")
def _():
    from src.agents.prompt_factory import PromptBuilder, is_ooc, parse_ooc

@test("import: src.simulation.systems.needs (resolver 경로 변경)")
def _():
    from src.simulation.systems.needs import run_needs_update, ensure_traits

@test("import: src.simulation.state.classifier")
def _():
    from src.simulation.state.classifier import classify_and_extract, _sanitize_stress_level

@test("import: src.simulation.state.updater")
def _():
    from src.simulation.state.updater import (
        process_actor_response, apply_time_updates, delegate_complex_update
    )

@test("import: src.simulation (패키지 __init__)")
def _():
    from src.simulation.state import classify_and_extract
    from src.simulation.systems import ensure_memories_for_event, run_needs_update


# ════════════════════════════════════════════════════════════
# 2. is_ooc() — 순수 함수
# ════════════════════════════════════════════════════════════

@test("is_ooc: 일반 대화 → False")
def _():
    from src.agents.prompt_factory.ooc_handler import is_ooc
    assert not is_ooc("안녕, 오늘 뭐 했어?")

@test("is_ooc: OOC 명령 → True")
def _():
    from src.agents.prompt_factory.ooc_handler import is_ooc
    assert is_ooc("*3시간 후.*")

@test("is_ooc: 볼드(**) 안에 있는 * 는 무시")
def _():
    from src.agents.prompt_factory.ooc_handler import is_ooc
    assert not is_ooc("**은서**가 웃으며 말했다.")

@test("is_ooc: 볼드 밖 + * 있으면 True")
def _():
    from src.agents.prompt_factory.ooc_handler import is_ooc
    assert is_ooc("**은서**가 웃으며 *손을 잡았다.*")


# ════════════════════════════════════════════════════════════
# 3. _sanitize_stress_level() — 순수 함수
# ════════════════════════════════════════════════════════════

@test("_sanitize_stress_level: 정수 문자열 → int")
def _():
    from src.simulation.state.classifier import _sanitize_stress_level
    assert _sanitize_stress_level("7") == 7

@test("_sanitize_stress_level: None → None")
def _():
    from src.simulation.state.classifier import _sanitize_stress_level
    assert _sanitize_stress_level(None) is None

@test("_sanitize_stress_level: 범위 초과(15) → None")
def _():
    from src.simulation.state.classifier import _sanitize_stress_level
    assert _sanitize_stress_level(15) is None


# ════════════════════════════════════════════════════════════
# 4. _try_rule_based() — 순수 함수
# ════════════════════════════════════════════════════════════

@test("_try_rule_based: 짧은 대화 → daily 반환")
def _():
    from src.agents.manager import _try_rule_based
    result = _try_rule_based("뭐 해?")
    assert result is not None
    assert result["scene_types"] == ["daily"]
    assert result["elapsed_minutes"] == 2

@test("_try_rule_based: OOC 패턴(*) → None (LLM으로 위임)")
def _():
    from src.agents.manager import _try_rule_based
    assert _try_rule_based("*3시간 후.*") is None

@test("_try_rule_based: 긴 입력 → None (LLM으로 위임)")
def _():
    from src.agents.manager import _try_rule_based
    long_input = "오늘 헬스장에서 운동을 마치고 집에 돌아와서 은서에게 말을 걸었다." * 3
    assert _try_rule_based(long_input) is None


# ════════════════════════════════════════════════════════════
# 5. PromptBuilder — LLM 출력을 가정한 mock 데이터로 조립
# ════════════════════════════════════════════════════════════

# LLM이 반환했다고 가정하는 캐릭터 데이터 (Neo4j 조회 결과 모의)
MOCK_CHAR_DATA = {
    "name": "은서",
    "static_profile": {
        "name": "은서", "age": 24, "job": "헬스 트레이너",
        "personality": "활발하고 솔직함. 귀여움을 싫어함.",
    },
    "personality": {"style": "직설적", "humor": "블랙유머"},
    "dynamic_state": {
        "mood": "calm", "stress_level": 3, "physical_condition": "healthy",
        "location_id": "헬스장", "cycle_day": 10,
    },
}

MOCK_USER_DATA = {"name": "시안"}
MOCK_RELATIONSHIP = {"affinity": 65, "status": "친구"}
MOCK_EVENTS = [
    {"timestamp": "2026-05-05T10:00:00", "summary": "같이 헬스장에서 운동했다."},
]

@test("PromptBuilder: 3인칭 build() → 3-tuple 반환")
def _():
    from src.agents.prompt_factory.builder import PromptBuilder

    builder = PromptBuilder(
        world_config = {"rating": "r18", "few_shot_examples": {}},
        char_name    = "은서",
        user_name    = "시안",
        perspective  = 3,
    )
    fixed, genre, dynamic = builder.build(
        scene_types  = ["daily"],
        char_data    = MOCK_CHAR_DATA,
        relationship = MOCK_RELATIONSHIP,
        events       = MOCK_EVENTS,
        recent_story = "은서가 퇴근 후 막 집에 들어왔다.",
        user_input   = "안녕, 오늘 뭐 했어?",
        location     = "헬스장",
        dt           = datetime(2026, 5, 5, 18, 30),
    )
    assert isinstance(fixed, str) and len(fixed) > 100
    assert isinstance(genre, str)   # intimate 없으면 빈 문자열
    assert isinstance(dynamic, str) and len(dynamic) > 100

@test("PromptBuilder: fixed에 operator_policy 포함")
def _():
    from src.agents.prompt_factory.builder import PromptBuilder
    builder = PromptBuilder(
        world_config={"rating": "r18", "few_shot_examples": {}},
        char_name="은서", user_name="시안",
    )
    fixed, _, _ = builder.build(
        scene_types=["daily"], char_data=MOCK_CHAR_DATA,
        relationship=MOCK_RELATIONSHIP, events=MOCK_EVENTS,
        recent_story="", user_input="테스트", location="집",
        dt=datetime(2026, 5, 5, 20, 0),
    )
    assert "<operator_policy>" in fixed

@test("PromptBuilder: dynamic에 user_input 포함")
def _():
    from src.agents.prompt_factory.builder import PromptBuilder
    builder = PromptBuilder(
        world_config={"rating": "r18", "few_shot_examples": {}},
        char_name="은서", user_name="시안",
    )
    _, _, dynamic = builder.build(
        scene_types=["daily"], char_data=MOCK_CHAR_DATA,
        relationship=MOCK_RELATIONSHIP, events=MOCK_EVENTS,
        recent_story="", user_input="오늘 헬스장 어땠어?", location="집",
        dt=datetime(2026, 5, 5, 20, 0),
    )
    assert "오늘 헬스장 어땠어?" in dynamic

@test("PromptBuilder: intimate 씬 → genre에 intimate_protocol 포함")
def _():
    from src.agents.prompt_factory.builder import PromptBuilder
    builder = PromptBuilder(
        world_config={"rating": "r18", "few_shot_examples": {}},
        char_name="은서", user_name="시안",
    )
    _, genre, _ = builder.build(
        scene_types=["intimate"], char_data=MOCK_CHAR_DATA,
        relationship=MOCK_RELATIONSHIP, events=[],
        recent_story="", user_input="...", location="집",
        dt=datetime(2026, 5, 5, 22, 0),
    )
    assert "<intimate_protocol>" in genre

@test("PromptBuilder: 15등급 → R18 섹션 없음")
def _():
    from src.agents.prompt_factory.builder import PromptBuilder
    builder = PromptBuilder(
        world_config={"rating": "15", "few_shot_examples": {}},
        char_name="은서", user_name="시안",
    )
    fixed, genre, _ = builder.build(
        scene_types=["intimate"], char_data=MOCK_CHAR_DATA,
        relationship=MOCK_RELATIONSHIP, events=[],
        recent_story="", user_input="...", location="집",
        dt=datetime(2026, 5, 5, 22, 0),
    )
    assert "Adult creative writing" not in fixed
    assert genre == ""  # 15등급은 genre 섹션 없음

@test("PromptBuilder: dynamic에 날짜 헤더 포함 (YYYY년)")
def _():
    from src.agents.prompt_factory.builder import PromptBuilder
    builder = PromptBuilder(
        world_config={"rating": "r18", "few_shot_examples": {}},
        char_name="은서", user_name="시안",
    )
    _, _, dynamic = builder.build(
        scene_types=["daily"], char_data=MOCK_CHAR_DATA,
        relationship=MOCK_RELATIONSHIP, events=[],
        recent_story="", user_input="테스트", location="집",
        dt=datetime(2026, 5, 5, 10, 0),
    )
    # build_header가 **2026년 5월 5일... 형태로 주입
    assert "2026" in dynamic and "헬스장" in dynamic

@test("PromptBuilder: char_name 없으면 ValueError")
def _():
    from src.agents.prompt_factory.builder import PromptBuilder
    try:
        PromptBuilder(world_config={}, char_name=None, user_name="시안")
        assert False, "예외 미발생"
    except ValueError:
        pass


# ════════════════════════════════════════════════════════════
# 결과 출력
# ════════════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"  리팩토링 검증 (Step 4)")
print(f"{'=' * 60}")

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)

for name, ok, tb in results:
    status = PASS if ok else FAIL
    print(f"  {status}  {name}")
    if not ok:
        for line in tb.strip().splitlines():
            print(f"         {line}")

print(f"\n  결과: {passed} passed, {failed} failed / 총 {len(results)}개")
print(f"{'=' * 60}\n")

sys.exit(0 if failed == 0 else 1)
