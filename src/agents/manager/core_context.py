# ================================
# src/agents/manager/core_context.py
#
# Manager core graph/context collection helpers.
#
# Functions
#   - assemble_core_context(user_input: str, recent_story: str, pc_id: str, npc_id: str, bootstrap: ManagerBootstrap, scene_plan: SceneTimePlan, world_id: str | None, deps: ManagerDependencies, current_turn_personal_facts: list[dict] | None = None) -> CoreContext : Collect graph context for prompt rendering
# ================================

import asyncio
from datetime import datetime

from src.agents.context.planner import build_context_plan, context_plan_to_prompt_dict
from src.agents.context.transient import sanitize_location_hints_for_turn
from src.agents.manager.models import CoreContext, ManagerBootstrap, ManagerDependencies, SceneTimePlan
from src.agents.context.scene_state import get_scene_state, scene_state_to_prompt_dict
from src.assets.worlds.base import World
from src.core.database import async_driver
from src.core.embedding.encoder import embed_async
from src.simulation.systems.personal_facts import fetch_active_personal_facts, merge_prompt_facts

_LOCATION_ID_ALIASES: dict[str, tuple[str, ...]] = {
    "sunghwa_high_school": ("sunghwa_school",),
    "sunghwa_high_school_classroom_1_7": ("sunghwa_classroom_1_7",),
    "sunghwa_high_school_hallway": ("sunghwa_hallway",),
    "sunghwa_high_school_cafeteria": ("sunghwa_cafeteria",),
    "sunghwa_high_school_gym": ("sunghwa_gym",),
    "sunghwa_high_school_rooftop": ("sunghwa_rooftop",),
    "sunghwa_high_school_library": ("sunghwa_library",),
    "sunghwa_high_school_shoe_locker": ("sunghwa_shoe_locker",),
}


async def assemble_core_context(
    user_input: str,
    recent_story: str,
    pc_id: str,
    npc_id: str,
    bootstrap: ManagerBootstrap,
    scene_plan: SceneTimePlan,
    world_id: str | None,
    deps: ManagerDependencies,
    current_turn_personal_facts: list[dict] | None = None,
) -> CoreContext:
    """Collect graph context needed before the final prompt rendering stage."""
    char_data, user_data, relationship, recent_events = await _fetch_core_records(
        pc_id,
        npc_id,
        scene_plan.scene_types,
        deps,
    )
    location_id, location_name, location_nodes = await _resolve_turn_location(
        scene_plan.time_plan,
        bootstrap.global_state,
        npc_id,
        deps,
    )
    location_nodes = sanitize_location_hints_for_turn(location_nodes, user_input, recent_story)

    if "dynamic_state" in char_data:
        char_data["dynamic_state"]["location_id"] = location_name

    npcs = await _fetch_present_npc_context(
        user_input,
        recent_story,
        location_id,
        bootstrap.world,
        npc_id,
        pc_id,
        deps,
        scene_plan.schedule_context,
    )
    scene_state_dict = _build_scene_state_dict(
        world_id,
        pc_id,
        npc_id,
        location_name,
        scene_plan.scene_types,
        recent_story,
        npcs,
    )
    context_plan_dict, requires_memory = _build_context_plan_dict(
        scene_plan.scene_types,
        user_input,
        scene_state_dict,
        bootstrap.world_config,
    )
    recall_events, memory_conflicts, raw_memories = await _fetch_memory_context_if_needed(
        requires_memory,
        user_input,
        npc_id,
        recent_events,
        scene_plan.scene_types,
        scene_plan.base_time,
        location_id=location_id,
    )
    stored_personal_facts = await fetch_active_personal_facts(
        None,
        npc_id,
        scene_plan.current_dt,
        user_input=user_input,
    )
    personal_facts = merge_prompt_facts(
        stored_personal_facts,
        current_turn_personal_facts or [],
        user_input=user_input,
    )

    return CoreContext(
        char_data=char_data,
        user_data=user_data,
        relationship=relationship,
        recent_events=recent_events,
        recall_events=recall_events,
        personal_facts=personal_facts,
        memory_conflicts=memory_conflicts,
        raw_memories=raw_memories,
        location_id=location_id,
        location_name=location_name,
        location_nodes=location_nodes,
        npcs=npcs,
        scene_state=scene_state_dict,
        context_plan=context_plan_dict,
    )


