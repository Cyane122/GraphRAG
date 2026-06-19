# ================================
# src/apps/app/service.py
#
# Standalone web UI conversation orchestration and generation service.
# Message mutation operations (reroll/edit/activate/delete) live in message_ops.py.
#
# Functions
#   - preview_text(value: str) -> str : Build a compact sidebar preview from assistant output.
#   - create_conversation(world_id: str, scenario_id: str | None, store: ConversationStore, actor_model: str | None = None, ooc_config: str = "") -> ConversationState : Create a persisted conversation.
#   - _should_parse_ooc(content: str) -> bool : Decide whether input contains actionable OOC spans.
#   - _prepare_generation_input(state: ConversationState, content: str, include_pending_ooc: bool = True) -> tuple[str, dict | None] : Split OOC parsing from Actor scene input.
#   - _append_ooc_display_block(content: str, ooc_result: dict | None) -> str : Append OOC display metadata to assistant content.
#   - _format_ooc_change_details(ooc_result: dict) -> str : Render OOC parser changes as compact character-scoped lines.
#   - _display_change_value(value: object) -> str : Return a readable OOC change value.
#   - refresh_graph_snapshot_best_effort(state: ConversationState) -> None : Refresh graph viewer cache for the current web conversation.
#   - append_user_and_stream(state: ConversationState, content: str, store: ConversationStore, client_message_id: str | None = None, actor_model: str | None = None) -> AsyncIterator[dict] : Commit previous pending, append user input, and stream Actor output.
#   - run_database_tool(state: ConversationState, tool_name: str, store: ConversationStore) -> dict : Run a read-only database tool and persist its message.
#   - _persist_pregnancy_result(state: ConversationState, store: ConversationStore, ooc: str) -> dict : Persist a pregnancy OOC message and queue it for the actor.
#   - force_pregnancy(state: ConversationState, mother_id: str, father_id: str | None, store: ConversationStore) -> dict : Force a pregnancy and persist the result.
#   - simulate_pregnancy(state: ConversationState, mother_id: str, father_id: str | None, shots: int, store: ConversationStore) -> dict : Simulate N internal ejaculations and persist the result.
#   - _collect_generation(state, content, user_msg_id, store, *, actor_model, ooc_result, turn_ooc_directives, persist) -> dict : Run generation to completion and return the final event.
#   - _message_payload(message: ChatMessage) -> dict : Convert a persisted message into frontend JSON.
# ================================

from __future__ import annotations

import logging
import random
import re
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.agents.manager import run_manager
from src.agents.prompt_factory.ooc_handler import is_ooc, parse_ooc
from src.agents.prompt_factory.usernote import build_usernotes_block
from src.simulation.systems.world_dynamics.organic import (
    set_pregnant_manual,
    simulate_internal_ejaculation,
)
from src.config import MAX_TOKEN, MODEL_OUTPUT_REPAIR
from src.core.llm.client import get_client
from src.apps.app.input_routing import TurnInputType, route_user_input
from src.apps.app.output_guard import find_forbidden_terms, find_pov_violations
from src.apps.app.output_repair import repair_actor_output
from src.apps.app.pending_store import save_pending_commit
from src.apps.app.settings import load_settings
from src.apps.app.session_models import PendingCommit
from src.apps.app.social_media_settings import resolve_social_media_features
from src.apps.graph_viewer.debug import build_debug_graph
from src.apps.app.turn_debug import write_actor_raw_snapshot, write_turn_debug_snapshot
from src.apps.app.actor import stream_actor_events
from src.apps.app.analysis_tools import render_database_tool
from src.apps.app.commit import commit_pending_web
from src.apps.app.models import ChatMessage, ConversationState, normalize_actor_model
from src.apps.app.runtime import (
    ActiveConversation,
    initialize_conversation,
    snapshot_game_time,
    sync_conversation_perspective,
)
from src.apps.app.storage import ConversationStore
from src.apps.graph_viewer.server import ensure_graph_server, update_graph_snapshot

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 10
RECENT_STORY_TURNS = 3
_LOGS_DIR = Path("logs")
_TURN_DEBUG_DIR = _LOGS_DIR / "turn_debug"
_GENAI_CLIENT = get_client()
_STATUS_TEXTS = [
    "데이터를 수집하고 장면을 정리하는 중입니다.",
    "흘러간 시간과 머물다 간 감정들을 기록하고 있습니다.",
    "캐릭터와 세계 상태를 확인하는 중입니다.",
]


