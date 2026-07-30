# ================================
# tests/smoke_wiki_runtime.py
#
# 실제 LLM 없이 Wiki 대화 생성, PromptBuilder 조립과 지연 commit lifecycle을 검증합니다.
#
# Functions
#   - _fake_actor_events(**kwargs: object) -> AsyncIterator[dict] : 고정 Actor 스트림을 반환합니다.
#   - _fake_scene_classifier(user_input: str, recent_story: str, scene_descriptions: dict[str, str] | None = None) -> list[str] : 외부 분류 호출 없이 장면 타입을 반환합니다.
#   - _fake_pending_commit(documents: list[WikiDocument], user_input: str, actor_response: str, model_name: str, max_attempts: int = 3, player_profile_id: str = "", actor_profile_id: str = "", user_message_id: str | None = None, assistant_message_id: str | None = None, thinking_level: str | None = None, debug_root: Path | None = None) -> PendingWikiCommit : scene patch 하나를 생성합니다.
#   - _failing_pending_commit(documents: list[WikiDocument], user_input: str, actor_response: str, model_name: str, max_attempts: int = 3, player_profile_id: str = "", actor_profile_id: str = "", user_message_id: str | None = None, assistant_message_id: str | None = None, thinking_level: str | None = None, debug_root: Path | None = None) -> PendingWikiCommit : Updater 재시도 소진을 모사합니다.
#   - _identity_repair(full_response: str, visible_text: str, state: ConversationState) -> str : 출력 repair를 우회합니다.
#   - _opening_tag_sequence(prompt: str) -> tuple[str, ...] : Prompt segment의 opening tag 순서를 구조 fingerprint로 정규화합니다.
#   - _prompt_structure_snapshot(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str) -> dict[str, tuple[str, ...]] : Fixed/Genre/Dynamic 구조 fingerprint를 반환합니다.
#   - _report_prompt_content_snapshot(scenario_id: str, prompt_snapshot: dict[str, str]) -> None : authored prose drift 진단용 content hash를 출력합니다.
#   - _run() -> None : 임시 vault에서 Wiki 런타임 전체 흐름을 검증합니다.
# ================================

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
import shutil
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.app import create_app
from src.apps.app.models import ConversationState
from src.apps.app.storage import ConversationStore
from src.apps.app.turn_debug import write_turn_debug_snapshot
from src.agents.manager.classifier import classify_scene_types
import src.apps.app.runtime as app_runtime
import src.apps.app.service as app_service
import src.apps.app.conversation_lifecycle as conversation_lifecycle
import src.apps.app.wiki_branching as wiki_branching
import src.apps.app.wiki_controls as wiki_controls
import src.apps.app.wiki_message_ops as wiki_message_ops
import src.apps.app.wiki_service as wiki_service
import src.wiki as wiki_package
from src.wiki.markdown import document_revision, parse_markdown_sections
from src.wiki.document_creation import prepare_created_document
from src.wiki.models import (
    CreateMemoryDocument,
    PendingWikiCommit,
    SectionPatch,
    WikiDocument,
)
from src.wiki.store import WikiStore
from src.wiki import (
    PendingCommitExists,
    WikiPromptContractError,
    build_wiki_prompt_bundle,
    get_wiki_thread_runtime_status,
    initialize_wiki_conversation,
    parse_frontmatter,
    read_wiki_scene_descriptions,
    validate_wiki_prompt_bundle,
)


_EXPECTED_PROMPT_SNAPSHOTS = {
    "lover": {
        "fixed": "54a6280c261ff43fc639e8fa3bcbd9db3ebe64a97575f86af71e467feb9bcd53",
        "genre": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "dynamic": "04917ce3ddbfb9a99e431b08a53c8f3e631123853bd2318bf924ea2acae08d49",
    },
    "best_friends": {
        "fixed": "b2a7ceb5a40f040e0b9872134d0b03fa8d1c6786d850301f1aef86023c535362",
        "genre": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "dynamic": "9658dfc1966887b56e45a78286735a8d8bbd2b6f23f11a183bd9d75472406a54",
    },
    "amputee_fwb": {
        "fixed": "76f1004e54269e5c83a0d2e00d459c872b179e96cc3ba6e934b5cd2a6d3a032d",
        "genre": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "dynamic": "7fd49764a6fd78f11522bdfae1521ea6419dec314bac9cbab1254d242e1c3a43",
    },
    "ntr_lite": {
        "fixed": "a5748152a9938c17370b85fa1d89c7313f947688332d4392af84ddd0c4dce146",
        "genre": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "dynamic": "d3621c763e9c915ac2156071caf94557ddcf84588a4f6eb61803ac4fe106cda0",
    },
    "altered": {
        "fixed": "3701d34f35c68423490c6c1772d5396a6f40042e7773583039115347b63799b8",
        "genre": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "dynamic": "13aadd8eba924555820240c995d445978aa3494f500399bddf14e36ca2f9aa73",
    },
    "boyfriend_platonic": {
        "fixed": "90dddb55a0590d6d206bf671b886ac60dc9dd55fe3744d2708ca9c6a83b7f608",
        "genre": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "dynamic": "10bd6d7c828894d4bb694ac507b8a6cc6da2102bff367c308fc1fcdeb0886199",
    },
}

