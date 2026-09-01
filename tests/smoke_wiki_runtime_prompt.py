# ================================
# tests/smoke_wiki_runtime_prompt.py
#
# Wiki runtime prompt smoke checks cover bootstrap contracts, scenario prompt assembly, authored prompt drift, and memory visibility.
#
# Functions
#   - _opening_tag_sequence(prompt: str) -> tuple[str, ...] : Normalize opening tag order into a prompt-structure fingerprint.
#   - _prompt_structure_snapshot(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str) -> dict[str, tuple[str, ...]] : Build the Fixed/Genre/Dynamic structure fingerprint.
#   - _report_prompt_content_snapshot(scenario_id: str, prompt_snapshot: dict[str, str]) -> None : Print content hashes used for authored-drift diagnostics.
#   - _assert_world_prompt_additions(vault_root: Path) -> None : Validate cot_append and blacklist inheritance behavior.
#   - _check_runtime_bootstrap(vault_root: Path) -> None : Validate classifier wiring, routes, runtime status, and opening-scene basics.
#   - _check_prompt_bundle_common(temporary_root: Path, vault_root: Path, scenario_id: str) -> tuple[object, object] : Validate common prompt bundle structure and isolation rules for one scenario.
#   - _check_prompt_bundle_scenario_specific(vault_root: Path, scenario_id: str, setup: object, prompt_bundle: object) -> None : Validate scenario-specific prompt rules for one scenario.
#   - _check_prompt_bundle_debug_and_materialization(temporary_root: Path, vault_root: Path, scenario_id: str, setup: object, prompt_bundle: object) -> None : Validate debug snapshots and materialized thread documents for one scenario.
#   - _check_memory_visibility(vault_root: Path, setup: object) -> None : Validate actor-only versus player-only memory visibility in prompt bundles.
#   - run_runtime_prompt_suite(temporary_root: Path, vault_root: Path) -> None : Run the full runtime prompt smoke suite.
#   - main() -> None : Run the standalone runtime prompt smoke suite.
# ================================

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.app import create_app  # noqa: E402
from src.apps.app.turn_debug import write_turn_debug_snapshot  # noqa: E402
from src.agents.manager.classifier import classify_scene_types  # noqa: E402
import src.apps.app.runtime as app_runtime  # noqa: E402
import src.apps.app.wiki_service as wiki_service  # noqa: E402
from src.wiki import (  # noqa: E402
    WikiPromptContractError,
    build_wiki_prompt_bundle,
    get_wiki_thread_runtime_status,
    initialize_wiki_conversation,
    parse_frontmatter,
    read_wiki_scene_descriptions,
    validate_wiki_prompt_bundle,
)
from src.wiki.context import scene_datetime_and_location  # noqa: E402
from src.wiki.document_creation import prepare_created_document  # noqa: E402
from src.wiki.models import CreateMemoryDocument  # noqa: E402
from src.wiki.store import WikiStore  # noqa: E402
from tests.wiki_runtime_smoke_fixtures import (  # noqa: E402
    _EXPECTED_PROMPT_SNAPSHOTS,
    _EXPECTED_PROMPT_STRUCTURES,
    configure_runtime_environment,
    copy_runtime_world,
    _OPENING_TAG_RE,
)

def _opening_tag_sequence(prompt: str) -> tuple[str, ...]:
    """Prompt segment의 opening tag 순서를 whitespace-normalized tuple로 반환합니다."""
    return tuple(" ".join(match.group(0).split()) for match in _OPENING_TAG_RE.finditer(prompt))

def _prompt_structure_snapshot(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
) -> dict[str, tuple[str, ...]]:
    """Compiled prompt의 Fixed/Genre/Dynamic 구조 fingerprint를 반환합니다."""
    return {
        "fixed": _opening_tag_sequence(fixed_prompt),
        "genre": _opening_tag_sequence(genre_prompt),
        "dynamic": _opening_tag_sequence(dynamic_prompt),
    }