def preview_text(value: str) -> str:
    """Build a compact preview from assistant output."""
    text = re.sub(r"<analyze>[\s\S]*?</analyze>", "", value or "", flags=re.IGNORECASE)
    text = re.sub(r"<ooc>[\s\S]*?</ooc>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\*\*[^*]+\*\*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:25] + "..." if len(text) > 26 else text or "새 대화"


def _social_media_features(state: ConversationState) -> dict:
    """Resolve social-media feature flags for the conversation."""
    return resolve_social_media_features(state.world_config, {})


def create_conversation(
    world_id: str,
    scenario_id: str | None,
    store: ConversationStore,
    actor_model: str | None = None,
    ooc_config: str = "",
) -> ConversationState:
    """Create and persist a standalone web conversation."""
    state = initialize_conversation(ConversationState(world_id=world_id, scenario_id=scenario_id or "default"))
    state.actor_model = normalize_actor_model(actor_model or state.actor_model)
    state.ooc_config = str(ooc_config or "")
    opening_scene = (
        str(state.world_config.get("opening_scene") or "")
        or str(state.world_config.get("prompt", {}).get("sections", {}).get("opening_scene") or "")
    ).strip()
    if opening_scene:
        state.messages.append(
            ChatMessage(
                id=f"opening_{uuid4().hex}",
                role="assistant",
                content=opening_scene,
            )
        )
    return store.save(state)


async def _commit_previous_pending(state: ConversationState) -> None:
    """Commit the previous accepted pending response if one exists."""
    if not state.pending_commit:
        return
    await commit_pending_web(state.pending_commit, state)
    state.pending_commit = None


async def _parse_ooc_if_needed(content: str, state: ConversationState) -> dict | None:
    """Apply OOC spans before generation and return the OOC result."""
    if not _should_parse_ooc(content):
        return None
    return await parse_ooc(
        content,
        npc_id=state.npc_id,
        npc_name=state.npc_name_kor,
        pc_id=state.pc_id,
        world_config=state.world_config,
    )


async def _prepare_generation_input(
    state: ConversationState,
    content: str,
    include_pending_ooc: bool = True,
) -> tuple[str, dict | None]:
    """Parse OOC mutations separately from the Actor scene input."""
    consumed_ooc = state.pending_ooc if include_pending_ooc else ""
    ooc_parse_input = f"{consumed_ooc}\n{content}" if consumed_ooc else content
    ooc_result = await _parse_ooc_if_needed(ooc_parse_input, state)
    if consumed_ooc:
        state.pending_ooc = ""

    scene_input = content
    note_block = build_usernotes_block(state.usernotes)
    if note_block:
        scene_input = f"{note_block}\n\n{scene_input}"
    return scene_input, ooc_result


def _should_parse_ooc(content: str) -> bool:
    """Return whether input should be sent through the OOC parser."""
    input_type = route_user_input(content, object())
    if input_type in {
        TurnInputType.EMPTY,
        TurnInputType.EDIT,
        TurnInputType.REROLL,
        TurnInputType.SYSTEM_COMMAND,
    }:
        return False
    return is_ooc(content)


def _apply_ooc_effects(manager_effects: dict, ooc_result: dict | None, pc_id: str) -> dict:
    """Merge OOC result into manager effects using the legacy Chainlit semantics."""
    if not ooc_result:
        return manager_effects
    manager_effects["ooc_patch_result"] = ooc_result
    if ooc_result.get("time_changed"):
        manager_effects["ooc_time_patch"] = ooc_result
        manager_effects["time_plan"] = None
        manager_effects["ooc_time_after"] = ooc_result.get("time_after")
        manager_effects["needs_update"] = {
            "pc_id": pc_id,
            "elapsed_minutes": float(ooc_result.get("elapsed_minutes") or 0.0),
            "current_time": ooc_result.get("time_after"),
        }
        manager_effects["daily_systems"] = {
            "days_passed": int(ooc_result.get("days_passed") or 0),
            "current_time": ooc_result.get("time_after"),
        }
        manager_effects["pending_effects"] = [
            effect for effect in manager_effects.get("pending_effects", [])
            if effect.get("type") not in {"global_time_update", "global_weather_update", "location_update"}
        ]
    return manager_effects


def _append_ooc_display_block(content: str, ooc_result: dict | None) -> str:
    """Append an OOC display block to assistant content without changing Actor text."""
    if not ooc_result or re.search(r"<ooc>[\s\S]*?</ooc>", content or "", flags=re.IGNORECASE):
        return content

    summary = str(ooc_result.get("summary") or "OOC 변경 사항이 반영되었습니다.").strip()
    details = _format_ooc_change_details(ooc_result)
    separator = "\n---\n" if details else "\n"
    return f"{content.rstrip()}\n\n<ooc>\n{summary}{separator}{details}\n</ooc>"


def _format_ooc_change_details(ooc_result: dict) -> str:
    """Render OOC parser changes as compact character-scoped lines."""
    grouped: dict[str, list[str]] = {}

    for char_id, change in (ooc_result.get("location_changes") or {}).items():
        before = _display_change_value(change.get("before"))
        after = _display_change_value(change.get("after"))
        grouped.setdefault(str(char_id), []).append(f"- location: {before} -> {after}")

    for char_id, fields in (ooc_result.get("state_change_diffs") or {}).items():
        for field, change in (fields or {}).items():
            before = _display_change_value(change.get("before"))
            after = _display_change_value(change.get("after"))
            grouped.setdefault(str(char_id), []).append(f"- {field}: {before} -> {after}")

    if ooc_result.get("time_changed"):
        grouped.setdefault("global", []).append(
            f"- time: {_display_change_value(ooc_result.get('time_before'))} -> "
            f"{_display_change_value(ooc_result.get('time_after'))}"
        )

    if not grouped:
        for char_name, fields in (ooc_result.get("state_changes") or {}).items():
            for field, value in (fields or {}).items():
                grouped.setdefault(str(char_name), []).append(f"- {field}: ? -> {_display_change_value(value)}")
        location_id = ooc_result.get("location_id")
        for char_id in ooc_result.get("moved_character_ids") or []:
            grouped.setdefault(str(char_id), []).append(f"- location: ? -> {_display_change_value(location_id)}")

    blocks = []
    for subject, lines in grouped.items():
        blocks.append("\n".join([subject, *lines]))
    return "\n\n".join(blocks)


def _display_change_value(value: object) -> str:
    """Return a readable OOC change value."""
    if value in (None, ""):
        return "?"
    return str(value)


async def refresh_graph_snapshot_best_effort(state: ConversationState) -> None:
    """Refresh graph viewer cache for the current web conversation."""
    try:
        graph = await build_debug_graph(
            pc_id=state.pc_id,
            npc_id=state.npc_id,
            world_id=state.world_id,
            thread_id=state.thread_id,
        )
        ensure_graph_server()
        update_graph_snapshot(graph)
    except Exception as exc:
        print(f"[app] graph snapshot refresh skipped: {exc}")


async def _repair_if_needed(full_response: str, visible_text: str, state: ConversationState) -> str:
    """Repair output guard violations in the visible prose before persistence.

    Guard는 사용자에게 보이는 prose(visible_text)만 검사한다. <analyze> 내부 추론 블록은
    1인칭이 자연스러워 POV guard를 오발동시키므로(불필요한 Pro repair 유발) 검사 대상에서 제외한다.
    위반이 있으면 보이는 prose만 수정한 뒤, analyze 블록을 보존하도록 full_response를 재구성한다.
    전역 설정에서 output repair가 꺼져 있으면 검사·수정 없이 원본을 그대로 반환한다.
    """
    if not load_settings().output_repair_enabled:
        return full_response
    target = visible_text or full_response
    blocked_terms = find_forbidden_terms(target) + find_pov_violations(
        target, state.perspective, state.npc_name_kor
    )
    if not blocked_terms:
        return full_response
    repaired_visible = await repair_actor_output(target, blocked_terms, MODEL_OUTPUT_REPAIR)
    remaining_terms = find_forbidden_terms(repaired_visible) + find_pov_violations(
        repaired_visible, state.perspective, state.npc_name_kor
    )
    if remaining_terms:
        repaired_visible = await repair_actor_output(repaired_visible, remaining_terms, MODEL_OUTPUT_REPAIR)
        remaining_terms = find_forbidden_terms(repaired_visible) + find_pov_violations(
            repaired_visible, state.perspective, state.npc_name_kor
        )
    if remaining_terms:
        sample = ", ".join(remaining_terms[:8])
        raise RuntimeError(f"Actor output failed the output guard after repair. Remaining: {sample}")
    if visible_text and visible_text in full_response:
        return full_response.replace(visible_text, repaired_visible, 1)
    return repaired_visible


async def _run_generation_events(
    state: ConversationState,
    user_input: str,
    user_msg_id: str | None,
    *,
    actor_model: str | None = None,
    ooc_result: dict | None = None,
    turn_ooc_directives: str = "",
) -> AsyncIterator[dict]:
    """Run Manager and Actor, yielding frontend stream events."""
    sync_conversation_perspective(state)
    selected_actor_model = normalize_actor_model(actor_model or state.actor_model)
    state.actor_model = selected_actor_model
    commit_id = uuid4().hex
    prev_game_time = await snapshot_game_time()
    recent_story = "\n".join(state.recent_responses[-RECENT_STORY_TURNS:])
    queued_kakao_messages = list(state.pending_kakao_messages or [])

    yield {"type": "status", "content": random.choice(_STATUS_TEXTS)}
    fixed, genre, dynamic, scene_types, manager_effects = await run_manager(
        user_input=user_input,
        pc_id=state.pc_id,
        npc_id=state.npc_id,
        recent_story=recent_story,
        world_id=state.world_id,
        scenario_id=state.scenario_id,
        perspective=state.perspective,
        return_meta=True,
        suppress_time_plan=bool(ooc_result and ooc_result.get("time_changed")),
        scene_need_hints=state.scene_need_hints,
        pending_kakao_messages=queued_kakao_messages,
        enable_kakao_preprocessing=_social_media_features(state).get("kakao_enabled", False),
        social_media_features=_social_media_features(state),
        thread_id=state.thread_id,
        commit_id=None if ooc_result else commit_id,
        turn_ooc_directives=turn_ooc_directives,
    )
    manager_effects = _apply_ooc_effects(manager_effects, ooc_result, state.pc_id)

    if state.npc_name_kor:
        dynamic = (
            dynamic
            + "\n\n[Character Naming Lock]\n"
            + f"- Current primary NPC: {state.npc_name_kor} ({state.npc_id}).\n"
            + f"- Use `{state.npc_name_kor}` or the established short name when referring to this character.\n"
            + "- Do not replace a named active character with generic labels such as `여학생`, `여자`, `학생`, or `그 여자` unless explicitly referring to an unnamed extra.\n"
        )
    if state.prev_cot:
        dynamic = dynamic + f"\n\n[Previous Turn CoT]\n{state.prev_cot}"

    debug_dir = write_turn_debug_snapshot(
        user_input=user_input,
        fixed_prompt=fixed,
        genre_prompt=genre,
        dynamic_prompt=dynamic,
        scene_types=scene_types,
        manager_effects=manager_effects,
        history=state.history,
        world_id=state.world_id,
        pc_id=state.pc_id,
        npc_id=state.npc_id,
        npc_name=state.npc_name_kor,
        logs_dir=_LOGS_DIR,
        turn_debug_dir=_TURN_DEBUG_DIR,
        actor_model=selected_actor_model,
    )

    history_snapshot = list(state.history)
    recent_snapshot = list(state.recent_responses)
    actor_kwargs = dict(
        fixed_prompt=fixed,
        genre_prompt=genre,
        dynamic_prompt=dynamic,
        history=state.history,
        genai_client=_GENAI_CLIENT,
        model_name=selected_actor_model,
        max_token=MAX_TOKEN,
    )
    final_event: dict | None = None
    async for event in stream_actor_events(**actor_kwargs):
        if event["type"] == "complete":
            final_event = event
            break
        yield event

    if final_event is None:
        raise RuntimeError("Actor stream ended without a complete event.")

    # 모델이 <analyze> 사고만 내고 가시 본문을 누락하면(가끔, 모델 변동성) 사용자에겐 빈 응답이나
    # 사고 덤프가 보인다. 수동 리롤이 통하는 것과 동일하게, 가시 prose가 비면 1회 자동 재생성한다.
    if not (final_event.get("visible_text") or "").strip():
        logger.warning("[WebGeneration] Actor produced empty visible prose; retrying once")
        retry_event: dict | None = None
        async for event in stream_actor_events(**actor_kwargs):
            if event["type"] == "complete":
                retry_event = event
                break
            yield event
        if retry_event is not None and (retry_event.get("visible_text") or "").strip():
            final_event = retry_event

    write_actor_raw_snapshot(
        full_response=str(final_event.get("content") or ""),
        raw_thinking=str(final_event.get("raw_thinking") or ""),
        visible_text=str(final_event.get("visible_text") or ""),
        logs_dir=_LOGS_DIR,
        debug_dir=debug_dir,
    )

    full_response = await _repair_if_needed(
        final_event["content"], final_event.get("visible_text") or "", state
    )
    display_response = _append_ooc_display_block(full_response, ooc_result)
    scene_chars = list(set(final_event.get("scene_chars") or []) | set(manager_effects.get("scene_npc_ids") or []))

    assistant_msg = ChatMessage(
        id=f"assistant_{uuid4().hex}",
        role="assistant",
        content=display_response,
        parent_user_id=user_msg_id,
        actor_model=selected_actor_model,
    )
    state.messages.append(assistant_msg)
    state.history += [
        {"role": "user", "content": user_input, "msg_id": user_msg_id},
        {"role": "assistant", "content": full_response, "msg_id": assistant_msg.id},
    ]
    del state.history[:-MAX_HISTORY_TURNS * 2]
    state.recent_responses.append(full_response[:1500])
    state.recent_responses = state.recent_responses[-RECENT_STORY_TURNS:]
    state.prev_cot = str(final_event.get("raw_thinking") or "")
    state.preview = preview_text(display_response)
    state.title = f"{state.world_id}/{state.scenario_id}"
    if manager_effects.get("kakao_processed"):
        state.pending_kakao_messages = []

    pending_commit = PendingCommit(
        commit_id=commit_id,
        thread_id=state.thread_id,
        user_input=user_input,
        ai_response=full_response,
        scene_types=scene_types,
        scene_chars=scene_chars,
        timestamp=datetime.now(),
        history_snapshot=history_snapshot,
        recent_snapshot=recent_snapshot,
        prev_game_time=prev_game_time,
        manager_effects=manager_effects,
        ooc_result=ooc_result,
        time_plan=manager_effects.get("time_plan"),
        pending_kakao_messages=queued_kakao_messages if manager_effects.get("kakao_processed") else [],
        pending_effects=manager_effects.get("pending_effects", []),
        debug_dir=debug_dir,
        user_msg_id=user_msg_id,
        response_msg_id=assistant_msg.id,
        prev_cot=state.prev_cot,
    )
    state.pending_commit = pending_commit.model_dump(mode="json")
    save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
    print(
        "[WebGeneration] pending saved "
        f"commit_id={commit_id} actor_model={selected_actor_model} "
        f"thread_id={state.thread_id}"
    )
    await refresh_graph_snapshot_best_effort(state)
    yield {
        "type": "complete",
        "message": _message_payload(assistant_msg),
        "pending_commit_id": commit_id,
        "preview": state.preview,
    }


def _message_payload(message: ChatMessage) -> dict:
    """Convert a persisted message into frontend JSON."""
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "createdAt": message.created_at.strftime("%H:%M"),
        "parentUserId": message.parent_user_id,
        "edited": message.edited,
        "actorModel": message.actor_model,
        "oocConfig": getattr(message, "ooc_config", ""),
        "variants": [
            {
                "id": variant.id,
                "content": variant.content,
                "createdAt": variant.created_at.strftime("%H:%M"),
                "actorModel": variant.actor_model,
                "edited": variant.edited,
            }
            for variant in message.variants
        ],
    }


