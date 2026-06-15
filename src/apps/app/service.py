# ================================
# src/apps/app/service.py
#
# Standalone web UI conversation orchestration and generation service.
#
# Functions
#   - preview_text(value: str) -> str : Build a compact sidebar preview from assistant output.
#   - create_conversation(world_id: str, scenario_id: str | None, store: ConversationStore, actor_model: str | None = None) -> ConversationState : Create a persisted conversation.
#   - _should_parse_ooc(content: str) -> bool : Decide whether input contains actionable OOC spans.
#   - _append_ooc_display_block(content: str, ooc_result: dict | None) -> str : Append OOC display metadata to assistant content.
#   - _format_ooc_change_details(ooc_result: dict) -> str : Render OOC parser changes as compact character-scoped lines.
#   - _display_change_value(value: object) -> str : Return a readable OOC change value.
#   - refresh_graph_snapshot_best_effort(state: ConversationState) -> None : Refresh graph viewer cache for the current web conversation.
#   - append_user_and_stream(state: ConversationState, content: str, store: ConversationStore, client_message_id: str | None = None, actor_model: str | None = None) -> AsyncIterator[dict] : Commit previous pending, append user input, and stream Actor output.
#   - run_database_tool(state: ConversationState, tool_name: str, store: ConversationStore) -> dict : Run a read-only database tool and persist its message.
#   - reroll_assistant(state: ConversationState, assistant_id: str, store: ConversationStore, actor_model: str | None = None) -> dict : Regenerate an assistant message from its paired user input.
#   - edit_message(state: ConversationState, message_id: str, content: str, store: ConversationStore, actor_model: str | None = None) -> dict : Edit a message and update state.
#   - delete_message(state: ConversationState, message_id: str, store: ConversationStore) -> dict : Delete a message and update state.
# ================================

from __future__ import annotations