_OPENING_TAG_RE = re.compile(r"<[a-z_]+(?:\s+[^<>]+?)?>")

_FIXED_TAGS_12_CHARACTERS = (
    "<operator_policy>",
    "<user_impersonation>",
    "<pov>",
    "<core>",
    "<emotion>",
    "<style>",
    "<world_lore>",
    "<world_setting>",
    *("<location_information>",) * 8,
    *("<organization_information>",) * 2,
    *("<character_profile>",) * 12,
    "<situation_information>",
    "<world_specific_prose_prompt>",
    "<prose_rules>",
    "<blacklist>",
    "<npc_behavior>",
    "<token_limit_constraint>",
    "<analyze>",
)
_FIXED_TAGS_13_CHARACTERS = (
    "<operator_policy>",
    "<user_impersonation>",
    "<pov>",
    "<core>",
    "<emotion>",
    "<style>",
    "<world_lore>",
    "<world_setting>",
    *("<location_information>",) * 8,
    *("<organization_information>",) * 2,
    *("<character_profile>",) * 13,
    "<situation_information>",
    "<world_specific_prose_prompt>",
    "<prose_rules>",
    "<blacklist>",
    "<npc_behavior>",
    "<token_limit_constraint>",
    "<analyze>",
)
_DYNAMIC_TAGS_12_STATES = (
    "<active_characters>",
    "<scene_specific_prompts>",
    '<scene type="daily">',
    "<world_context>",
    "<current_scene>",
    *("<current_character_state>",) * 12,
    "<current_relationship_state>",
    "<turn_ooc_directives>",
    *("<ooc>",) * 3,
    "<user_input>",
    *("<analyze>",) * 3,
)
_DYNAMIC_TAGS_13_STATES = (
    "<active_characters>",
    "<scene_specific_prompts>",
    '<scene type="daily">',
    "<world_context>",
    "<current_scene>",
    *("<current_character_state>",) * 13,
    "<current_relationship_state>",
    "<turn_ooc_directives>",
    *("<ooc>",) * 3,
    "<user_input>",
    *("<analyze>",) * 3,
)
_EXPECTED_PROMPT_STRUCTURES = {
    "lover": {
        "fixed": _FIXED_TAGS_12_CHARACTERS,
        "genre": (),
        "dynamic": _DYNAMIC_TAGS_12_STATES,
    },
    "best_friends": {
        "fixed": _FIXED_TAGS_12_CHARACTERS,
        "genre": (),
        "dynamic": _DYNAMIC_TAGS_12_STATES,
    },
    "amputee_fwb": {
        "fixed": _FIXED_TAGS_12_CHARACTERS,
        "genre": (),
        "dynamic": _DYNAMIC_TAGS_12_STATES,
    },
    "ntr_lite": {
        "fixed": _FIXED_TAGS_13_CHARACTERS,
        "genre": (),
        "dynamic": _DYNAMIC_TAGS_13_STATES,
    },
    "altered": {
        "fixed": _FIXED_TAGS_13_CHARACTERS,
        "genre": (),
        "dynamic": _DYNAMIC_TAGS_13_STATES,
    },
    "boyfriend_platonic": {
        "fixed": _FIXED_TAGS_13_CHARACTERS,
        "genre": (),
        "dynamic": _DYNAMIC_TAGS_13_STATES,
    },
}


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