def _report_prompt_content_snapshot(
    scenario_id: str,
    prompt_snapshot: dict[str, str],
) -> None:
    """Authoring drift 진단용 content hash와 baseline 차이를 출력합니다."""
    print(
        f"[prompt-content] {scenario_id}: "
        f"fixed={prompt_snapshot['fixed']} "
        f"genre={prompt_snapshot['genre']} "
        f"dynamic={prompt_snapshot['dynamic']}"
    )
    expected = _EXPECTED_PROMPT_SNAPSHOTS[scenario_id]
    if prompt_snapshot != expected:
        print(
            f"[authoring-drift] {scenario_id}: content hashes drifted from the "
            f"recorded authored baseline without changing prompt structure."
        )
        print(f"[authoring-drift] baseline={expected}")
        print(f"[authoring-drift] current={prompt_snapshot}")

def _assert_world_prompt_additions(vault_root: Path) -> None:
    """Wiki prompt 추가문의 시나리오 override와 월드 fallback을 검증합니다."""
    world_root = vault_root / "worlds" / "babe_university"
    world_cot = "- WIKI_WORLD_COT_SENTINEL"
    scenario_cot = "- WIKI_SCENARIO_COT_SENTINEL"
    world_blacklist = "- WIKI_WORLD_BLACKLIST_SENTINEL"
    (world_root / "cot_append.md").write_text(world_cot, encoding="utf-8")
    (world_root / "blacklist.md").write_text(world_blacklist, encoding="utf-8")
    scenario_cot_path = world_root / "scenarios" / "lover" / "cot_append.md"
    scenario_cot_path.write_text(scenario_cot, encoding="utf-8")
    setup = initialize_wiki_conversation(
        vault_root,
        "babe_university",
        "lover",
        "prompt_additions_smoke",
    )

    overridden = build_wiki_prompt_bundle(
        vault_root,
        setup,
        "테스트 입력",
        scene_types=["daily"],
    )
    assert overridden.fixed_prompt.count(world_blacklist) == 1
    assert overridden.dynamic_prompt.count(scenario_cot) == 1
    assert world_cot not in overridden.dynamic_prompt

    scenario_cot_path.unlink()
    inherited = build_wiki_prompt_bundle(
        vault_root,
        setup,
        "테스트 입력",
        scene_types=["daily"],
    )
    assert inherited.dynamic_prompt.count(world_cot) == 1
    assert scenario_cot not in inherited.dynamic_prompt
    (world_root / "cot_append.md").unlink()
    (world_root / "blacklist.md").unlink()
    shutil.rmtree(vault_root / "threads" / "prompt_additions_smoke")

SCENARIO_IDS = (
    "lover",
    "best_friends",
    "amputee_fwb",
    "ntr_lite",
    "altered",
    "boyfriend_platonic",
)

EXPECTED_FACTS = {
    "lover": "dated for two years",
    "best_friends": "They neither date nor cohabit",
    "amputee_fwb": "arms were amputated high through the upper arms",
    "ntr_lite": (
        "secretly continues a sexual relationship with her while she "
        "publicly dates 한도준"
    ),
    "altered": "Shifted Common Sense around 시안",
    "boyfriend_platonic": (
        "They are comfortable lifelong friends who live separately and "
        "have never dated or had sex."
    ),
}

SCENARIO_ONLY_RULES = {
    "amputee_fwb": "Distinguish what she can do independently",
    "ntr_lite": "Suspicion begins with observable inconsistencies",
    "altered": "Only social meaning and judgment around 시안 change",
    "boyfriend_platonic": (
        "A future change in any relationship requires direct choices and "
        "events; familiarity alone does not retroactively create romance, "
        "sex, betrayal, or secret consent."
    ),
}

