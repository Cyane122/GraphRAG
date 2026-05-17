# ================================
# src/agents/context/scene_state.py
#
# Lightweight current-scene state storage and update heuristics.
#
# Classes
#   - SceneState : current scene continuity snapshot
#
# Functions
#   - get_scene_state(world_id: str | None, pc_id: str, npc_id: str, location: str | None, participants: list[str], scene_types: list[str], recent_story: str) -> SceneState : load or create current SceneState
#   - update_scene_state_after_response(world_id: str | None, pc_id: str, npc_id: str, user_input: str, actor_response: str, scene_types: list[str], scene_chars: list[str], location: str | None) -> SceneState : commit-time SceneState update
#   - scene_state_to_prompt_dict(scene_state: SceneState) -> dict : prompt-safe state dict
# ================================

from dataclasses import asdict, dataclass, field
from hashlib import sha1
import re


_SCENE_STORE: dict[str, "SceneState"] = {}
_MAX_UNRESOLVED_BEATS = 5

_TENSION_WORDS = re.compile(
    r"(angry|fight|argument|cry|tears|tremble|panic|fear|kiss|touch|"
    r"화|싸움|말다툼|눈물|울|떨|불안|키스|입맞춤|껴안|붙잡)"
)
_DISTANCE_CLOSE_WORDS = re.compile(r"(kiss|hug|touch|hold|키스|입맞춤|껴안|안아|손을 잡|붙잡)")
_DISTANCE_DISTANT_WORDS = re.compile(r"(step back|leave|silence|뒤로|물러|떠나|침묵|외면)")
_SCENE_BREAK_WORDS = re.compile(r"(다음 날|다음날|며칠 뒤|장소를 옮|집으로|학교로|회사로|카페로|after a few days|next day)")


@dataclass
class SceneState:
    """Current scene continuity snapshot used by the context planner."""

    scene_id: str
    location: str
    participants: list[str]
    scene_type: str
    mood: str = "neutral"
    tension: float = 0.2
    physical_distance: str = "normal"
    unresolved_beats: list[str] = field(default_factory=list)
    last_action: str = ""


def get_scene_state(
    world_id: str | None,
    pc_id: str,
    npc_id: str,
    location: str | None,
    participants: list[str],
    scene_types: list[str],
    recent_story: str,
) -> SceneState:
    """Load the in-memory SceneState or create one for the current location."""
    key = _store_key(world_id, pc_id, npc_id)
    scene_type = _primary_scene_type(scene_types)
    location_name = location or "unknown"
    existing = _SCENE_STORE.get(key)

    if existing and not _should_start_new_scene(existing, location_name, scene_type, recent_story):
        existing.participants = _merge_participants(existing.participants, participants)
        existing.scene_type = scene_type
        existing.location = location_name
        return existing

    scene_state = SceneState(
        scene_id=_make_scene_id(world_id, pc_id, npc_id, location_name, recent_story),
        location=location_name,
        participants=_merge_participants([pc_id, npc_id], participants),
        scene_type=scene_type,
        mood=_infer_mood(recent_story),
        tension=_infer_tension(recent_story, scene_types),
        physical_distance=_infer_distance(recent_story),
        unresolved_beats=_extract_unresolved_beats(recent_story),
        last_action=_extract_last_action(recent_story),
    )
    _SCENE_STORE[key] = scene_state
    return scene_state


def update_scene_state_after_response(
    world_id: str | None,
    pc_id: str,
    npc_id: str,
    user_input: str,
    actor_response: str,
    scene_types: list[str],
    scene_chars: list[str],
    location: str | None,
) -> SceneState:
    """Update SceneState after an Actor response has been accepted for commit."""
    participants = _merge_participants([pc_id, npc_id], scene_chars)
    scene_state = get_scene_state(
        world_id=world_id,
        pc_id=pc_id,
        npc_id=npc_id,
        location=location,
        participants=participants,
        scene_types=scene_types,
        recent_story=actor_response,
    )
    scene_state.participants = participants
    scene_state.scene_type = _primary_scene_type(scene_types)
    scene_state.mood = _infer_mood(actor_response)
    scene_state.tension = _blend_tension(scene_state.tension, _infer_tension(actor_response, scene_types))
    scene_state.physical_distance = _infer_distance(actor_response)
    scene_state.last_action = _extract_last_action(actor_response)
    scene_state.unresolved_beats = _merge_unresolved_beats(
        scene_state.unresolved_beats,
        _extract_unresolved_beats(f"{user_input}\n{actor_response}"),
    )
    _SCENE_STORE[_store_key(world_id, pc_id, npc_id)] = scene_state
    return scene_state