async def append_user_and_stream(
    state: ConversationState,
    content: str,
    store: ConversationStore,
    client_message_id: str | None = None,
    actor_model: str | None = None,
) -> AsyncIterator[dict]:
    """Commit previous pending, append user input, and stream Actor output."""
    if not state.pc_id or not state.npc_id:
        initialize_conversation(state)
    async with ActiveConversation(state):
        # 직전 pending 커밋 → OOC 주입/파싱 → 생성 전체를 try로 감싼다. 어느 단계에서 실패하든
        # finally의 store.save가 in-memory 상태를 영속화한다(특히 커밋 후 pending_commit=None이
        # 저장되지 않아 다음 로드 때 이미 커밋된 pending이 재실행되는 것을 방지).
        try:
            await _commit_previous_pending(state)
            # OOC는 DB mutation으로 먼저 소비하고, Actor에는 사용자가 입력한 scene 텍스트만 보낸다.
            # pending_ooc는 parse 성공 뒤에만 비워 다음 턴 이중 반영을 막는다.
            effective_input, ooc_result = await _prepare_generation_input(state, content)

            user_msg = ChatMessage(
                id=client_message_id or f"user_{uuid4().hex}",
                role="user",
                content=content,
                ooc_config=state.ooc_config,
            )
            state.messages.append(user_msg)
            yield {"type": "user", "message": _message_payload(user_msg)}
            # *...* 만으로 된 입력도 OOC 효과(시간/상태 등)를 적용한 뒤 항상 Actor 응답을
            # 생성한다. OOC 전용 단락(응답 없이 요약만)은 출력이 안 나오는 것처럼 보여 제거했다.
            async for event in _run_generation_events(
                state,
                effective_input,
                user_msg.id,
                actor_model=actor_model,
                ooc_result=ooc_result,
                turn_ooc_directives=state.ooc_config,
            ):
                yield event
        finally:
            store.save(state)


