# ================================
# src/simulation/systems/kakao/__init__.py
#
# KakaoTalk room simulation, command helpers, and prompt context.
#
# Functions
#   - ensure_default_rooms(pc_id: str, npc_id: str, current_time: datetime) -> None : Ensure baseline KakaoTalk rooms exist.
#   - generate_turn_messages(pc_id: str, npc_id: str, current_time: datetime, recent_story: str = "") -> list[str] : Generate autonomous KakaoTalk messages for visible rooms.
#   - process_kakao_before_actor(pc_id: str, npc_id: str, current_time: datetime, pending_player_messages: list[dict], recent_story: str = "", world_hints: dict | None = None) -> dict : Build deferred KakaoTalk turn context and effects.
#   - commit_kakao_effects(effects: list[dict]) -> None : Persist accepted deferred KakaoTalk message effects.
#   - fetch_kakao_panel_state(pc_id: str, active_room_id: str | None = None) -> dict : Fetch UI-ready KakaoTalk panel state.
#   - fetch_kakao_context(pc_id: str, limit_rooms: int = 3, limit_messages: int = 5) -> list[dict] : Fetch prompt-ready KakaoTalk room context.
#   - send_player_message(pc_id: str, room_ref: str, content: str, current_time: datetime) -> str : Store a player KakaoTalk message.
#   - invite_character(pc_id: str, room_ref: str, char_ref: str, current_time: datetime) -> str : Invite a character to a KakaoTalk room.
# ================================
import hashlib
import json
from datetime import datetime

from src.config import MODEL_STATE_UPDATER
from src.core.database import async_driver
from src.core.llm.client import extract_json_from_llm, get_model
from src.simulation.systems.kakao.models import KakaoMessageDraft, KakaoRoomSummary

KAKAO_MODEL = MODEL_STATE_UPDATER
MAX_GENERATED_MESSAGES = 3
MAX_CONTEXT_ROOMS = 3
MAX_CONTEXT_MESSAGES = 5


async def generate_turn_messages(
    pc_id: str,
    npc_id: str,
    current_time: datetime,
    recent_story: str = "",
) -> list[str]:
    """Generate autonomous KakaoTalk messages for rooms the player belongs to."""
    if not pc_id:
        return []

    await _ensure_default_rooms(pc_id, npc_id, current_time)
    rooms = await _fetch_visible_rooms(pc_id, limit=MAX_CONTEXT_ROOMS)
    if not rooms:
        return []

    generated: list[str] = []
    remaining = MAX_GENERATED_MESSAGES
    for room in rooms:
        if remaining <= 0:
            break
        members = await _fetch_room_members(room.id)
        candidates = [member for member in members if member.get("id") != pc_id]
        if not candidates:
            continue
        count = 1 if remaining == 1 else min(2, remaining)
        drafts = await _generate_room_drafts(room, candidates, recent_story, count=count)
        for draft in drafts[:remaining]:
            sender = next((member for member in candidates if member.get("id") == draft.sender_id), None)
            if not sender:
                continue
            await _store_message(
                room_id=room.id,
                sender_id=draft.sender_id,
                sender_name=sender.get("name") or draft.sender_id,
                content=draft.content,
                timestamp=current_time,
                source="auto",
            )
            generated.append(f"{room.name} / {sender.get('name') or draft.sender_id}: {draft.content}")
            remaining -= 1
            if remaining <= 0:
                break
    return generated


async def process_kakao_before_actor(
    pc_id: str,
    npc_id: str,
    current_time: datetime,
    pending_player_messages: list[dict],
    recent_story: str = "",
    world_hints: dict | None = None,
) -> dict:
    """Build prompt context for queued KakaoTalk messages without committing turn messages."""
    if not pc_id:
        return {}

    await _ensure_default_rooms(pc_id, npc_id, current_time)
    turn_messages: list[dict] = []
    touched_room_ids: list[str] = []

    for idx, item in enumerate(pending_player_messages):
        room_ref = str(item.get("room_id") or "").strip()
        content = str(item.get("content") or "").strip()
        if not room_ref or not content:
            continue
        room = await _resolve_visible_room(pc_id, room_ref)
        if room is None:
            continue
        timestamp = _parse_datetime(str(item.get("created_at") or ""), fallback=current_time)
        timestamp = timestamp.replace(microsecond=min(999999, timestamp.microsecond + idx))
        sender_name = await _fetch_character_name(pc_id) or "플레이어"
        touched_room_ids.append(room.id)
        turn_messages.append(_turn_context_entry(room, pc_id, sender_name, content, timestamp, "player"))

    npc_entries = await _generate_kakao_agent_messages(
        pc_id=pc_id,
        current_time=current_time,
        recent_story=recent_story,
        preferred_room_ids=touched_room_ids,
        world_hints=world_hints or {},
    )
    turn_messages.extend(npc_entries)
    turn_messages.sort(key=lambda message: message.get("timestamp") or "")

    if not turn_messages:
        return {}
    return {"messages": turn_messages, "effects": turn_messages}