async def _fetch_core_records(
    pc_id: str,
    npc_id: str,
    scene_types: list[str],
    deps: ManagerDependencies,
) -> tuple[dict, dict, dict, list[dict]]:
    """Fetch character, relationship, and recent event records in parallel."""
    return await asyncio.gather(
        deps.fetch_character_data(npc_id, scene_types),
        deps.fetch_character_data(pc_id, scene_types),
        deps.fetch_relationship_data(npc_id, pc_id),
        deps.fetch_recent_events(npc_id, pc_id, 3),
    )


async def _resolve_turn_location(
    time_plan: dict,
    global_state: dict,
    npc_id: str,
    deps: ManagerDependencies,
) -> tuple[str | None, str, list[dict]]:
    """Resolve location id/name and fetch the full PART_OF hierarchy for prompt injection."""
    loc_id = time_plan.get("new_location_id") or global_state.get("currentLocationId")
    new_location = time_plan.get("new_location") if isinstance(time_plan.get("new_location"), dict) else {}
    location_name = (
        await deps.get_location_name_from_id(loc_id)
        or new_location.get("name")
        or await deps.fetch_location(npc_id)
    )
    location_nodes = await deps.fetch_location_hierarchy(loc_id) if loc_id else []
    if not location_nodes and new_location:
        location_nodes = [{
            "id": loc_id,
            "name": new_location.get("name") or loc_id,
            "description": new_location.get("description") or "",
            "prompt_hint": new_location.get("prompt_hint") or new_location.get("description") or "",
            "prompt_priority": new_location.get("prompt_priority") or 8,
        }]
    return loc_id, location_name, location_nodes


async def _fetch_present_npc_context(
    user_input: str,
    recent_story: str,
    location_id: str | None,
    world: World,
    npc_id: str,
    pc_id: str,
    deps: ManagerDependencies,
    schedule_context: dict | None = None,
) -> list[dict]:
    """Fetch secondary NPC profiles present by mention or current location."""
    mentioned_ids = deps.detect_present_npcs(user_input, recent_story, world.get_npc_name_map())
    located_ids = await deps.fetch_location_character_ids(location_id)
    scheduled_ids = _active_schedule_character_ids(schedule_context or {}, location_id)
    present_npc_ids = sorted({*mentioned_ids, *located_ids, *scheduled_ids} - {npc_id, pc_id})
    return await deps.fetch_npc_profiles(present_npc_ids, npc_id, pc_id) if present_npc_ids else []


def _active_schedule_character_ids(schedule_context: dict, location_id: str | None) -> list[str]:
    """Return owners of active schedules at the current scene location."""
    if not location_id:
        return []
    result: list[str] = []
    for schedule in schedule_context.get("schedules") or []:
        if schedule.get("timing") != "active":
            continue
        if not _same_schedule_location(schedule, location_id):
            continue
        owner_id = str(schedule.get("owner_id") or "").strip()
        if owner_id:
            result.append(owner_id)
    return result


def _same_schedule_location(schedule: dict, location_id: str) -> bool:
    """Compare schedule location id/name against the current scene location token."""
    schedule_id = str(schedule.get("location_id") or "")
    schedule_name = str(schedule.get("location_name") or "")
    return (
        _same_location_id(schedule_id, location_id)
        or bool(schedule_name and schedule_name == location_id)
    )


def _same_location_id(left: str, right: str) -> bool:
    """Compare location ids while accepting known legacy/current aliases."""
    if not left or not right:
        return False
    return bool(set(_location_seed_ids(left)) & set(_location_seed_ids(right)))


def _location_seed_ids(location_id: str) -> list[str]:
    """Return a location id plus known legacy/current aliases."""
    ids = {location_id}
    for canonical, aliases in _LOCATION_ID_ALIASES.items():
        if location_id == canonical or location_id in aliases:
            ids.add(canonical)
            ids.update(aliases)
    return sorted(ids)


