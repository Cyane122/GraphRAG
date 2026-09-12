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
        "fixed": "3ebdab16cf222eabcaba8d1c171125aca6f1c81a38ddddd26731f8d61d802719",
        "dynamic": "eea6bf9093ba8dd431f719b02ffe2a3d270766598b4e481f9e6737527a5c9cb8",
    },
    "best_friends": {
        "fixed": "197083eb3aae846d5a33c8897e9851c71653f06274d9fd072ef547df3cbdf34c",
        "dynamic": "4f6f0090e61c1d6de53ec9b9a7e0924f8ac1531d327d66d26b962eb9abfdf70c",
    },
    "amputee_fwb": {
        "fixed": "59b9ae725db3ef98ae74b1d6e697335aa57d575901ade05fb80ba0c2170873ca",
        "dynamic": "51dab38169e2b223d93c53816f7e316920159a42107a68d75b8056ad08b236c3",
    },
    "ntr_lite": {
        "fixed": "af5fc1a98decac302e4d578e7c4524395496a43a1a0a3644d4665f430ae8ead0",
        "dynamic": "f20ef7a83da73c672c8121de8be8bc1e084347b1f8a7bf9bbad7da30566d4aec",
    },
    "altered": {
        "fixed": "ad6b36ba95ce45b97be5ee54f5680b0d14c8062ca6d05bfd64ba4bcd9724d523",
        "dynamic": "8a5a2043062ebc29b063a96ecce4c6e296270128a7ea7f65b5e6afd1c6912d23",
    },
    "boyfriend_platonic": {
        "fixed": "79f4d2bb555829cc08e44c5c4735979e9f9fe62a04e4ece22357c7feb3900300",
        "dynamic": "a8c94472a223c9fad12917d9fdd277cba0b2cbe24ab248cd2ae21603e74a6d8f",
    },
}

_OPENING_TAG_RE = re.compile(r"<[a-z_]+(?:\s+[^<>]+?)?>")

_FIXED_TAGS_12_CHARACTERS = (
    "<operator_policy>",
    "<user_impersonation>",
    "<simulation_core>",
    "<prose_profile>",
    "<analyze>",
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
    "<analyze>",
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