async def _check_runtime_bootstrap(vault_root: Path) -> None:
    """Validate classifier wiring, routes, runtime status, and opening-scene basics."""
    assert await classify_scene_types("팬티를 벗긴다.", "") == ["intimate"]
    route_paths = {route.path for route in create_app().routes}
    assert {
        "/api/conversations/{thread_id}/wiki/commit",
        "/api/conversations/{thread_id}/wiki/commit/apply",
        "/api/conversations/{thread_id}/wiki/commit/retry",
        "/api/conversations/{thread_id}/wiki/commit/skip",
        "/api/conversations/{thread_id}/wiki/commits/{commit_id}/inverse",
        "/api/conversations/{thread_id}/wiki/commits/{commit_id}/inverse/apply",
        "/api/conversations/{thread_id}/wiki/branch/{message_id}",
        "/api/conversations/{thread_id}/wiki/title",
        "/api/conversations/{thread_id}/wiki/archive",
        "/api/conversations/{thread_id}/wiki/export",
        "/api/conversations/{thread_id}/wiki",
    }.issubset(route_paths)
    _assert_world_prompt_additions(vault_root)
    parsed_scene_time, _location = scene_datetime_and_location(
        "- 2026년 5월 22일 금요일, 07시 20분. 전세버스 내부이다."
    )
    assert parsed_scene_time == datetime(2026, 5, 22, 7, 20)
    profiles = app_runtime.discover_world_profiles("wiki")
    assert profiles[0]["id"] == "babe_university"
    assert profiles[0]["runtime_ready"] is True
    assert "충전 좀 해줘" in app_runtime.resolve_opening_scene(
        "babe_university",
        "lover",
        "wiki",
    )
    legacy_thread_root = vault_root / "threads" / "legacy_runtime"
    legacy_thread_root.mkdir(parents=True)
    legacy_status = get_wiki_thread_runtime_status(vault_root, "legacy_runtime")
    assert legacy_status.generation == "legacy"
    missing_status = get_wiki_thread_runtime_status(vault_root, "missing_runtime")
    assert missing_status.generation == "missing"