async def _fake_actor_events(**kwargs: object) -> AsyncIterator[dict]:
    """LLM 호출 없이 token과 complete Actor 이벤트를 반환합니다."""
    assert "dated for two years" in str(kwargs["fixed_prompt"])
    assert "scenario_lore" not in str(kwargs["fixed_prompt"])
    assert "<user_input>" in str(kwargs["dynamic_prompt"])
    assert "충전 좀 해줘" in str(kwargs["dynamic_prompt"])
    assert "## 시작 기준" in str(kwargs["dynamic_prompt"])
    assert "은서 recently feared that 시안 valued only her body" in str(
        kwargs["dynamic_prompt"]
    )
    visible = "**2024년 3월 8일 금요일 08시 02분, 바베빌라 205호**\n\n은서가 고개를 끄덕였다."
    yield {"type": "token", "content": visible}
    yield {
        "type": "complete",
        "content": visible,
        "visible_text": visible,
        "raw_thinking": "",
        "scene_chars": [],
    }


async def _fake_scene_classifier(
    user_input: str,
    recent_story: str,
    scene_descriptions: dict[str, str] | None = None,
) -> list[str]:
    """외부 LLM 없이 intimate 키워드가 있으면 친밀 장면, 아니면 일상을 반환합니다."""
    del recent_story
    assert scene_descriptions is not None
    assert {"daily", "intimate"}.issubset(scene_descriptions)
    return ["intimate"] if "친밀" in user_input else ["daily"]


async def _fake_pending_commit(
    documents: list[WikiDocument],
    user_input: str,
    actor_response: str,
    model_name: str,
    max_attempts: int = 3,
    player_profile_id: str = "",
    actor_profile_id: str = "",
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
    thinking_level: str | None = None,
    debug_root: Path | None = None,
) -> PendingWikiCommit:
    """현재 scene의 당장 계기 섹션을 바꾸는 검증 가능한 commit을 반환합니다."""
    del (
        actor_response,
        max_attempts,
        player_profile_id,
        actor_profile_id,
        user_message_id,
        assistant_message_id,
        thinking_level,
        debug_root,
    )
    scene = next(document for document in documents if document.path == "scene/current.md")
    section_path = ("시작 기준", "Immediate Trigger")
    section = parse_markdown_sections(scene.content)[section_path]
    patch = SectionPatch(
        document=scene.path,
        base_revision=scene.revision,
        base_section_revision=document_revision(section.markdown),
        base_markdown=section.markdown,
        section_path=section_path,
        replacement_markdown=(
            "### Immediate Trigger\n\n"
            f"- Trigger: 첫 Actor 응답이 확정됨 ({sha256(user_input.encode('utf-8')).hexdigest()[:8]})"
        ),
        evidence="mocked accepted Actor response",
        confidence=1.0,
    )
    return PendingWikiCommit(
        user_input_hash="user",
        actor_response_hash="actor",
        updater_model=model_name,
        patches=[patch],
    )


async def _failing_pending_commit(
    documents: list[WikiDocument],
    user_input: str,
    actor_response: str,
    model_name: str,
    max_attempts: int = 3,
    player_profile_id: str = "",
    actor_profile_id: str = "",
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
    thinking_level: str | None = None,
    debug_root: Path | None = None,
) -> PendingWikiCommit:
    """Updater가 재시도를 모두 소진한 실패를 LLM 호출 없이 모사합니다."""
    del (
        documents,
        user_input,
        actor_response,
        model_name,
        max_attempts,
        player_profile_id,
        actor_profile_id,
        user_message_id,
        assistant_message_id,
        thinking_level,
        debug_root,
    )
    raise RuntimeError("mock updater exhausted")


async def _identity_repair(
    full_response: str,
    visible_text: str,
    state: ConversationState,
    documents: list[WikiDocument],
) -> str:
    """스모크 테스트에서 출력 repair 외부 호출을 생략합니다."""
    del visible_text, state, documents
    return full_response