async def _fetch_memory_context_if_needed(
    requires_memory: bool,
    user_input: str,
    npc_id: str,
    recent_events: list[dict],
    scene_types: list[str] | None = None,
    current_dt: datetime | None = None,
    location_id: str | None = None,
) -> tuple[list[dict], list[str], list[dict]]:
    """Fetch recalled memory context only when the context planner asks for memory."""
    if not requires_memory:
        return [], [], []
    return await _recall_relevant_memories(user_input, npc_id, recent_events, scene_types, current_dt, location_id)


_MEMORY_SUMMARY_EXPR = """\
CASE
    WHEN mem.narrative_summary IS NULL OR mem.narrative_summary = '' THEN mem.summary
    ELSE mem.narrative_summary
END"""


async def _recall_relevant_memories(
    user_input: str,
    npc_id: str,
    recent_events: list[dict],
    scene_types: list[str] | None = None,
    current_dt: datetime | None = None,
    location_id: str | None = None,
) -> tuple[list[dict], list[str], list[dict]]:
    """Fetch memories via three complementary strategies and merge by priority.

    Priority order:
      1. Pinned (importance >= 8) — always included regardless of similarity
      2. Recency guarantee — most recent memory if not already covered
      3. Vector search — composite-scored semantic matches fill remaining slots
    """
    recall_events: list[dict] = []
    memory_conflicts: list[str] = []
    raw_memories: list[dict] = []
    limit = _memory_limit(scene_types)
    try:
        # Expand the embedding query with scene context so short inputs ("응", "계속해")
        # retrieve more relevant memories via semantic similarity.
        query_parts = [user_input]
        if scene_types:
            query_parts.extend(scene_types)
        if location_id:
            query_parts.append(location_id)
        for ev in (recent_events or [])[-2:]:
            ev_summary = (ev.get("narrative_summary") or ev.get("summary") or "")[:100]
            if ev_summary:
                query_parts.append(ev_summary)
        memory_query = " ".join(filter(None, query_parts))
        query_embedding = await embed_async(memory_query)

        # ── 1. Vector search ──────────────────────────────────
        async with async_driver.session() as session:
            rec = await session.run(f"""
                CALL QUERY_VECTOR_INDEX('Memory', 'memory_embeddings', $embedding, $candidates)
                WITH node AS mem, distance
                MATCH (c:Character {{id: $char_id}})-[:REMEMBERS]->(mem)
                WHERE distance <= $max_distance AND mem.summary_level < 3
                RETURN mem.event_id         AS id,
                       {_MEMORY_SUMMARY_EXPR} AS summary,
                       mem.memory_type      AS memory_type,
                       mem.distortion_level AS distortion,
                       mem.importance       AS importance,
                       mem.created_at       AS created_at,
                       distance
                ORDER BY distance ASC
                LIMIT $limit
            """,
                char_id=npc_id,
                embedding=query_embedding,
                candidates=20,
                max_distance=0.45,
                limit=15,
            )
            raw_memories = await rec.data()

        # ── 2. Pinned (importance >= 8) ───────────────────────
        async with async_driver.session() as session:
            pin_rec = await session.run(f"""
                MATCH (c:Character {{id: $char_id}})-[:REMEMBERS]->(mem:Memory)
                WHERE mem.importance >= 8 AND mem.summary_level < 3
                RETURN mem.event_id         AS id,
                       {_MEMORY_SUMMARY_EXPR} AS summary,
                       mem.memory_type      AS memory_type,
                       mem.distortion_level AS distortion,
                       mem.importance       AS importance
                ORDER BY mem.importance DESC
                LIMIT 5
            """, char_id=npc_id)
            pinned_rows = await pin_rec.data()

        # ── 3. Recency guarantee ──────────────────────────────
        async with async_driver.session() as session:
            rec_rec = await session.run(f"""
                MATCH (c:Character {{id: $char_id}})-[:REMEMBERS]->(mem:Memory)
                WHERE mem.summary_level < 3
                RETURN mem.event_id         AS id,
                       {_MEMORY_SUMMARY_EXPR} AS summary,
                       mem.memory_type      AS memory_type,
                       mem.distortion_level AS distortion,
                       mem.importance       AS importance
                ORDER BY mem.created_at DESC
                LIMIT 2
            """, char_id=npc_id)
            recent_rows = await rec_rec.data()

        recent_event_ids = {event["id"] for event in recent_events}

        # Score vector results
        scored: list[tuple[float, dict]] = []
        for memory in raw_memories:
            if memory["id"] in recent_event_ids:
                continue
            sim = max(0.0, 1.0 - float(memory.get("distance") or 0.0))
            recency = _recency_score(memory.get("created_at"), current_dt)
            imp = _importance_score(memory.get("importance"))
            final_score = round(sim * 0.6 + recency * 0.2 + imp * 0.2, 3)
            scored.append((final_score, memory))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Merge: pinned (guaranteed) → recency (1 guaranteed) → vector (fills budget)
        seen_ids: set[str] = set()
        budget = limit  # non-pinned slots

        def _add_entry(mem: dict, score: float) -> None:
            is_conflict = float(mem.get("distortion") or 0.0) > 0.4
            recall_events.append({
                "id": mem["id"],
                "summary": mem["summary"],
                "memory_type": mem.get("memory_type"),
                "score": score,
                "conflict": is_conflict,
            })
            if is_conflict:
                memory_conflicts.append(mem["summary"])

        for mem in pinned_rows:
            mid = mem.get("id")
            if mid and mid not in seen_ids and mid not in recent_event_ids:
                _add_entry(mem, 1.0)
                seen_ids.add(mid)

        for mem in recent_rows[:1]:
            mid = mem.get("id")
            if mid and mid not in seen_ids and mid not in recent_event_ids:
                _add_entry(mem, 0.75)
                seen_ids.add(mid)
                budget -= 1

        for final_score, mem in scored:
            if budget <= 0:
                break
            mid = mem.get("id")
            if mid and mid not in seen_ids:
                _add_entry(mem, final_score)
                seen_ids.add(mid)
                budget -= 1

    except Exception as exc:
        print(f"[Manager] recall_events 조회 실패 (무시): {exc}")

    return recall_events, memory_conflicts, raw_memories