def _check_prompt_bundle_common(
    temporary_root: Path,
    vault_root: Path,
    scenario_id: str,
) -> tuple[object, object]:
    """Validate common prompt bundle structure and isolation rules for one scenario."""
    scene_descriptions = read_wiki_scene_descriptions(
        vault_root,
        "babe_university",
        scenario_id,
    )
    assert "daily" in scene_descriptions
    assert "intimate" in scene_descriptions
    assert ("altered" in scene_descriptions) == (scenario_id == "altered")
    setup = initialize_wiki_conversation(
        vault_root,
        "babe_university",
        scenario_id,
        f"prompt_check_{scenario_id}",
    )
    runtime_status = get_wiki_thread_runtime_status(
        vault_root,
        setup.thread_id,
    )
    assert runtime_status.generation == "current"
    assert runtime_status.format_version == 1
    if scenario_id == "lover":
        legacy_scene_path = (
            vault_root / "threads" / setup.thread_id / "scene" / "current.md"
        )
        legacy_scene_path.write_text(
            legacy_scene_path.read_text(encoding="utf-8").replace(
                "# 현재 장면",
                "# babe_university/lover 현재 장면",
                1,
            ),
            encoding="utf-8",
        )
        legacy_character_path = (
            vault_root / "threads" / setup.thread_id / "characters" / "sian.md"
        )
        legacy_character_path.write_text(
            legacy_character_path.read_text(encoding="utf-8")
            + "\n## 시나리오 격리\n\n- lover와 thread 내부 정보\n",
            encoding="utf-8",
        )
    prompt_bundle = build_wiki_prompt_bundle(
        vault_root,
        setup,
        "프롬프트 격리 확인",
        "",
    )
    combined_prompt = "\n".join(
        (
            prompt_bundle.fixed_prompt,
            prompt_bundle.genre_prompt,
            prompt_bundle.dynamic_prompt,
        )
    )
    prompt_snapshot = {
        "fixed": sha256(prompt_bundle.fixed_prompt.encode("utf-8")).hexdigest(),
        "genre": sha256(prompt_bundle.genre_prompt.encode("utf-8")).hexdigest(),
        "dynamic": sha256(prompt_bundle.dynamic_prompt.encode("utf-8")).hexdigest(),
    }
    prompt_structure = _prompt_structure_snapshot(
        prompt_bundle.fixed_prompt,
        prompt_bundle.genre_prompt,
        prompt_bundle.dynamic_prompt,
    )
    _report_prompt_content_snapshot(scenario_id, prompt_snapshot)
    assert prompt_structure == _EXPECTED_PROMPT_STRUCTURES[scenario_id], (
        scenario_id,
        prompt_structure,
    )
    assert EXPECTED_FACTS[scenario_id] in prompt_bundle.fixed_prompt
    for other_scenario_id, fact in EXPECTED_FACTS.items():
        if other_scenario_id != scenario_id:
            assert fact not in prompt_bundle.fixed_prompt
    for owner_scenario_id, rule in SCENARIO_ONLY_RULES.items():
        assert (rule in prompt_bundle.fixed_prompt) == (
            owner_scenario_id == scenario_id
        )
    assert "<prose_rules>" in prompt_bundle.fixed_prompt
    assert "<world_specific_prose_prompt>" in prompt_bundle.fixed_prompt
    assert prompt_bundle.fixed_prompt.count("<prose_rules>") == 1
    assert "## Shared History" in prompt_bundle.fixed_prompt
    assert "Founded in 1968" in prompt_bundle.fixed_prompt
    assert "플레이어 권한" not in prompt_bundle.fixed_prompt
    assert "역할 연기 계약" not in prompt_bundle.fixed_prompt
    assert "## 시작 기준" in prompt_bundle.dynamic_prompt
    assert "<current_scene>" in prompt_bundle.dynamic_prompt
    assert "<character_profile>" in prompt_bundle.fixed_prompt
    if scenario_id == "lover":
        expected_prompt_scenes = {
            "daily": "daily",
            "vulnerable": "bonding",
            "intimate": "intimate",
            "workplace": "formal",
            "tense": "tense",
            "conflict": "conflict",
            "physical": "action",
            "ambient": "ambient",
        }
        for raw_scene, prompt_scene in expected_prompt_scenes.items():
            scene_bundle = build_wiki_prompt_bundle(
                vault_root,
                setup,
                "장면 프롬프트 연결 확인",
                "",
                scene_types=[raw_scene],
            )
            assert scene_bundle.scene_types == [prompt_scene]
            assert (
                f'<scene type="{prompt_scene}">'
                in scene_bundle.dynamic_prompt
            )
            assert scene_bundle.fixed_prompt == prompt_bundle.fixed_prompt
            if prompt_scene == "intimate":
                assert '<genre name="intimate">' in scene_bundle.genre_prompt
                assert "Intimate Physicality in Ordinary Life" in (
                    scene_bundle.dynamic_prompt
                )
    assert "path=" not in combined_prompt
    assert ".md" not in combined_prompt
    assert "<wiki_" not in combined_prompt
    assert "scene/current.md" not in combined_prompt
    assert "characters/" not in combined_prompt
    assert "thread_id" not in combined_prompt
    assert "scenario_lore" not in combined_prompt
    assert "시나리오 특징" not in combined_prompt
    assert "시나리오 한정" not in combined_prompt
    assert "thread" not in combined_prompt.lower()
    assert "wiki" not in combined_prompt.lower()
    assert "scenario" not in combined_prompt.lower()
    assert setup.thread_id not in combined_prompt
    assert setup.world_id not in combined_prompt
    assert setup.pc_id not in combined_prompt
    assert setup.npc_id not in combined_prompt
    assert all(internal_id not in combined_prompt for internal_id in SCENARIO_IDS)
    assert all(
        placeholder not in combined_prompt
        for placeholder in (
            "아직 정해지지",
            "플레이 중 확정",
            "구체적인 값은 미정",
            "세부 값은 미정",
        )
    )
    return setup, prompt_bundle