async def commit_kakao_effects(effects: list[dict]) -> None:
    """Persist accepted deferred KakaoTalk message effects."""
    for effect in effects:
        room_id = str(effect.get("room_id") or "").strip()
        sender_id = str(effect.get("sender_id") or "").strip()
        sender_name = str(effect.get("sender_name") or sender_id).strip()
        content = str(effect.get("content") or "").strip()
        source = str(effect.get("source") or "auto").strip()
        if not room_id or not sender_id or not content:
            continue
        timestamp = _parse_datetime(str(effect.get("timestamp") or ""), fallback=datetime.now())
        await _store_message(
            room_id=room_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            timestamp=timestamp,
            source=source,
        )


async def ensure_default_rooms(pc_id: str, npc_id: str, current_time: datetime) -> None:
    """Ensure baseline KakaoTalk rooms exist for the player session."""
    await _ensure_default_rooms(pc_id, npc_id, current_time)


async def fetch_kakao_context(
    pc_id: str,
    limit_rooms: int = MAX_CONTEXT_ROOMS,
    limit_messages: int = MAX_CONTEXT_MESSAGES,
) -> list[dict]:
    """Fetch prompt-ready KakaoTalk rooms visible to the player."""
    rooms = await _fetch_visible_rooms(pc_id, limit=limit_rooms)
    context: list[dict] = []
    for room in rooms:
        context.append({
            "room": room.name,
            "topic": room.topic,
            "members": room.members,
            "recent_messages": room.recent_messages[-limit_messages:],
        })
    return context


async def fetch_kakao_panel_state(pc_id: str, active_room_id: str | None = None) -> dict:
    """Fetch UI-ready room, message, and invite candidate state."""
    rooms = await _fetch_visible_rooms(pc_id, limit=30)
    active_room = None
    if active_room_id:
        active_room = next((room for room in rooms if room.id == active_room_id), None)
    if active_room is None and rooms:
        active_room = rooms[0]

    messages = await _fetch_room_messages(active_room.id, limit=50) if active_room else []
    candidates = await _fetch_invite_candidates(active_room.id if active_room else None)
    return {
        "rooms": [room.model_dump() for room in rooms],
        "activeRoomId": active_room.id if active_room else "",
        "messages": messages,
        "inviteCandidates": candidates,
    }


async def send_player_message(
    pc_id: str,
    room_ref: str,
    content: str,
    current_time: datetime,
) -> str:
    """Store a player KakaoTalk message in a visible room."""
    room = await _resolve_visible_room(pc_id, room_ref)
    if room is None:
        return f"톡방을 찾을 수 없거나 참여 중이 아닙니다: `{room_ref}`"
    pc_name = await _fetch_character_name(pc_id) or "플레이어"
    await _store_message(
        room_id=room.id,
        sender_id=pc_id,
        sender_name=pc_name,
        content=content,
        timestamp=current_time,
        source="player",
    )
    return f"`{room.name}`에 메시지를 보냈습니다: {content}"


async def invite_character(
    pc_id: str,
    room_ref: str,
    char_ref: str,
    current_time: datetime,
) -> str:
    """Invite a known character to a visible KakaoTalk room."""
    room = await _resolve_visible_room(pc_id, room_ref)
    if room is None:
        return f"톡방을 찾을 수 없거나 참여 중이 아닙니다: `{room_ref}`"
    character = await _resolve_character(char_ref)
    if character is None:
        return f"캐릭터를 찾을 수 없습니다: `{char_ref}`"
    await _ensure_member(room.id, character["id"])
    await _store_message(
        room_id=room.id,
        sender_id=pc_id,
        sender_name="시스템",
        content=f"{character['name']}님이 초대되었습니다.",
        timestamp=current_time,
        source="system",
    )
    return f"`{room.name}`에 `{character['name']}`을(를) 초대했습니다."


