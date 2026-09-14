# ================================
# tests/wiki_runtime_smoke_fixtures.py
#
# Shared prompt baselines, fake runtime hooks, and reusable handles for the split Wiki runtime smoke suites live here.
#
# Classes
#   - RuntimeConversationHandles : Carry the conversation, storage, and thread paths needed by later runtime smoke stages.
#
# Functions
#   - _fake_actor_events(**kwargs: object) -> AsyncIterator[dict] : Yield the fixed actor token and complete events used by the smoke tests.
#   - _fake_scene_classifier(user_input: str, recent_story: str, scene_descriptions: dict[str, str] | None = None) -> list[str] : Return scene types without external classification calls.
#   - _fake_pending_commit(documents: list[WikiDocument], user_input: str, actor_response: str, model_name: str, max_attempts: int = 3, player_profile_id: str = "", actor_profile_id: str = "", user_message_id: str | None = None, assistant_message_id: str | None = None, thinking_level: str | None = None, debug_root: Path | None = None) -> PendingWikiCommit : Build a deterministic scene patch commit.
#   - _failing_pending_commit(documents: list[WikiDocument], user_input: str, actor_response: str, model_name: str, max_attempts: int = 3, player_profile_id: str = "", actor_profile_id: str = "", user_message_id: str | None = None, assistant_message_id: str | None = None, thinking_level: str | None = None, debug_root: Path | None = None) -> PendingWikiCommit : Simulate updater retry exhaustion without an LLM call.
#   - _identity_repair(full_response: str, visible_text: str, state: ConversationState, documents: list[WikiDocument]) -> str : Bypass external repair calls during the smoke tests.
#   - copy_runtime_world(temporary_root: Path) -> Path : Copy the babe_university Wiki world fixture into a temporary vault root.
#   - configure_runtime_environment(temporary_root: Path, vault_root: Path) -> None : Point runtime modules at the temporary vault and install fake hooks.
#   - main() -> None : Print the standalone success marker for this shared module.
# ================================

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.models import ConversationState  # noqa: E402
from src.apps.app.storage import ConversationStore  # noqa: E402
import src.apps.app.conversation_lifecycle as conversation_lifecycle  # noqa: E402
import src.apps.app.runtime as app_runtime  # noqa: E402
import src.apps.app.service as app_service  # noqa: E402
import src.apps.app.wiki_branching as wiki_branching  # noqa: E402
import src.apps.app.wiki_controls as wiki_controls  # noqa: E402
import src.apps.app.wiki_message_ops as wiki_message_ops  # noqa: E402
import src.apps.app.wiki_service as wiki_service  # noqa: E402
import src.wiki as wiki_package  # noqa: E402
from src.wiki.markdown import document_revision, parse_markdown_sections  # noqa: E402
from src.wiki.models import PendingWikiCommit, SectionPatch, WikiDocument  # noqa: E402

_EXPECTED_PROMPT_SNAPSHOTS = {
    "lover": {
        "fixed": "0100994fab10408f063e6077659eba1badfd533a21a8d5cc39b2965fdd4c5dbc",
        "dynamic": "78ac12b6cd34bf0364e95dd20ed8062a23aba29dbce044abf59c99a71233560a",
    },
    "best_friends": {
        "fixed": "79445bb951486b99778873331e9144cd67b9424329d5de943eff168421a576f1",
        "dynamic": "ee5f12dccc0c4dc7243da5a2929799d5a474f5681106c94c5eed750600bd9f33",
    },
    "amputee_fwb": {
        "fixed": "a59f03e20bda0c1f7f3937e516e02f35945371027e21eb8252176e0b44698d2e",
        "dynamic": "a9454b8dafb21c585eff554adb22bf62d8f941290fe81463ac0523cdbff54cde",
    },
    "ntr_lite": {
        "fixed": "4d0400f35674c887d6f3dcf4ce798a3825acb0d648aef1f6a70b60a3c757d044",
        "dynamic": "a495edb4dffe6d98d002123cf04f3098153504172680d971bc8a13c9e741513a",
    },
    "altered": {
        "fixed": "b82d744cd1e6f320d0e4d24099267e094088ee756636219b4f03e2bd07df791a",
        "dynamic": "c1b87c307a79578a64019ecb2c8bf714c58fbf256735acb2b17368cdc314ae7d",
    },
    "boyfriend_platonic": {
        "fixed": "145817a07a6c0ba12f9d5fdec5e2aaafaa6731064f4f2b2a18d8bec94b636fde",
        "dynamic": "55a9d9dd1e3fb3ba8070308c989b4093d7bdc2a2e61ae8ead1956e577b5cf5b8",
    },
}

_OPENING_TAG_RE = re.compile(r"<[a-z_]+(?:\s+[^<>]+?)?>")