def _check_prompt_bundle_scenario_specific(
    vault_root: Path,
    scenario_id: str,
    setup: object,
    prompt_bundle: object,
) -> None:
    """Validate scenario-specific prompt rules for one scenario."""
    if scenario_id == "altered":
        assert setup.pov_mode == "1p_char"
        assert setup.perspective == 1
        assert "Narration = first person." in prompt_bundle.fixed_prompt
        assert "Narrator = 진은서." in prompt_bundle.fixed_prompt
        assert "Born February 17, 1990" in prompt_bundle.fixed_prompt
        assert "character_profile:jung_woojin" not in prompt_bundle.fixed_prompt
        assert "They began dating on September 28, 2024" in prompt_bundle.fixed_prompt
        assert "Their relationship remains ongoing on May 22, 2026" in (
            prompt_bundle.fixed_prompt
        )
        assert "They have not broken up" in prompt_bundle.fixed_prompt
        assert "시안 has likewise never seen 우진" in prompt_bundle.fixed_prompt
        assert "They have never met in person, exchanged a call or message" in (
            prompt_bundle.fixed_prompt
        )
        assert "Learning 우진's name does not create familiarity" in (
            prompt_bundle.fixed_prompt
        )
        assert "scenario:altered" not in prompt_bundle.fixed_prompt
        assert "meets 은서 for the first time at 바베 피트니스 on May 22, 2026" in (
            prompt_bundle.fixed_prompt
        )
        assert "studies Mechanical Engineering at 바베대학교" not in prompt_bundle.fixed_prompt
        assert "has known 진은서 since childhood" not in prompt_bundle.fixed_prompt
        altered_scene_bundle = build_wiki_prompt_bundle(
            vault_root,
            setup,
            "시안의 요구가 일상의 판단을 바꾼다",
            "",
            scene_types=["altered"],
        )
        assert altered_scene_bundle.scene_types == ["altered"]
        assert "Practical Handling of 시안's Conduct" in (
            altered_scene_bundle.dynamic_prompt
        )
        assert "scene_prompt:" not in altered_scene_bundle.dynamic_prompt
    else:
        assert setup.pov_mode == "3p_char"
        assert setup.perspective == 3
        assert "Narration = third-person limited." in prompt_bundle.fixed_prompt
        assert "Born February 17, 1990" not in prompt_bundle.fixed_prompt
    if scenario_id == "lover":
        alternate_bundle = build_wiki_prompt_bundle(
            vault_root,
            setup,
            "다른 일상 입력",
            "평온한 직전 대화",
        )
        assert alternate_bundle.fixed_prompt == prompt_bundle.fixed_prompt
        assert alternate_bundle.genre_prompt == prompt_bundle.genre_prompt
        assert alternate_bundle.dynamic_prompt != prompt_bundle.dynamic_prompt
        poisoned_bundle = prompt_bundle.model_copy(
            update={
                "fixed_prompt": (
                    f"{prompt_bundle.fixed_prompt}\n[[hidden/path|alternate label]]"
                )
            }
        )
        try:
            validate_wiki_prompt_bundle(poisoned_bundle)
        except WikiPromptContractError:
            pass
        else:
            raise AssertionError("Fixed Wiki prompt wikilinks must be rejected")
        world_document_path = (
            vault_root / "worlds" / setup.world_id / "world.md"
        )
        world_document = world_document_path.read_text(encoding="utf-8")
        world_document_path.write_text(
            f"{world_document}\n\n[[hidden/path|alternate label]]\n",
            encoding="utf-8",
        )
        try:
            build_wiki_prompt_bundle(
                vault_root,
                setup,
                "문서 계약 확인",
                "",
            )
        except WikiPromptContractError:
            pass
        else:
            raise AssertionError(
                "Actor-visible Wiki document wikilinks must be rejected"
            )
        finally:
            world_document_path.write_text(
                world_document,
                encoding="utf-8",
            )
        intimate_bundle = build_wiki_prompt_bundle(
            vault_root,
            setup,
            "서로 동의한 성관계를 이어간다",
            "",
        )
        assert intimate_bundle.scene_types == ["intimate"]
        assert "current behavioral/verbal consent" in intimate_bundle.genre_prompt
        assert "arrangement ≠ blanket consent" in intimate_bundle.genre_prompt
        assert "Mind refuses" not in intimate_bundle.genre_prompt
        assert "forced bareback" not in intimate_bundle.genre_prompt
        assert "Non-consensual" not in intimate_bundle.genre_prompt