async def _run() -> None:
    """임시 vault에서 생성, 조립, queue와 다음 입력 적용을 순서대로 검증합니다."""
    temporary_root = Path(tempfile.mkdtemp(prefix="wiki_runtime_"))
    try:
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
        vault_root = temporary_root / "wiki_v2"
        shutil.copytree(
            Path("wiki_v2/worlds/babe_university"),
            vault_root / "worlds" / "babe_university",
        )
        store = ConversationStore(temporary_root / "data" / "threads")
        app_runtime.WIKI_VAULT_ROOT = vault_root
        app_service.WIKI_VAULT_ROOT = vault_root
        conversation_lifecycle.WIKI_VAULT_ROOT = vault_root
        wiki_branching.WIKI_VAULT_ROOT = vault_root
        wiki_controls.WIKI_VAULT_ROOT = vault_root
        wiki_service.WIKI_VAULT_ROOT = vault_root
        wiki_service.classify_scene_types = _fake_scene_classifier
        wiki_service.stream_actor_events = _fake_actor_events
        wiki_service._repair_wiki_response = _identity_repair
        wiki_service.write_turn_debug_snapshot = lambda **kwargs: temporary_root / "debug"
        wiki_service.write_actor_raw_snapshot = lambda **kwargs: None
        wiki_package.plan_pending_commit = _fake_pending_commit

        profiles = app_runtime.discover_world_profiles("wiki")
        assert profiles[0]["id"] == "babe_university"
        assert profiles[0]["runtime_ready"] is True
        assert "충전 좀 해줘" in app_runtime.resolve_opening_scene(
            "babe_university",
            "lover",
            "wiki",
        )

        scenario_ids = (
            "lover",
            "best_friends",
            "amputee_fwb",
            "ntr_lite",
            "altered",
            "boyfriend_platonic",
        )
        expected_facts = {
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
        scenario_only_rules = {
            "amputee_fwb": "Distinguish what she can do independently",
            "ntr_lite": "Suspicion begins with observable inconsistencies",
            "altered": "Only social meaning and judgment around 시안 change",
            "boyfriend_platonic": (
                "A future change in any relationship requires direct choices and "
                "events; familiarity alone does not retroactively create romance, "
                "sex, betrayal, or secret consent."
            ),
        }
        legacy_thread_root = vault_root / "threads" / "legacy_runtime"
        legacy_thread_root.mkdir(parents=True)
        legacy_status = get_wiki_thread_runtime_status(
            vault_root,
            "legacy_runtime",
        )
        assert legacy_status.generation == "legacy"
        missing_status = get_wiki_thread_runtime_status(
            vault_root,
            "missing_runtime",
        )
        assert missing_status.generation == "missing"
        for scenario_id in scenario_ids:
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
            assert expected_facts[scenario_id] in prompt_bundle.fixed_prompt
            for other_scenario_id, fact in expected_facts.items():
                if other_scenario_id != scenario_id:
                    assert fact not in prompt_bundle.fixed_prompt
            for owner_scenario_id, rule in scenario_only_rules.items():
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
            assert all(internal_id not in combined_prompt for internal_id in scenario_ids)
            assert all(
                placeholder not in combined_prompt
                for placeholder in (
                    "아직 정해지지",
                    "플레이 중 확정",
                    "구체적인 값은 미정",
                    "세부 값은 미정",
                )
            )
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
            assert all(internal_id not in materialized_body for internal_id in scenario_ids)
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

        state = app_service.create_conversation(
            "babe_university",
            "lover",
            store,
            world_mode="wiki",
        )
        assert state.world_mode == "wiki"
        assert len(state.messages) == 1
        assert "충전 좀 해줘" in state.messages[0].content
        assert "충전 좀 해줘" in state.recent_responses[0]
        thread_root = vault_root / "threads" / state.thread_id
        scene_path = thread_root / "scene" / "current.md"
        before = scene_path.read_text(encoding="utf-8")
        manifest_path = thread_root / "thread.md"
        manifest_before = manifest_path.read_text(encoding="utf-8")
        initialize_wiki_conversation(
            vault_root,
            state.world_id,
            state.scenario_id or "default",
            state.thread_id,
        )
        assert manifest_path.read_text(encoding="utf-8") == manifest_before

        first_events = [
            event
            async for event in app_service.append_user_and_stream(
                state,
                "좋은 아침. 잘 잤어?",
                store,
            )
        ]
        assert first_events[-1]["wiki_update_status"] == "queued"
        assert state.wiki_update_status == "queued"
        assert state.wiki_pending_commit_id == first_events[-1]["pending_commit_id"]
        assert (thread_root / "commit.md").is_file()
        assert scene_path.read_text(encoding="utf-8") == before

        latest_assistant = state.messages[-1]
        latest_user = state.messages[-2]
        original_response = latest_assistant.content
        rerolled = await wiki_message_ops.reroll_wiki_assistant(
            state,
            latest_assistant.id,
            store,
            actor_model=state.actor_model,
        )
        assert rerolled["wiki_update_status"] == "queued"
        assert state.messages[-1].id == latest_assistant.id
        assert state.messages[-1].variants[0].content == original_response
        assert (thread_root / "commit.md").is_file()
        activated = await wiki_message_ops.activate_wiki_variant(
            state,
            latest_assistant.id,
            0,
            store,
        )
        assert activated["wiki_update_status"] == "queued"
        assert state.messages[-1].content == original_response
        edited_assistant = await wiki_message_ops.edit_wiki_message(
            state,
            latest_assistant.id,
            f"{original_response}\n\n수정된 응답.",
            store,
        )
        assert edited_assistant["wiki_update_status"] == "queued"
        assert state.messages[-1].edited is True
        edited_user = await wiki_message_ops.edit_wiki_message(
            state,
            latest_user.id,
            "좋은 아침. 물부터 마실래?",
            store,
            actor_model=state.actor_model,
        )
        assert edited_user["wiki_update_status"] == "queued"
        assert state.messages[-2].id == latest_user.id
        assert state.messages[-2].edited is True
        assert state.messages[-2].content == "좋은 아침. 물부터 마실래?"
        assert state.messages[-1].parent_user_id == latest_user.id

        status = wiki_controls.get_wiki_commit_status(state)
        assert status.update_status == "queued"
        assert status.commit is not None
        assert status.wiki_thread_generation == "current"
        try:
            await wiki_controls.retry_wiki_update(state, store)
        except PendingCommitExists:
            pass
        else:
            raise AssertionError("Retry must not overwrite a pending commit")

        skipped = wiki_controls.skip_wiki_commit(
            state,
            store,
            "플레이어가 이번 변경을 폐기함",
        )
        assert skipped.update_status == "skipped"
        assert skipped.commit is not None
        assert skipped.commit["status"] == "skipped"
        assert not (thread_root / "commit.md").exists()
        assert scene_path.read_text(encoding="utf-8") == before

        retried = await wiki_controls.retry_wiki_update(state, store)
        assert retried.update_status == "queued"
        assert retried.commit is not None
        assert retried.commit["user_message_id"] == state.messages[-2].id
        assert retried.commit["assistant_message_id"] == state.messages[-1].id
        assert state.messages[-1].wiki_commit_id == retried.commit["commit_id"]
        assert (thread_root / "commit.md").is_file()

        second_events = [
            event
            async for event in app_service.append_user_and_stream(
                state,
                "식빵부터 구울까?",
                store,
            )
        ]
        assert second_events[-1]["type"] == "complete"
        assert "첫 Actor 응답이 확정됨" in scene_path.read_text(encoding="utf-8")
        assert list((thread_root / "commits").glob("*.md"))
        assert (thread_root / "commit.md").is_file()
        try:
            await wiki_message_ops.reroll_wiki_assistant(
                state,
                latest_assistant.id,
                store,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Committed historical Wiki responses must not reroll")
        applied_now = wiki_controls.apply_wiki_commit_now(state, store)
        assert applied_now.update_status == "applied"
        assert applied_now.commit is None
        assert not (thread_root / "commit.md").exists()

        # 후속 턴이 없는 최신 applied 응답은 inverse 후 안전하게 수정할 수 있다.
        latest_applied_assistant = state.messages[-1]
        assert latest_applied_assistant.wiki_commit_id is not None
        edited_applied = await wiki_message_ops.edit_wiki_message(
            state,
            latest_applied_assistant.id,
            f"{latest_applied_assistant.content}\n\n적용 후 수정.",
            store,
        )
        assert edited_applied["wiki_update_status"] == "queued"
        assert state.messages[-1].wiki_commit_id == state.wiki_pending_commit_id
        assert any(
            '"operation": "inverse"' in path.read_text(encoding="utf-8")
            for path in (thread_root / "commits").glob("*.md")
        )
        assert wiki_controls.apply_wiki_commit_now(state, store).update_status == "applied"

        # 중간 과거 턴은 원본을 건드리지 않고 턴 직전 상태로 새 thread를 만든다.
        source_scene_before_branch = scene_path.read_text(encoding="utf-8")
        branch_result = wiki_branching.branch_wiki_conversation_before_message(
            state,
            latest_user.id,
            store,
        )
        branch = branch_result.conversation
        branch_root = vault_root / "threads" / branch.thread_id
        assert branch.thread_id != state.thread_id
        assert branch_result.draft == latest_user.content
        assert len(branch.messages) == 1
        assert branch.messages[0].role == "assistant"
        assert not (branch_root / "commit.md").exists()
        assert scene_path.read_text(encoding="utf-8") == source_scene_before_branch
        branch_scene = (branch_root / "scene" / "current.md").read_text(
            encoding="utf-8"
        )
        branch_trigger = parse_markdown_sections(branch_scene)[
            ("시작 기준", "Immediate Trigger")
        ].markdown
        baseline_trigger = parse_markdown_sections(before)[
            ("시작 기준", "Immediate Trigger")
        ].markdown
        assert branch_trigger.rstrip() == baseline_trigger.rstrip(), (
            f"branch trigger mismatch:\n{branch_trigger}\n--- baseline ---\n"
            f"{baseline_trigger}"
        )
        branch_manifest_metadata = parse_frontmatter(
            (branch_root / "thread.md").read_text(encoding="utf-8")
        )
        assert branch_manifest_metadata is not None
        assert branch.thread_id in branch_manifest_metadata.id
        assert state.thread_id not in branch_manifest_metadata.id
        assert f'"thread_id": "{branch.thread_id}"' in (
            branch_root / ".wikirag-runtime.json"
        ).read_text(encoding="utf-8")

        # 연결 archive가 없으면 불완전한 branch를 남기지 않고 중단한다.
        broken_state = state.model_copy(deep=True)
        broken_assistant = next(
            message
            for message in broken_state.messages
            if message.parent_user_id == latest_user.id
        )
        broken_assistant.wiki_commit_id = "missing_commit_archive"
        thread_directories_before = {
            path.name for path in (vault_root / "threads").iterdir()
        }
        try:
            wiki_branching.branch_wiki_conversation_before_message(
                broken_state,
                latest_user.id,
                store,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Missing applied archives must abort safe branching")
        assert {
            path.name for path in (vault_root / "threads").iterdir()
        } == thread_directories_before

        # Wiki 대화 lifecycle은 이름·보관·ZIP 내보내기와 thread 삭제를 동기화한다.
        renamed_branch = conversation_lifecycle.rename_wiki_conversation(
            branch,
            "아침 장면 분기",
            store,
        )
        assert renamed_branch.title == "아침 장면 분기"
        assert conversation_lifecycle.set_wiki_conversation_archived(
            renamed_branch,
            True,
            store,
        ).archived is True
        export_bytes, export_filename = (
            conversation_lifecycle.export_wiki_conversation(renamed_branch)
        )
        assert export_filename.endswith(".zip")
        with ZipFile(BytesIO(export_bytes)) as exported:
            exported_names = set(exported.namelist())
            assert "conversation.json" in exported_names
            assert "wiki_thread/thread.md" in exported_names
            assert not any("debug/" in name for name in exported_names)
            assert not any(name.endswith(".wiki_commit.lock") for name in exported_names)
        conversation_lifecycle.delete_wiki_conversation(
            renamed_branch,
            store,
        )
        assert not store.exists(renamed_branch.thread_id)
        assert not branch_root.exists()

        wiki_package.plan_pending_commit = _failing_pending_commit
        failed_events = [
            event
            async for event in app_service.append_user_and_stream(
                state,
                "Updater 실패 상태를 확인한다.",
                store,
            )
        ]
        assert failed_events[-1]["wiki_update_status"] == "failed"
        assert not (thread_root / "commit.md").exists()
        saved = store.load(state.thread_id)
        assert saved.world_mode == "wiki"
        assert saved.wiki_update_status == "failed"
        assert "mock updater exhausted" in saved.wiki_update_error

        wiki_package.plan_pending_commit = _fake_pending_commit
        recovered = await wiki_controls.retry_wiki_update(saved, store)
        assert recovered.update_status == "queued"
        assert recovered.commit is not None
        wiki_controls.skip_wiki_commit(saved, store, "스모크 테스트 정리")
        deleted_assistant_id = saved.messages[-1].id
        deleted = wiki_message_ops.delete_wiki_message(
            saved,
            deleted_assistant_id,
            store,
        )
        assert all(
            message["id"] != deleted_assistant_id
            for message in deleted["messages"]
        )
        assert not (thread_root / "commit.md").exists()
    finally:
        shutil.rmtree(temporary_root)


if __name__ == "__main__":
    asyncio.run(_run())
    print("smoke_wiki_runtime: ok")
