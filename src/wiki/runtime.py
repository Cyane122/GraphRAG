# ================================
# src/wiki/runtime.py
#
# Wiki Markdown을 기존 PromptBuilder와 지연 커밋 흐름에 연결합니다.
#
# Functions
#   - initialize_wiki_conversation(vault_root: Path, world_id: str, scenario_id: str, thread_id: str) -> WikiConversationSetup : Wiki thread를 초기화합니다.
#   - resolve_wiki_opening_scene(vault_root: Path, world_id: str, scenario_id: str) -> str : 선택 시나리오의 첫 장면 원문을 반환합니다.
#   - build_wiki_prompt_bundle(vault_root: Path, setup: WikiConversationSetup, user_input: str, recent_story: str = "", turn_ooc_directives: str = "", scene_types: list[str] | None = None) -> WikiPromptBundle : 기존 PromptBuilder로 Actor prompt를 조립합니다.
#   - apply_pending_wiki_commit(vault_root: Path, thread_id: str) -> PendingWikiCommit | None : 다음 입력 직전 commit.md를 적용합니다.
# ================================

from __future__ import annotations

from pathlib import Path
import re

from src.agents.context.scene_keys import normalize_prompt_scene_types
from src.agents.prompt_factory import PromptBuilder
from src.config import (
    WIKI_ACTOR_RECALL_BUDGET,
    WIKI_ACTOR_RECALL_TOKEN_BUDGET,
)
from src.simulation.systems.world_dynamics.organic_models import (
    normalize_contraception_value,
)
from src.wiki.commit import WikiCommitQueue
from src.wiki.context import (
    document_body,
    initialize_wiki_thread,
    load_wiki_setup,
    read_wiki_actor_assets,
    read_wiki_scene_prompt_assets,
    read_wiki_thread_documents,
    scene_datetime_and_location,
)
from src.wiki.markdown import parse_markdown_sections
from src.wiki.models import (
    PendingWikiCommit,
    WikiConversationSetup,
    WikiDocument,
    WikiMetadata,
    WikiPromptBundle,
    WikiScenePromptAsset,
)
from src.wiki.recall import select_recall_documents
from src.wiki.prompt_contract import (
    validate_actor_document_body,
    validate_wiki_prompt_bundle,
)
from src.wiki.store import WikiStore


_INTIMATE_RE = re.compile(
    r"자지|보지|섹스|성관계|삽입|애무|절정|오르가슴|발기|신음|팬티|브래지어|알몸|나체"
)
_ACTOR_METADATA_RE = re.compile(r"\b(?:wiki|thread|scenario)\b|시나리오", re.IGNORECASE)
_ACTOR_FILE_REFERENCE_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.md\b")
_CURRENT_STATE_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_CURRENT_STATE_FIELD_RE = re.compile(r"^-\s*([^:\n]+):\s*(.*?)\s*$")
_REPRODUCTIVE_STATE_SECTION_PATH = ("현재 상태", "Reproductive State")
_PROMPT_DIR = Path(__file__).parent / "prompts"
_PROTECTION_OOC_PROMPT_PATH = _PROMPT_DIR / "protection_ooc.md"


def initialize_wiki_conversation(
    vault_root: Path,
    world_id: str,
    scenario_id: str,
    thread_id: str,
) -> WikiConversationSetup:
    """Wiki thread와 초기 Markdown 상태를 만들고 앱용 설정을 반환합니다."""
    return initialize_wiki_thread(vault_root, world_id, scenario_id, thread_id)


def resolve_wiki_opening_scene(
    vault_root: Path,
    world_id: str,
    scenario_id: str,
) -> str:
    """thread 생성 없이 선택한 시나리오의 첫 장면 원문을 반환합니다."""
    preview_thread_id = "opening_preview"
    return load_wiki_setup(
        vault_root,
        world_id,
        scenario_id,
        preview_thread_id,
    ).opening_scene


def _render_document_block(label: str, document: WikiDocument, body: str | None = None) -> str:
    """문서 저장 위치를 노출하지 않고 본문만 의미 기반 XML 블록으로 감쌉니다."""
    rendered_body = document_body(document.content) if body is None else body.strip()
    rendered_body = _remove_actor_metadata(rendered_body)
    if not rendered_body:
        return ""
    validate_actor_document_body(rendered_body, document)
    return f"<{label}>\n{rendered_body}\n</{label}>"