import random
import re
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.agents.manager import run_manager
from src.agents.prompt_factory.ooc_handler import is_ooc, parse_ooc
from src.agents.prompt_factory.usernote import build_usernotes_block
from src.config import MAX_TOKEN, MODEL_OUTPUT_REPAIR
from src.core.llm.client import get_client
from src.apps.app.input_routing import TurnInputType, route_user_input
from src.apps.app.output_guard import find_forbidden_terms, find_pov_violations
from src.apps.app.output_repair import repair_actor_output
from src.apps.app.pending_store import discard_pending_commit, save_pending_commit
from src.apps.app.session_models import PendingCommit
from src.apps.app.social_media_settings import resolve_social_media_features
from src.apps.graph_viewer.debug import build_debug_graph
from src.apps.app.turn_debug import write_turn_debug_snapshot
from src.apps.app.actor import stream_actor_events
from src.apps.app.analysis_tools import render_database_tool
from src.apps.app.commit import commit_pending_web
from src.apps.app.models import ChatMessage, ConversationState, MessageVariant, normalize_actor_model
from src.apps.app.runtime import (
    ActiveConversation,
    initialize_conversation,
    restore_game_time,
    snapshot_game_time,
    sync_conversation_perspective,
)
from src.apps.app.storage import ConversationStore
from src.apps.graph_viewer.server import ensure_graph_server, update_graph_snapshot

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
) -> ConversationState:
    """Create and persist a standalone web conversation."""
    state = initialize_conversation(ConversationState(world_id=world_id, scenario_id=scenario_id or "default"))
    state.actor_model = normalize_actor_model(actor_model or state.actor_model)
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
    """
    target = visible_text or full_response
    blocked_terms = find_forbidden_terms(target) + find_pov_violations(target, state.perspective)
    if not blocked_terms:
        return full_response
    repaired_visible = await repair_actor_output(target, blocked_terms, MODEL_OUTPUT_REPAIR)
    remaining_terms = find_forbidden_terms(repaired_visible) + find_pov_violations(repaired_visible, state.perspective)
    if remaining_terms:
        repaired_visible = await repair_actor_output(repaired_visible, remaining_terms, MODEL_OUTPUT_REPAIR)
        remaining_terms = find_forbidden_terms(repaired_visible) + find_pov_violations(repaired_visible, state.perspective)
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
    final_event: dict | None = None
    async for event in stream_actor_events(
        fixed_prompt=fixed,
        genre_prompt=genre,
        dynamic_prompt=dynamic,
        history=state.history,
        genai_client=_GENAI_CLIENT,
        model_name=selected_actor_model,
        max_token=MAX_TOKEN,
    ):
        if event["type"] == "complete":
            final_event = event
            break
        yield event

    if final_event is None:
        raise RuntimeError("Actor stream ended without a complete event.")

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
        "oocConfig": message.ooc_config,
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
            # 직전 커밋에서 임신/유기 시스템이 생성한 OOC가 있으면 이번 턴 입력 앞에 주입한다.
            # parse_ooc는 호출 즉시 DB(state/위치/시간)에 변경을 반영하므로, 파싱이 성공하면 OOC는
            # 이미 '소비'된 것 → 그 직후 pending_ooc를 비워 다음 턴 재적용(이중 반영)을 막는다.
            # 파싱 전에 실패하면 pending_ooc가 남아 다음 턴 재시도되므로 유실도 없다.
            # 표시되는 user_msg는 사용자가 친 원본을 유지하고, 파싱·생성에는 주입된 입력을 쓴다.
            consumed_ooc = state.pending_ooc
            effective_input = f"{consumed_ooc}\n{content}" if consumed_ooc else content
            ooc_result = await _parse_ooc_if_needed(effective_input, state)
            if consumed_ooc:
                state.pending_ooc = ""

            # 유저노트(활성화된 것만) → effective_input 맨 앞에 prepend (Player Input 바로 위).
            note_block = build_usernotes_block(state.usernotes)
            if note_block:
                effective_input = f"{note_block}\n\n{effective_input}"

            # OOC 설정이 있으면 Player Input 뒤에 append.
            if state.ooc_config:
                effective_input = f"{effective_input}\n\n{state.ooc_config}"

            user_msg = ChatMessage(
                id=client_message_id or f"user_{uuid4().hex}",
                role="user",
                content=content,
                ooc_config=state.ooc_config,
            )
            state.messages.append(user_msg)
            yield {"type": "user", "message": _message_payload(user_msg)}
            async for event in _run_generation_events(
                state,
                effective_input,
                user_msg.id,
                actor_model=actor_model,
                ooc_result=ooc_result,
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


async def _collect_generation(
    state: ConversationState,
    content: str,
    user_msg_id: str | None,
    store: ConversationStore,
    *,
    actor_model: str | None = None,
    ooc_result: dict | None = None,
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
    ):
        if event["type"] == "complete":
            final = event
    if persist:
        store.save(state)
    if final is None:
        raise RuntimeError("Generation completed without final response.")
    return final


async def reroll_assistant(
    state: ConversationState,
    assistant_id: str,
    store: ConversationStore,
    actor_model: str | None = None,
) -> dict:
    """Regenerate an assistant message from its paired user message."""
    if not state.pc_id or not state.npc_id:
        initialize_conversation(state)
    async with ActiveConversation(state):
        assistant_index = next((i for i, msg in enumerate(state.messages) if msg.id == assistant_id), None)
        if assistant_index is None:
            latest_user = next((msg for msg in reversed(state.messages) if msg.role == "user"), None)
            if latest_user and (not state.messages or state.messages[-1].id == latest_user.id):
                selected_actor_model = normalize_actor_model(actor_model or state.actor_model)
                return await _collect_generation(
                    state,
                    latest_user.content,
                    latest_user.id,
                    store,
                    actor_model=selected_actor_model,
                )
            raise KeyError("assistant message not found")
        assistant = state.messages[assistant_index]
        parent = next((msg for msg in state.messages if msg.id == assistant.parent_user_id), None)
        if parent is None:
            raise KeyError("paired user message not found")
        selected_actor_model = normalize_actor_model(actor_model or state.actor_model)
        original_messages = [msg.model_copy(deep=True) for msg in state.messages]
        original_history = deepcopy(state.history)
        original_recent = list(state.recent_responses)
        original_preview = state.preview
        original_pending = deepcopy(state.pending_commit)
        # 보류 커밋은 항상 '최신(미커밋) 응답'의 것이다. reroll은 그 응답만 대상으로 한다.
        # 다른(과거) 메시지를 reroll하려 하면 거부한다 — 허용하면 최신 응답의 보류 커밋이 재생성으로
        # 덮어써져 디스크에 stale pending 파일로 남는다(edit/delete의 response_msg_id 매칭과 같은 취지).
        if state.pending_commit and state.pending_commit.get("response_msg_id") != assistant.id:
            raise ValueError("can only reroll the response tied to the current pending commit")
        if state.pending_commit:
            # 위에서 불일치는 거부했으므로 이 보류 커밋은 reroll 대상(assistant)의 것임이 보장된다.
            # 폐기하고 재생성을 위해 시간/history/recent를 직전 응답 이전으로 롤백한다. 여기서는 저장하지
            # 않는다 — 최종 상태는 성공 시(dedup 후) 또는 실패 시(except 롤백)에만 영속화한다.
            pending = state.pending_commit
            await restore_game_time(pending.get("prev_game_time"))
            state.history = list(pending.get("history_snapshot") or [])
            state.recent_responses = list(pending.get("recent_snapshot") or [])
            state.prev_cot = str(pending.get("prev_cot") or "")
            discard_pending_commit(pending, state.world_id, state.pc_id, state.npc_id)
            state.pending_commit = None
        assistant.variants.insert(
            0,
            MessageVariant(
                content=assistant.content,
                created_at=assistant.created_at,
                actor_model=assistant.actor_model,
                edited=assistant.edited,
            ),
        )
        try:
            # persist=False: 새 응답 메시지를 원본에 합치고 중복을 제거한 뒤(아래) 최종 상태를 한 번에 저장한다.
            result = await _collect_generation(state, parent.content, parent.id, store, actor_model=selected_actor_model, persist=False)
            new_payload = result["message"]
            new_message = next((msg for msg in state.messages if msg.id == new_payload["id"]), None)
            if new_message is None:
                raise RuntimeError("Reroll completed without a persisted assistant message.")
            assistant.content = new_message.content
            assistant.created_at = new_message.created_at
            assistant.parent_user_id = new_message.parent_user_id
            assistant.edited = new_message.edited
            assistant.actor_model = new_message.actor_model
            state.messages = [msg for msg in state.messages if msg.id != new_message.id]
            state.history = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "msg_id": msg.id,
                }
                for msg in state.messages
                if msg.role in {"user", "assistant"}
            ][-MAX_HISTORY_TURNS * 2:]
            if state.pending_commit and state.pending_commit.get("response_msg_id") == new_message.id:
                state.pending_commit["response_msg_id"] = assistant.id
                state.pending_commit["ai_response"] = assistant.content
                save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            store.save(state)
            return {"message": _message_payload(assistant), "pending_commit_id": result.get("pending_commit_id"), "preview": state.preview}
        except Exception:
            # 재생성 실패 시에는 사용자가 보던 기존 응답과 그 pending commit을 되살린다.
            # 그래야 일시적인 Actor/API 오류가 원본 메시지를 삭제해 이후 reroll이 404가 되는 상태를 만들지 않는다.
            if state.pending_commit and original_pending:
                discard_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            state.messages = original_messages
            state.history = original_history
            state.recent_responses = original_recent
            state.preview = original_preview
            state.pending_commit = original_pending
            if state.pending_commit:
                save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            store.save(state)
            raise


async def edit_message(
    state: ConversationState,
    message_id: str,
    content: str,
    store: ConversationStore,
    actor_model: str | None = None,
) -> dict:
    """Edit a message and update conversation state."""
    if not state.pc_id or not state.npc_id:
        initialize_conversation(state)
    async with ActiveConversation(state):
        index = next((i for i, msg in enumerate(state.messages) if msg.id == message_id), None)
        if index is None:
            raise KeyError("message not found")
        message = state.messages[index]
        if message.role == "assistant":
            message.content = content
            message.edited = True
            if state.pending_commit and state.pending_commit.get("response_msg_id") == message.id:
                state.pending_commit["ai_response"] = content
                save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            state.preview = preview_text(content)
            store.save(state)
            return {"message": _message_payload(message), "preview": state.preview}

        selected_actor_model = normalize_actor_model(actor_model or state.actor_model)
        original_messages = [msg.model_copy(deep=True) for msg in state.messages]
        original_history = deepcopy(state.history)
        original_recent = list(state.recent_responses)
        original_preview = state.preview
        original_pending = deepcopy(state.pending_commit)
        message.content = content
        message.edited = True
        removed_ids = {msg.id for msg in state.messages[index + 1:]}
        if state.pending_commit and state.pending_commit.get("response_msg_id") in removed_ids:
            discard_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            state.pending_commit = None
        state.messages = state.messages[: index + 1]
        state.history = [
            {"role": msg.role, "content": msg.content, "msg_id": msg.id}
            for msg in state.messages
            if msg.role in {"user", "assistant"}
        ]
        state.recent_responses = [msg.content[:1500] for msg in state.messages if msg.role == "assistant"][-RECENT_STORY_TURNS:]
        # pending 폐기와 메시지 절단을 생성 전에 영속화: 재생성이 실패해도 폐기된 pending이 되살아나지 않게 한다.
        store.save(state)
        try:
            return await _collect_generation(state, content, message.id, store, actor_model=selected_actor_model)
        except Exception:
            # 편집 후 재생성 실패는 기존 응답/pending을 잃으면 복구가 어렵다.
            # 일시적 API 오류는 원래 대화 상태로 되돌리고 사용자가 다시 시도하게 한다.
            if state.pending_commit and original_pending:
                discard_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            state.messages = original_messages
            state.history = original_history
            state.recent_responses = original_recent
            state.preview = original_preview
            state.pending_commit = original_pending
            if state.pending_commit:
                save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            store.save(state)
            raise


def activate_variant(
    state: ConversationState,
    message_id: str,
    version_index: int,
    store: ConversationStore,
) -> dict:
    """Activate a specific version of an assistant message by index (oldest-first)."""
    msg = next((m for m in state.messages if m.id == message_id), None)
    if msg is None:
        raise KeyError("message not found")
    if msg.role != "assistant":
        raise ValueError("can only activate variants of assistant messages")

    # all versions oldest-first: reversed(variants) + [current]
    variants_oldest_first = list(reversed(msg.variants))
    total = len(variants_oldest_first) + 1  # +1 for current

    if version_index < 0 or version_index >= total:
        raise ValueError(f"version_index {version_index} out of range [0, {total - 1}]")

    if version_index == total - 1:
        store.save(state)
        return {"message": _message_payload(msg)}

    selected = variants_oldest_first[version_index]
    old_current = MessageVariant(
        content=msg.content,
        created_at=msg.created_at,
        actor_model=msg.actor_model,
        edited=msg.edited,
    )
    remaining = [v for v in msg.variants if v is not selected]
    msg.variants = [old_current] + remaining
    msg.content = selected.content
    msg.actor_model = selected.actor_model
    msg.edited = selected.edited

    state.history = [
        {"role": m.role, "content": m.content, "msg_id": m.id}
        for m in state.messages
        if m.role in {"user", "assistant"}
    ][-MAX_HISTORY_TURNS * 2:]
    state.recent_responses = [
        m.content[:1500] for m in state.messages if m.role == "assistant"
    ][-RECENT_STORY_TURNS:]
    state.preview = preview_text(msg.content)
    # variant 활성화로 현재 표시 응답이 바뀌면, 아직 미커밋 상태인 pending_commit의
    # 응답 내용도 함께 맞춰 다음 턴 Updater가 표시된 내용 기준으로 그래프를 갱신하게 한다.
    if state.pending_commit and state.pending_commit.get("response_msg_id") == msg.id:
        state.pending_commit["ai_response"] = msg.content
        save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
    store.save(state)
    return {"message": _message_payload(msg)}


def delete_message(state: ConversationState, message_id: str, store: ConversationStore) -> dict:
    """Delete a message and update conversation state."""
    message = next((msg for msg in state.messages if msg.id == message_id), None)
    if message is None:
        raise KeyError("message not found")
    removed_ids = {message.id}
    if message.role == "user":
        removed_ids.update(msg.id for msg in state.messages if msg.parent_user_id == message.id)
    if state.pending_commit and state.pending_commit.get("response_msg_id") in removed_ids:
        discard_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
        state.pending_commit = None
    state.messages = [msg for msg in state.messages if msg.id not in removed_ids]
    state.history = [
        {"role": msg.role, "content": msg.content, "msg_id": msg.id}
        for msg in state.messages
        if msg.role in {"user", "assistant"}
    ][-MAX_HISTORY_TURNS * 2:]
    state.recent_responses = [msg.content[:1500] for msg in state.messages if msg.role == "assistant"][-RECENT_STORY_TURNS:]
    latest = next((msg for msg in reversed(state.messages) if msg.role == "assistant"), None)
    state.preview = preview_text(latest.content) if latest else "새 대화"
    store.save(state)
    return {"messages": [_message_payload(msg) for msg in state.messages], "preview": state.preview}