def _build_scene_state_dict(
    world_id: str | None,
    pc_id: str,
    npc_id: str,
    location_name: str,
    scene_types: list[str],
    recent_story: str,
    npcs: list[dict],
) -> dict:
    """Build prompt-ready scene continuity state."""
    participants = [pc_id, npc_id] + [npc.get("char_id") for npc in npcs if npc.get("char_id")]
    scene_state = get_scene_state(
        world_id=world_id,
        pc_id=pc_id,
        npc_id=npc_id,
        location=location_name,
        participants=participants,
        scene_types=scene_types,
        recent_story=recent_story,
    )
    return scene_state_to_prompt_dict(scene_state)


def _build_context_plan_dict(
    scene_types: list[str],
    user_input: str,
    scene_state: dict,
    world_config: dict,
) -> tuple[dict, bool]:
    """Build prompt-ready context plan and report whether memory is required."""
    context_plan = build_context_plan(
        scene_types=scene_types,
        user_input=user_input,
        scene_state=scene_state,
        world_config=world_config,
    )
    return context_plan_to_prompt_dict(context_plan), "memory" in context_plan.required_systems


def _memory_limit(scene_types: list[str] | None) -> int:
    """Return memory retrieval limit based on scene depth."""
    if scene_types and any(s in {"bonding", "emotional", "vulnerable", "intimate"} for s in scene_types):
        return 5
    return 3


def _recency_score(created_at: str | None, current_dt: datetime | None) -> float:
    """Inverse-distance decay over in-game days since memory creation (half-life 30 days)."""
    if not created_at or not current_dt:
        return 0.5
    try:
        mem_dt = datetime.fromisoformat(created_at)
        days_ago = max(0, (current_dt - mem_dt).days)
        return 1.0 / (1.0 + days_ago / 30)
    except Exception:
        return 0.5


def _importance_score(importance: object) -> float:
    """Normalize memory importance (3–10) to 0–1."""
    try:
        return max(0.0, min(1.0, (int(importance) - 3) / 7))
    except (TypeError, ValueError):
        return 0.5