def _remove_actor_metadata(body: str) -> str:
    """저장소 구조를 설명하는 행과 그 전용 하위 섹션을 Actor 본문에서 제거합니다."""
    rendered: list[str] = []
    blocked_heading_depth: int | None = None
    for line in body.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if blocked_heading_depth is not None:
            if heading is None or len(heading.group(1)) > blocked_heading_depth:
                continue
            blocked_heading_depth = None
        if heading is not None and (
            _ACTOR_METADATA_RE.search(heading.group(2))
            or _ACTOR_FILE_REFERENCE_RE.search(heading.group(2))
        ):
            blocked_heading_depth = len(heading.group(1))
            continue
        if _ACTOR_METADATA_RE.search(line) or _ACTOR_FILE_REFERENCE_RE.search(line):
            continue
        rendered.append(line)
    return "\n".join(rendered).strip()


def _situation_rules_body(document: WikiDocument) -> str:
    """작성용 scenario 포장 제목을 제거하고 현재 상황의 사실과 규칙만 반환합니다."""
    omitted_titles = {
        "시나리오 특징과 묘사 규정",
        "시나리오 특징",
        "시나리오 한정 묘사 규정",
    }
    rendered: list[str] = []
    for line in document_body(document.content).splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match is None:
            rendered.append(line)
            continue
        depth = len(match.group(1))
        title = match.group(2).strip()
        if title in omitted_titles:
            continue
        rendered.append(f"{'#' * max(1, depth - 1)} {title}" if depth >= 3 else line)
    return "\n".join(rendered).strip()


def _scene_prompt_body(asset: WikiScenePromptAsset) -> str:
    """선택된 scene prompt에서 저장 metadata를 제거하고 독립 본문만 반환합니다."""
    body = _remove_actor_metadata(document_body(asset.document.content))
    validate_actor_document_body(body, asset.document)
    return body


def _static_block_label(document: WikiDocument) -> str:
    """정적 문서의 저장 형식 대신 Actor가 이해할 의미 기반 XML 이름을 반환합니다."""
    document_type = document.metadata.type if document.metadata is not None else "world"
    return {
        "world": "world_setting",
        "prose": "prose_rules",
        "location": "location_information",
        "organization": "organization_information",
    }.get(document_type, "world_information")


def _character_fixed_body(document: WikiDocument) -> str:
    """thread character에서 변경 가능한 현재 상태를 제외한 고정 본문을 반환합니다."""
    sections = parse_markdown_sections(document.content)
    current = sections.get(("현재 상태",))
    body = document_body(document.content)
    if current is None:
        return body
    current_markdown = current.markdown.strip()
    return body.replace(current_markdown, "", 1).strip()


def _omit_current_state_subsection(markdown: str, heading_title: str) -> str:
    """현재 상태 H2에서 지정한 H3 subsection 전체를 Actor 본문에서 제거합니다."""
    lines = markdown.splitlines()
    heading_indexes = [
        index
        for index, line in enumerate(lines)
        if _CURRENT_STATE_H3_RE.fullmatch(line) is not None
    ]
    if not heading_indexes:
        return markdown.strip()
    filtered_lines = lines[: heading_indexes[0]]
    for position, start in enumerate(heading_indexes):
        end = heading_indexes[position + 1] if position + 1 < len(heading_indexes) else len(lines)
        block = lines[start:end]
        heading = _CURRENT_STATE_H3_RE.fullmatch(block[0])
        if heading is not None and heading.group(1).strip() == heading_title:
            continue
        filtered_lines.extend(block)
    return "\n".join(filtered_lines).strip()


def _actor_character_document(
    documents: list[WikiDocument],
    actor_profile_id: str,
) -> WikiDocument | None:
    """활성 Actor profile에 대응하는 thread character 문서를 반환합니다."""
    return next(
        (
            document
            for document in documents
            if document.metadata is not None
            and document.metadata.type == "character"
            and document.metadata.profile_id == actor_profile_id
        ),
        None,
    )


def _parse_reproductive_state_int(value: str) -> int | None:
    """Reproductive State 정수 필드를 안전하게 변환하고 malformed input은 거부합니다."""
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


def _actor_cycle_dynamic_state(
    documents: list[WikiDocument],
    actor_profile_id: str,
) -> dict[str, object] | None:
    """활성 Actor character의 canonical Reproductive State를 checklist 입력으로 변환합니다."""
    character = _actor_character_document(documents, actor_profile_id)
    if character is None:
        return None
    section = parse_markdown_sections(character.content).get(_REPRODUCTIVE_STATE_SECTION_PATH)
    if section is None:
        return None
    fields: dict[str, str] = {}
    for line in section.markdown.splitlines()[1:]:
        if not line.strip():
            continue
        match = _CURRENT_STATE_FIELD_RE.fullmatch(line)
        if match is None:
            return None
        label = match.group(1).strip()
        if label in fields:
            return None
        fields[label] = match.group(2).strip()
    if fields.get("Menstrual cycle", "").lower() != "enabled":
        return None
    cycle_day = _parse_reproductive_state_int(fields.get("Cycle day", ""))
    pregnancy_day = _parse_reproductive_state_int(fields.get("Pregnancy day", ""))
    pregnant_text = fields.get("Pregnant", "").strip().lower()
    contraception = normalize_contraception_value(fields.get("Contraception"))
    if cycle_day is None or pregnancy_day is None or pregnant_text not in {"yes", "no"}:
        return None
    return {
        "has_menstrual_cycle": True,
        "contraception": contraception,
        "cycle_day": cycle_day,
        "pregnant": pregnant_text == "yes",
        "pregnancy_day": pregnancy_day,
    }