async def _ensure_default_rooms(pc_id: str, npc_id: str, current_time: datetime) -> None:
    """Create the default one-on-one KakaoTalk room when it is missing."""
    if not pc_id or not npc_id:
        return
    npc_name = await _fetch_character_name(npc_id) or npc_id
    room_id = _room_id_for([pc_id, npc_id], "direct")
    room_name = f"{npc_name} 톡방"
    await _ensure_room(room_id, room_name, "1:1 chat", current_time)
    await _ensure_member(room_id, pc_id)
    await _ensure_member(room_id, npc_id)


async def _ensure_room(room_id: str, name: str, topic: str, current_time: datetime) -> None:
    """Create a KakaoTalk room if it does not already exist."""
    timestamp = current_time.isoformat()
    async with async_driver.session() as session:
        rec = await session.run("MATCH (r:KakaoRoom {id: $id}) RETURN r.id AS id", id=room_id)
        if await rec.single():
            return
        await session.run(
            """
            CREATE (:KakaoRoom {
                id: $id,
                name: $name,
                topic: $topic,
                status: "active",
                created_at: $timestamp,
                last_active_at: $timestamp
            })
            """,
            id=room_id,
            name=name,
            topic=topic,
            timestamp=timestamp,
        )


async def _ensure_member(room_id: str, char_id: str) -> None:
    """Ensure a Character is a member of a KakaoTalk room."""
    async with async_driver.session() as session:
        rec = await session.run(
            """
            MATCH (c:Character {id: $char_id})-[:MEMBER_OF]->(r:KakaoRoom {id: $room_id})
            RETURN c.id AS id
            """,
            char_id=char_id,
            room_id=room_id,
        )
        if await rec.single():
            return
        await session.run(
            """
            MATCH (c:Character {id: $char_id}), (r:KakaoRoom {id: $room_id})
            CREATE (c)-[:MEMBER_OF]->(r)
            """,
            char_id=char_id,
            room_id=room_id,
        )


async def _fetch_visible_rooms(pc_id: str, limit: int) -> list[KakaoRoomSummary]:
    """Return KakaoTalk rooms where the player is a member."""
    async with async_driver.session() as session:
        rec = await session.run(
            """
            MATCH (:Character {id: $pc_id})-[:MEMBER_OF]->(r:KakaoRoom)
            RETURN r.id AS id,
                   r.name AS name,
                   r.topic AS topic,
                   r.last_active_at AS last_active_at
            ORDER BY r.last_active_at DESC
            LIMIT $limit
            """,
            pc_id=pc_id,
            limit=limit,
        )
        rows = await rec.data()

    rooms: list[KakaoRoomSummary] = []
    for row in rows:
        room_id = row["id"]
        members = await _fetch_room_member_names(room_id)
        messages = await _fetch_room_messages(room_id, limit=MAX_CONTEXT_MESSAGES)
        rooms.append(KakaoRoomSummary(
            id=room_id,
            name=row["name"] or room_id,
            topic=row["topic"] or "",
            members=members,
            recent_messages=[
                f"{message.get('sender_name')}: {message.get('content')}"
                for message in messages
            ],
        ))
    return rooms


async def _resolve_visible_room(pc_id: str, room_ref: str) -> KakaoRoomSummary | None:
    """Resolve a room by id or name among rooms visible to the player."""
    text = room_ref.strip()
    for room in await _fetch_visible_rooms(pc_id, limit=50):
        if text in {room.id, room.name}:
            return room
    return None


async def _fetch_room_members(room_id: str) -> list[dict]:
    """Fetch room member ids, names, and profile summaries."""
    async with async_driver.session() as session:
        rec = await session.run(
            """
            MATCH (c:Character)-[:MEMBER_OF]->(:KakaoRoom {id: $room_id})
            OPTIONAL MATCH (c)-[:HAS_PROFILE]->(sp:StaticProfile)
            OPTIONAL MATCH (c)-[:HAS_PERSONALITY]->(p:Personality)
            RETURN c.id AS id,
                   c.name AS name,
                   sp.props AS profile_props,
                   p.props AS personality_props
            """,
            room_id=room_id,
        )
        rows = await rec.data()
    return [
        {
            "id": row["id"],
            "name": row["name"] or row["id"],
            "profile": _safe_json(row.get("profile_props")),
            "personality": _safe_json(row.get("personality_props")),
        }
        for row in rows
    ]


async def _fetch_room_member_names(room_id: str) -> list[str]:
    """Fetch display names for room members."""
    members = await _fetch_room_members(room_id)
    return [member["name"] for member in members]