def _check_prompt_bundle_debug_and_materialization(
    temporary_root: Path,
    vault_root: Path,
    scenario_id: str,
    setup: object,
    prompt_bundle: object,
) -> None:
    """Validate debug snapshots and materialized thread documents for one scenario."""
    debug_effects = wiki_service._wiki_debug_effects(prompt_bundle, setup)
    assert debug_effects["wiki_context"]["start_state_materialized"] is True
    assert debug_effects["wiki_context"]["start_state_in_dynamic_prompt"] is True
    assert debug_effects["wiki_context"]["thread_generation"] == "current"
    assert debug_effects["wiki_context"]["thread_format_version"] == 1
    assert any(
        document["path"] == "scene/current.md"
        for document in debug_effects["updater_documents"]
    )
    if scenario_id == "lover":
        debug_directory = write_turn_debug_snapshot(
            user_input="프롬프트 격리 확인",
            fixed_prompt=prompt_bundle.fixed_prompt,
            genre_prompt=prompt_bundle.genre_prompt,
            dynamic_prompt=prompt_bundle.dynamic_prompt,
            scene_types=prompt_bundle.scene_types,
            manager_effects=debug_effects,
            history=[],
            world_id=setup.world_id,
            pc_id=setup.pc_id,
            npc_id=setup.npc_id,
            npc_name=setup.npc_name,
            logs_dir=temporary_root / "logs",
            turn_debug_dir=temporary_root / "turn_debug",
            actor_model="mock-actor",
        )
        assert debug_directory is not None
        summary = (Path(debug_directory) / "summary.md").read_text(
            encoding="utf-8"
        )
        assert "## Wiki Context" in summary
        assert '"start_state_in_dynamic_prompt": true' in summary
        assert "## Wiki Updater Documents" in summary
    materialized_eunseo = (
        vault_root
        / "threads"
        / setup.thread_id
        / "characters"
        / "eun_seo.md"
    ).read_text(encoding="utf-8")
    materialized_body = materialized_eunseo.split("---", 2)[-1]
    assert "### common" not in materialized_body
    assert "### default" not in materialized_body
    assert all(internal_id not in materialized_body for internal_id in SCENARIO_IDS)
    if scenario_id == "amputee_fwb":
        assert "arms were amputated high on the upper arm near the shoulders" in materialized_body
        assert "She is 147 cm tall, weighs 42 kg, has an F-cup bust" not in materialized_body
        amputee_intimate_bundle = build_wiki_prompt_bundle(
            vault_root,
            setup,
            "서로 확인하며 자세를 조정한다",
            "",
            scene_types=["intimate"],
        )
        assert "Intimacy with Physical Dependence" in (
            amputee_intimate_bundle.dynamic_prompt
        )
        assert "Intimate Physicality in Ordinary Life" not in (
            amputee_intimate_bundle.dynamic_prompt
        )
    else:
        assert "She is 147 cm tall, weighs 42 kg, has an F-cup bust" in materialized_body
        assert "legally dead" not in materialized_body
    relationship_slug = (
        f"{setup.npc_id.rsplit(':', 1)[-1]}--"
        f"{setup.pc_id.rsplit(':', 1)[-1]}.md"
    )
    relationship_path = (
        vault_root
        / "threads"
        / setup.thread_id
        / "relationships"
        / relationship_slug
    )
    relationship_content = relationship_path.read_text(encoding="utf-8")
    relationship_metadata = parse_frontmatter(relationship_content)
    assert relationship_metadata is not None
    assert relationship_metadata.owner == setup.npc_id
    assert relationship_metadata.participants == [setup.npc_id, setup.pc_id]
    assert (
        "No durable relationship change has occurred since the story began."
        in prompt_bundle.dynamic_prompt
    )