def _character_dynamic_body(document: WikiDocument) -> str:
    """thread character의 현재 상태 섹션을 Actor 표시 규칙에 맞춰 반환합니다."""
    section = parse_markdown_sections(document.content).get(("현재 상태",))
    if section is None:
        return ""
    return _omit_current_state_subsection(section.markdown, "Reproductive State")


def _scene_actor_body(document: WikiDocument) -> str:
    """과거에 생성된 장면에서도 내부 식별자가 들어간 H1을 의미 제목으로 교체합니다."""
    return re.sub(
        r"^#\s+.+?$",
        "# 현재 장면",
        document_body(document.content),
        count=1,
        flags=re.MULTILINE,
    )


def _scene_types(user_input: str, recent_story: str) -> list[str]:
    """추가 LLM 호출 없이 일상/친밀 장면의 최소 genre 분기를 반환합니다."""
    return ["intimate"] if _INTIMATE_RE.search(f"{recent_story[-600:]}\n{user_input}") else ["daily"]


def _world_config(
    setup: WikiConversationSetup,
    assets: list[WikiDocument],
    thread_documents: list[WikiDocument],
    scene_prompts: list[WikiScenePromptAsset],
) -> dict:
    """Markdown 자산을 기존 PromptBuilder가 이해하는 world_config로 변환합니다."""
    scenario_document = next(
        document for document in assets if document.path.endswith("/scenario.md")
    )
    prose_document = next(
        document
        for document in assets
        if document.metadata is not None and document.metadata.type == "prose"
    )
    world_parts = [
        _render_document_block(_static_block_label(document), document)
        for document in assets
        if document is not scenario_document
        and document is not prose_document
        and document.metadata is not None
        and "actor" in document.metadata.visibility
    ]
    world_parts.extend(
        _render_document_block("character_profile", document, _character_fixed_body(document))
        for document in thread_documents
        if document.metadata is not None and document.metadata.type == "character"
    )
    world_parts.append(
        _render_document_block(
            "situation_information",
            scenario_document,
            _situation_rules_body(scenario_document),
        )
    )
    return {
        "rating": setup.rating,
        "pov_mode": setup.pov_mode,
        "pc_name_kor": setup.pc_name,
        "npc_name_kor": setup.npc_name,
        "unified_blacklist": False,
        "scene_specific_prompts": {
            asset.scene_type: _scene_prompt_body(asset)
            for asset in scene_prompts
        },
        "prompt": {
            "pov": {"mode": setup.pov_mode},
            "sections": {
                "world": "\n\n".join(part for part in world_parts if part),
                "scenario": "",
                "prose": _render_document_block("prose_rules", prose_document),
            },
        },
    }


def _actor_document_visible(
    metadata: WikiMetadata | None,
    actor_profile_id: str,
) -> bool:
    """활성 NPC가 볼 수 있는 문서인지 visibility와 owner/knower scoping으로 판별합니다.

    goal/item/memory/relationship은 owner NPC만, secret은 knower(owner+knowers)만
    노출한다. Actor가 모르는 문서는 무지를 유지하도록 제외한다.
    """
    if metadata is None or "actor" not in metadata.visibility:
        return False
    if metadata.type in {"memory", "relationship", "goal", "item"} and (
        metadata.owner is not None
        and metadata.owner != actor_profile_id
    ):
        return False
    if metadata.type == "secret":
        knowers = set(getattr(metadata, "knowers", None) or [])
        if metadata.owner is not None:
            knowers.add(metadata.owner)
        if actor_profile_id not in knowers:
            return False
    return True