async def _fetch_room_messages(room_id: str, limit: int) -> list[dict]:
    """Fetch recent KakaoTalk messages in chronological order."""
    async with async_driver.session() as session:
        rec = await session.run(
            """
            MATCH (:KakaoRoom {id: $room_id})-[:ROOM_HAS_MESSAGE]->(m:KakaoMessage)
            RETURN m.id AS id,
                   m.sender_id AS sender_id,
                   m.sender_name AS sender_name,
                   m.content AS content,
                   m.timestamp AS timestamp,
                   m.source AS source
            ORDER BY m.timestamp DESC
            LIMIT $limit
            """,
            room_id=room_id,
            limit=limit,
        )
        rows = await rec.data()
    return list(reversed([dict(row) for row in rows]))


async def _fetch_invite_candidates(room_id: str | None) -> list[dict]:
    """Fetch characters that can be invited into the active room."""
    async with async_driver.session() as session:
        rec = await session.run(
            """
            MATCH (c:Character)
            OPTIONAL MATCH (c)-[:MEMBER_OF]->(r:KakaoRoom)
            RETURN c.id AS id,
                   c.name AS name,
                   collect(r.id) AS room_ids
            ORDER BY name ASC
            LIMIT 80
            """
        )
        rows = await rec.data()
    candidates: list[dict] = []
    for row in rows:
        room_ids = row.get("room_ids") or []
        if room_id and room_id in room_ids:
            continue
        candidates.append({"id": row["id"], "name": row["name"] or row["id"]})
    return candidates


async def _generate_room_drafts(
    room: KakaoRoomSummary,
    candidates: list[dict],
    recent_story: str,
    count: int,
) -> list[KakaoMessageDraft]:
    """Ask the lightweight model for character-matched KakaoTalk messages."""
    recent = "\n".join(room.recent_messages[-5:])
    candidate_payload = [
        {
            "id": member["id"],
            "name": member["name"],
            "profile": member.get("profile", {}),
            "personality": member.get("personality", {}),
        }
        for member in candidates
    ]
    prompt = f"""Create {count} Korean KakaoTalk messages for this fictional room.
Messages must match each sender's personality/profile, feel casual, and be short enough for chat.
Do not write narration. Do not choose the player as sender.

Room: {room.name}
Topic: {room.topic}
Recent story:
{recent_story[-1200:]}

Recent room messages:
{recent}

Candidate senders:
{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}

Return ONLY JSON array: [{{"sender_id":"<candidate id>","content":"<message text>"}}, ...]"""
    try:
        model = get_model(
            KAKAO_MODEL,
            system_prompt="You generate in-universe Korean KakaoTalk messages for roleplay NPCs.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 512,
                "temperature": 0.9,
                "response_mime_type": "application/json",
            },
        )
        parsed = extract_json_from_llm(resp.text)
    except Exception as exc:
        print(f"[Kakao] message generation failed: {exc}")
        return []

    if not isinstance(parsed, list):
        return []
    allowed = {member["id"] for member in candidates}
    drafts: list[KakaoMessageDraft] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        sender_id = str(item.get("sender_id") or "").strip()
        content = str(item.get("content") or "").strip()
        if sender_id in allowed and content:
            drafts.append(KakaoMessageDraft(sender_id=sender_id, content=content[:240]))
    return drafts


async def _store_message(
    room_id: str,
    sender_id: str,
    sender_name: str,
    content: str,
    timestamp: datetime,
    source: str,
) -> None:
    """Persist a KakaoTalk message and link it to room and sender."""
    ts = timestamp.isoformat()
    message_id = _message_id(room_id, sender_id, content, ts)
    # 메시지 노드 + 방/발신자 관계 + 방 갱신을 한 트랜잭션으로 묶어 반쪽 연결을 막는다.
    async with async_driver.transaction() as tx:
        existing = await tx.run(
            "MATCH (m:KakaoMessage {id: $message_id}) RETURN m.id AS id",
            message_id=message_id,
        )
        if await existing.single():
            return
        await tx.run(
            """
            CREATE (:KakaoMessage {
                id: $message_id,
                room_id: $room_id,
                sender_id: $sender_id,
                sender_name: $sender_name,
                content: $content,
                timestamp: $timestamp,
                source: $source,
                status: "active"
            })
            """,
            message_id=message_id,
            room_id=room_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            timestamp=ts,
            source=source,
        )
        await tx.run(
            """
            MATCH (r:KakaoRoom {id: $room_id}), (m:KakaoMessage {id: $message_id})
            CREATE (r)-[:ROOM_HAS_MESSAGE]->(m)
            """,
            room_id=room_id,
            message_id=message_id,
        )
        await tx.run(
            """
            MATCH (c:Character {id: $sender_id}), (m:KakaoMessage {id: $message_id})
            CREATE (c)-[:SENT_KAKAO]->(m)
            """,
            sender_id=sender_id,
            message_id=message_id,
        )
        await tx.run(
            """
            MATCH (r:KakaoRoom {id: $room_id})
            SET r.last_active_at = $timestamp
            """,
            room_id=room_id,
            timestamp=ts,
        )