async def run_database_tool(state: ConversationState, tool_name: str, store: ConversationStore) -> dict:
    """Run a read-only database tool and persist its assistant message."""
    content = await render_database_tool(tool_name, state)
    message = ChatMessage(
        id=f"assistant_{uuid4().hex}",
        role="assistant",
        content=content,
    )
    state.messages.append(message)
    state.preview = preview_text(content)
    state.title = f"{state.world_id}/{state.scenario_id or 'default'}"
    store.save(state)
    return {"message": _message_payload(message), "preview": state.preview}


def _persist_pregnancy_result(state: ConversationState, store: ConversationStore, ooc: str) -> dict:
    """Persist a pregnancy-tool OOC message as a visible assistant record and queue it for the actor.

    The message is shown in the chat immediately, and appended to state.pending_ooc so the
    next turn's Actor is aware of the system change (same channel as the auto pregnancy OOC).
    """
    message = ChatMessage(
        id=f"assistant_{uuid4().hex}",
        role="assistant",
        content=ooc,
    )
    state.messages.append(message)
    state.preview = preview_text(ooc)
    state.pending_ooc = f"{state.pending_ooc}\n{ooc}".strip() if state.pending_ooc else ooc
    store.save(state)
    return {"message": _message_payload(message), "preview": state.preview, "ooc": ooc}