def _check_memory_visibility(vault_root: Path, setup: object) -> None:
    """Validate actor-only versus player-only memory visibility in prompt bundles."""
    memory_store = WikiStore(
        vault_root / "threads" / setup.thread_id
    )
    memory_created_at = datetime.now(timezone.utc)
    actor_memory = prepare_created_document(
        CreateMemoryDocument(
            document_type="memory",
            document_id="memory:actor-private-recollection",
            title="Actor Private Recollection",
            owner=setup.npc_id,
            related_event_id="event:visibility-check",
            formation_trigger="The Actor noticed a private clue.",
            formed_at="2026-05-22 22:00",
            location="성화오피스텔 307호",
            remembered_content="ACTOR_MEMORY_VISIBLE_MARKER",
            interpretation="The clue matters to the Actor alone.",
            emotion="Quiet concern.",
            certainty="High about seeing the clue.",
            distortion_risk="Its meaning may be overestimated later.",
            evidence="private actor clue",
            evidence_source="actor_response",
            confidence=0.9,
        ),
        setup.thread_id,
        "memory-visibility-test",
        memory_created_at,
        related_event_title="Visibility Check",
    )
    player_memory = prepare_created_document(
        CreateMemoryDocument(
            document_type="memory",
            document_id="memory:player-private-recollection",
            title="Player Private Recollection",
            owner=setup.pc_id,
            related_event_id="event:visibility-check",
            formation_trigger="The player noticed a different private clue.",
            formed_at="2026-05-22 22:00",
            location="성화오피스텔 307호",
            remembered_content="PLAYER_MEMORY_HIDDEN_MARKER",
            interpretation="The clue belongs to the player perspective.",
            emotion="Private uncertainty.",
            certainty="High about seeing the clue.",
            distortion_risk="Its meaning may be revised later.",
            evidence="private player clue",
            evidence_source="player_input",
            confidence=0.9,
        ),
        setup.thread_id,
        "memory-visibility-test",
        memory_created_at,
        related_event_title="Visibility Check",
    )
    memory_store.create_document(actor_memory.document, actor_memory.content)
    memory_store.create_document(player_memory.document, player_memory.content)
    memory_bundle = build_wiki_prompt_bundle(
        vault_root,
        setup,
        "기억 격리 확인",
        "",
    )
    assert "ACTOR_MEMORY_VISIBLE_MARKER" in memory_bundle.dynamic_prompt
    assert "PLAYER_MEMORY_HIDDEN_MARKER" not in memory_bundle.dynamic_prompt
    updater_paths = {
        document.path for document in memory_bundle.updater_documents
    }
    assert actor_memory.document in updater_paths
    assert player_memory.document in updater_paths

async def run_runtime_prompt_suite(temporary_root: Path, vault_root: Path) -> None:
    """Run the full runtime prompt smoke suite."""
    await _check_runtime_bootstrap(vault_root)
    for scenario_id in SCENARIO_IDS:
        setup, prompt_bundle = _check_prompt_bundle_common(
            temporary_root,
            vault_root,
            scenario_id,
        )
        _check_prompt_bundle_scenario_specific(
            vault_root,
            scenario_id,
            setup,
            prompt_bundle,
        )
        _check_prompt_bundle_debug_and_materialization(
            temporary_root,
            vault_root,
            scenario_id,
            setup,
            prompt_bundle,
        )
    _check_memory_visibility(vault_root, setup)

def main() -> None:
    """Run the standalone runtime prompt smoke suite."""
    with TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        vault_root = copy_runtime_world(temporary_root)
        configure_runtime_environment(temporary_root, vault_root)
        asyncio.run(run_runtime_prompt_suite(temporary_root, vault_root))

    print("smoke_wiki_runtime_prompt: ok")

if __name__ == "__main__":
    main()