async def _generate_kakao_agent_messages(
    pc_id: str,
    current_time: datetime,
    recent_story: str,
    preferred_room_ids: list[str],
    world_hints: dict,
) -> list[dict]:
    """Generate deferred NPC KakaoTalk messages for this Actor turn."""
    rooms = await _fetch_visible_rooms(pc_id, limit=MAX_CONTEXT_ROOMS)
    if preferred_room_ids:
        preferred = set(preferred_room_ids)
        rooms = sorted(rooms, key=lambda room: 0 if room.id in preferred else 1)

    generated: list[dict] = []
    remaining = MAX_GENERATED_MESSAGES
    hint_text = _format_world_hints_for_kakao(world_hints)
    story = "\n".join(part for part in [recent_story, hint_text] if part)
    for room in rooms:
        if remaining <= 0:
            break
        members = await _fetch_room_members(room.id)
        candidates = [member for member in members if member.get("id") != pc_id]
        if not candidates:
            continue
        count = 1 if remaining == 1 else min(2, remaining)
        drafts = await _generate_room_drafts(room, candidates, story, count=count)
        for draft in drafts[:remaining]:
            sender = next((member for member in candidates if member.get("id") == draft.sender_id), None)
            if not sender:
                continue
            sender_name = sender.get("name") or draft.sender_id
            timestamp = current_time.replace(microsecond=min(999999, current_time.microsecond + len(generated) + 1))
            generated.append(
                _turn_context_entry(room, draft.sender_id, sender_name, draft.content, timestamp, "auto")
            )
            remaining -= 1
            if remaining <= 0:
                break
    return generated


def _turn_context_entry(
    room: KakaoRoomSummary,
    sender_id: str,
    sender_name: str,
    content: str,
    timestamp: datetime,
    source: str,
) -> dict:
    """Build one prompt-ready this-turn KakaoTalk message entry."""
    return {
        "room_id": room.id,
        "room": room.name,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "content": content,
        "timestamp": timestamp.isoformat(),
        "source": source,
    }


def _parse_datetime(raw: str, fallback: datetime) -> datetime:
    """Parse an ISO datetime string, falling back to the supplied time."""
    if not raw:
        return fallback
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return fallback


def _format_world_hints_for_kakao(world_hints: dict) -> str:
    """Compact relevant world context for the separate KakaoTalk agent."""
    if not world_hints:
        return ""
    compact: dict = {}
    for key in ("scene_state", "nearby_activity", "schedules", "life_goals"):
        value = world_hints.get(key)
        if value:
            compact[key] = value
    if not compact:
        return ""
    return "World hints:\n" + json.dumps(compact, ensure_ascii=False)[:1200]


async def _fetch_character_name(char_id: str) -> str | None:
    """Fetch a character display name by id."""
    async with async_driver.session() as session:
        rec = await session.run("MATCH (c:Character {id: $id}) RETURN c.name AS name", id=char_id)
        row = await rec.single()
    return row["name"] if row else None


async def _resolve_character(char_ref: str) -> dict | None:
    """Resolve a character by id, name, or alias."""
    text = char_ref.strip()
    async with async_driver.session() as session:
        rec = await session.run(
            """
            MATCH (c:Character)
            WHERE c.id = $text OR c.name = $text OR $text IN c.aliases
            RETURN c.id AS id, c.name AS name
            LIMIT 1
            """,
            text=text,
        )
        row = await rec.single()
    return {"id": row["id"], "name": row["name"] or row["id"]} if row else None


def _room_id_for(member_ids: list[str], seed: str) -> str:
    """Build a stable room id from member ids and a seed label."""
    raw = "|".join(sorted(member_ids)) + f"|{seed}"
    digest = hashlib.blake2s(raw.encode("utf-8"), digest_size=6).hexdigest()
    return f"kakao_{digest}"


def _message_id(room_id: str, sender_id: str, content: str, timestamp: str) -> str:
    """Build a unique KakaoMessage id."""
    raw = f"{room_id}|{sender_id}|{content}|{timestamp}"
    digest = hashlib.blake2s(raw.encode("utf-8"), digest_size=10).hexdigest()
    return f"kmsg_{digest}"


def _safe_json(raw: object) -> dict:
    """Parse a JSON object from a graph property."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