async def force_pregnancy(
    state: ConversationState,
    mother_id: str,
    father_id: str | None,
    store: ConversationStore,
) -> dict:
    """Force the given character (mother) pregnant by the optional father and persist the result."""
    async with ActiveConversation(state):
        ooc = await set_pregnant_manual(mother_id, father_id or None)
    if ooc is None:
        raise KeyError("character not found")
    return _persist_pregnancy_result(state, store, ooc)


async def simulate_pregnancy(
    state: ConversationState,
    mother_id: str,
    father_id: str | None,
    shots: int,
    store: ConversationStore,
) -> dict:
    """Simulate N internal ejaculations on the mother, apply conception if rolled, and persist."""
    async with ActiveConversation(state):
        ooc = await simulate_internal_ejaculation(mother_id, father_id or None, shots)
    if ooc is None:
        raise KeyError("character not found")
    return _persist_pregnancy_result(state, store, ooc)


async def _collect_generation(
    state: ConversationState,
    content: str,
    user_msg_id: str | None,
    store: ConversationStore,
    *,
    actor_model: str | None = None,
    ooc_result: dict | None = None,
    turn_ooc_directives: str = "",
    persist: bool = True,
) -> dict:
    """Run generation to completion and return the final event.

    persist=False면 중간 store.save를 건너뛴다 — reroll처럼 호출 후 메시지 dedup을 거쳐 최종 상태를
    한 번에 저장해야 하는 경우, 중간 저장이 중복 메시지를 디스크에 남기는 창을 없앤다.
    """
    final: dict | None = None
    async for event in _run_generation_events(
        state,
        content,
        user_msg_id,
        actor_model=actor_model,
        ooc_result=ooc_result,
        turn_ooc_directives=turn_ooc_directives,
    ):
        if event["type"] == "complete":
            final = event
    if persist:
        store.save(state)
    if final is None:
        raise RuntimeError("Generation completed without final response.")
    return final