_FIXED_TAGS_12_CHARACTERS = (
    "<operator_policy>",
    "<user_impersonation>",
    "<simulation_core>",
    "<prose_profile>",
    "<world_lore>",
    "<world_setting>",
    *("<location_information>",) * 8,
    *("<organization_information>",) * 2,
    *("<character_profile>",) * 12,
    "<situation_information>",
    "<world_specific_prose_prompt>",
    "<prose_rules>",
    "<blacklist>",
    "<token_limit_constraint>",
    "<analyze>",
)
_FIXED_TAGS_13_CHARACTERS = (
    "<operator_policy>",
    "<user_impersonation>",
    "<simulation_core>",
    "<prose_profile>",
    "<world_lore>",
    "<world_setting>",
    *("<location_information>",) * 8,
    *("<organization_information>",) * 2,
    *("<character_profile>",) * 13,
    "<situation_information>",
    "<world_specific_prose_prompt>",
    "<prose_rules>",
    "<blacklist>",
    "<token_limit_constraint>",
    "<analyze>",
)
_DYNAMIC_TAGS_12_STATES = (
    "<active_characters>",
    "<world_context>",
    "<current_scene>",
    *("<current_character_state>",) * 12,
    "<current_relationship_state>",
    "<current_pov>",
    "<turn_ooc_directives>",
    *("<ooc>",) * 3,
    "<user_input>",
    "<output_contract>",
    "<analyze>",
)
_DYNAMIC_TAGS_13_STATES = (
    "<active_characters>",
    "<world_context>",
    "<current_scene>",
    *("<current_character_state>",) * 13,
    "<current_relationship_state>",
    "<current_pov>",
    "<turn_ooc_directives>",
    *("<ooc>",) * 3,
    "<user_input>",
    "<output_contract>",
    "<analyze>",
)
_EXPECTED_PROMPT_STRUCTURES = {
    "lover": {
        "fixed": _FIXED_TAGS_12_CHARACTERS,
        "dynamic": _DYNAMIC_TAGS_12_STATES,
    },
    "best_friends": {
        "fixed": _FIXED_TAGS_12_CHARACTERS,
        "dynamic": _DYNAMIC_TAGS_12_STATES,
    },
    "amputee_fwb": {
        "fixed": _FIXED_TAGS_12_CHARACTERS,
        "dynamic": _DYNAMIC_TAGS_12_STATES,
    },
    "ntr_lite": {
        "fixed": _FIXED_TAGS_13_CHARACTERS,
        "dynamic": _DYNAMIC_TAGS_13_STATES,
    },
    "altered": {
        "fixed": _FIXED_TAGS_13_CHARACTERS,
        "dynamic": _DYNAMIC_TAGS_13_STATES,
    },
    "boyfriend_platonic": {
        "fixed": _FIXED_TAGS_13_CHARACTERS,
        "dynamic": _DYNAMIC_TAGS_13_STATES,
    },
}

@dataclass
class RuntimeConversationHandles:
    """Carry the conversation, storage, and thread paths needed by later smoke stages."""

    state: ConversationState
    store: ConversationStore
    thread_root: Path
    scene_path: Path
    baseline_scene: str
    latest_user_id: str
    latest_assistant_id: str

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

def copy_runtime_world(temporary_root: Path) -> Path:
    """Copy the babe_university Wiki world fixture into a temporary vault root."""
    vault_root = temporary_root / "wiki_v2"
    shutil.copytree(
        Path("wiki_v2/worlds/babe_university"),
        vault_root / "worlds" / "babe_university",
    )
    return vault_root

def configure_runtime_environment(temporary_root: Path, vault_root: Path) -> None:
    """Point runtime modules at the temporary vault and install fake hooks."""
    app_runtime.WIKI_VAULT_ROOT = vault_root
    app_service.WIKI_VAULT_ROOT = vault_root
    conversation_lifecycle.WIKI_VAULT_ROOT = vault_root
    wiki_branching.WIKI_VAULT_ROOT = vault_root
    wiki_controls.WIKI_VAULT_ROOT = vault_root
    wiki_message_ops.WIKI_VAULT_ROOT = vault_root
    wiki_service.WIKI_VAULT_ROOT = vault_root
    wiki_service.classify_scene_types = _fake_scene_classifier
    wiki_service.stream_actor_events = _fake_actor_events
    wiki_service._repair_wiki_response = _identity_repair
    wiki_service.write_turn_debug_snapshot = lambda **kwargs: temporary_root / "debug"
    wiki_service.write_actor_raw_snapshot = lambda **kwargs: None
    wiki_package.plan_pending_commit = _fake_pending_commit

def main() -> None:
    """Print the standalone success marker for this shared module."""
    print("wiki_runtime_smoke_fixtures: ok")

if __name__ == "__main__":
    main()