def scene_state_to_prompt_dict(scene_state: SceneState) -> dict:
    """Return a prompt-safe dict representation of SceneState."""
    return asdict(scene_state)


def _store_key(world_id: str | None, pc_id: str, npc_id: str) -> str:
    """Build the in-memory store key for a PC/NPC pair."""
    return f"{world_id or 'default'}:{pc_id}:{npc_id}"


def _primary_scene_type(scene_types: list[str]) -> str:
    """Choose the first classified scene type, defaulting to daily."""
    return scene_types[0] if scene_types else "daily"


def _make_scene_id(
    world_id: str | None,
    pc_id: str,
    npc_id: str,
    location: str,
    recent_story: str,
) -> str:
    """Create a stable-ish scene id for the current scene seed."""
    seed = f"{world_id}:{pc_id}:{npc_id}:{location}:{recent_story[-120:]}"
    return "scene_" + sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _should_start_new_scene(
    existing: SceneState,
    location: str,
    scene_type: str,
    recent_story: str,
) -> bool:
    """Decide whether continuity should reset."""
    if existing.location != location:
        return True
    if _SCENE_BREAK_WORDS.search(recent_story or ""):
        return True
    return existing.scene_type != scene_type and scene_type in {"intimate", "workplace"}


def _merge_participants(existing: list[str], incoming: list[str]) -> list[str]:
    """Merge participants while preserving order."""
    merged: list[str] = []
    for item in existing + incoming:
        if item and item not in merged:
            merged.append(item)
    return merged


def _infer_mood(text: str) -> str:
    """Infer a broad scene mood from recent text."""
    lowered = (text or "").lower()
    if any(word in lowered for word in ("웃", "미소", "laugh", "smile", "장난")):
        return "warm"
    if any(word in lowered for word in ("불안", "떨", "눈물", "cry", "tears", "panic")):
        return "uneasy"
    if any(word in lowered for word in ("화", "싸움", "angry", "argument")):
        return "tense"
    return "neutral"


def _infer_tension(text: str, scene_types: list[str]) -> float:
    """Infer a conservative 0.0-1.0 tension score."""
    base = 0.25
    if "intimate" in scene_types:
        base = 0.55
    elif any(scene in scene_types for scene in ("bonding", "emotional", "vulnerable", "physical", "tense")):
        base = 0.45
    if _TENSION_WORDS.search(text or ""):
        base += 0.2
    return max(0.0, min(1.0, round(base, 2)))


def _blend_tension(previous: float, current: float) -> float:
    """Blend old and new tension so one line does not swing state too hard."""
    return round(max(0.0, min(1.0, previous * 0.35 + current * 0.65)), 2)


def _infer_distance(text: str) -> str:
    """Infer rough physical distance from recent text."""
    if _DISTANCE_CLOSE_WORDS.search(text or ""):
        return "close"
    if _DISTANCE_DISTANT_WORDS.search(text or ""):
        return "distant"
    return "normal"


def _extract_last_action(text: str) -> str:
    """Use the last non-empty prose line as a compact last-action hint."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:180] if lines else ""


def _extract_unresolved_beats(text: str) -> list[str]:
    """Extract compact unresolved continuity hints from recent text."""
    beats: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "?" in stripped or _TENSION_WORDS.search(stripped):
            beats.append(stripped[:160])
        if len(beats) >= 2:
            break
    return beats


def _merge_unresolved_beats(existing: list[str], incoming: list[str]) -> list[str]:
    """Keep a short deduplicated unresolved-beat list."""
    merged: list[str] = []
    for beat in existing + incoming:
        if beat and beat not in merged:
            merged.append(beat)
    return merged[-_MAX_UNRESOLVED_BEATS:]