def _dynamic_context(
    documents: list[WikiDocument],
    recent_story: str,
    actor_profile_id: str,
    scene_text: str,
) -> dict[str, str]:
    """Actor가 볼 수 있는 thread Markdown을 recall 예산 내에서 Dynamic context로 나눕니다."""
    visible = [
        document
        for document in documents
        if _actor_document_visible(document.metadata, actor_profile_id)
    ]
    # 누적 문서가 예산을 넘으면 관련성·최근성 상위만 남긴다. Actor는 정밀도를 위해 좁게 잡는다.
    selected = select_recall_documents(
        visible,
        active_profile_ids={actor_profile_id},
        scene_text=scene_text,
        budget=WIKI_ACTOR_RECALL_BUDGET,
        token_budget=WIKI_ACTOR_RECALL_TOKEN_BUDGET,
    )
    scene_parts: list[str] = []
    state_parts: list[str] = []
    other_parts: list[str] = []
    for document in selected:
        metadata = document.metadata
        if metadata is None:
            continue
        if metadata.type == "scene":
            scene_parts.append(
                _render_document_block("current_scene", document, _scene_actor_body(document))
            )
        elif metadata.type == "character":
            block = _render_document_block(
                "current_character_state",
                document,
                _character_dynamic_body(document),
            )
            if block:
                state_parts.append(block)
        elif metadata.type == "thread":
            continue
        else:
            other_parts.append(
                _render_document_block(f"current_{metadata.type}_state", document)
            )
    context = {
        "scene": "\n\n".join(scene_parts),
        "state": "\n\n".join(state_parts),
        "world": "\n\n".join(other_parts),
    }
    if recent_story.strip():
        context["narrative_log"] = (
            "<recent_story>\n"
            f"{recent_story.strip()}\n"
            "</recent_story>"
        )
    return context


def _wiki_turn_ooc_directives(turn_ooc_directives: str) -> str:
    """호출자 지시와 Wiki 전용 Protection bookkeeping 지시를 함께 반환합니다."""
    asset_directives = _PROTECTION_OOC_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "\n\n".join(
        part
        for part in [str(turn_ooc_directives or "").strip(), asset_directives]
        if part
    )


def build_wiki_prompt_bundle(
    vault_root: Path,
    setup: WikiConversationSetup,
    user_input: str,
    recent_story: str = "",
    turn_ooc_directives: str = "",
    scene_types: list[str] | None = None,
) -> WikiPromptBundle:
    """최신 Markdown을 읽어 기존 PromptBuilder의 Fixed/Genre/Dynamic을 조립합니다."""
    assets = read_wiki_actor_assets(vault_root, setup.world_id, setup.scenario_id)
    thread_documents = read_wiki_thread_documents(vault_root, setup.thread_id)
    scene_document = next(
        document
        for document in thread_documents
        if document.metadata is not None and document.metadata.type == "scene"
    )
    current_dt, location = scene_datetime_and_location(scene_document.content)
    selected_scene_types = normalize_prompt_scene_types(
        scene_types or _scene_types(user_input, recent_story)
    )
    scene_prompts = read_wiki_scene_prompt_assets(
        vault_root,
        setup.world_id,
        setup.scenario_id,
        selected_scene_types,
    )
    world_config = _world_config(setup, assets, thread_documents, scene_prompts)
    builder = PromptBuilder(
        world_config=world_config,
        char_name=setup.npc_name,
        user_name=setup.pc_name,
        perspective=setup.perspective,
    )
    char_data = {"id": setup.npc_name, "name": setup.npc_name}
    if (dynamic_state := _actor_cycle_dynamic_state(thread_documents, setup.npc_id)) is not None:
        char_data["dynamic_state"] = dynamic_state
    user_data = {"id": setup.pc_name, "name": setup.pc_name}
    fixed, genre, dynamic = builder.build(
        scene_types=selected_scene_types,
        char_data=char_data,
        recent_story=recent_story,
        user_input=user_input,
        location=location,
        dt=current_dt,
        user_data=user_data,
        rendered_context=_dynamic_context(
            thread_documents,
            recent_story,
            setup.npc_id,
            scene_document.content,
        ),
        current_pov={
            "selected": {
                "id": setup.npc_name,
                "name": setup.npc_name,
                "hide_metadata": True,
            }
        },
        turn_ooc_directives=_wiki_turn_ooc_directives(turn_ooc_directives),
    )
    bundle = WikiPromptBundle(
        fixed_prompt=fixed,
        genre_prompt=genre,
        dynamic_prompt=dynamic,
        scene_types=selected_scene_types,
        updater_documents=thread_documents,
    )
    validate_wiki_prompt_bundle(bundle)
    return bundle


def apply_pending_wiki_commit(
    vault_root: Path,
    thread_id: str,
) -> PendingWikiCommit | None:
    """다음 사용자 입력 직전에 thread의 commit.md를 적용합니다."""
    store = WikiStore(vault_root / "threads" / thread_id)
    return WikiCommitQueue(store).apply_pending()
