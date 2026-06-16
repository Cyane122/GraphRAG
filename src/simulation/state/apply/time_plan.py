# ================================
# src/simulation/state/apply/time_plan.py
#
# Apply planned or accepted-prose time, weather, and location updates to the DB.
#
# Functions
#   - build_time_plan(plan: dict, base_time: datetime) -> dict : Build a DB-write-ready time plan
#   - commit_time_plan(time_plan: dict, pc_id: str, npc_id: str, companion_ids: list[str] | None = None) -> datetime : Commit a prepared time plan to the DB
#   - parse_prose_header_datetime(actor_response: str) -> datetime | None : Parse the accepted Actor header time
#   - commit_time_from_prose_header(actor_response: str, fallback_time: object = None) -> datetime | None : Commit accepted Actor header time without rollback
#   - apply_time_updates(plan: dict, base_time: datetime, pc_id: str, npc_id: str) -> datetime : Build and commit a time plan
#   - reconcile_location_with_prose(actor_response: str, pc_id: str, npc_id: str, companion_ids: list[str] | None = None) -> str | None : Override committed location when Actor prose header disagrees
# ================================
import re
from datetime import datetime, timedelta

from src.core.database import async_driver, ensure_location, move_location

# ════════════════════════════════════════════════════════════
# 시간 업데이트
# ════════════════════════════════════════════════════════════

def build_time_plan(plan: dict, base_time: datetime) -> dict:
    """시간/날씨/위치 변경 계획을 DB write 없이 계산합니다."""
    action_type = plan.get("action_type", "dialogue")

    if action_type == "ooc_jump" and plan.get("target_hour") is not None:
        target_hour = _coerce_hour(plan.get("target_hour"))
        if target_hour is None:
            target_hour = base_time.hour
        days_to_add = 1 if target_hour <= base_time.hour else 0
        new_time    = base_time.replace(hour=target_hour, minute=0, second=0, microsecond=0) + timedelta(days=days_to_add)
    else:
        minutes = _coerce_elapsed_minutes(plan.get("elapsed_minutes"))
        new_time = base_time + timedelta(minutes=minutes)

    elapsed_minutes = max(1.0, (new_time - base_time).total_seconds() / 60)
    new_weather = plan.get("new_weather")
    new_loc_id = plan.get("new_location_id")
    new_location = plan.get("new_location") if isinstance(plan.get("new_location"), dict) else None

    return {
        "action_type":      action_type,
        "base_time":        base_time.isoformat(),
        "new_time":         new_time.isoformat(),
        "elapsed_minutes":  elapsed_minutes,
        "days_passed":      (new_time.date() - base_time.date()).days,
        "new_weather":      new_weather if new_weather and new_weather != "null" else None,
        "new_location_id":  new_loc_id if new_loc_id and new_loc_id != "null" else None,
        "new_location":     new_location,
        "reason":           plan.get("reason", ""),
    }


def _coerce_elapsed_minutes(value: object, default: int = 3) -> int:
    """LLM 숫자 출력(int/float/숫자 문자열)을 분 단위 정수로 정규화한다."""
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return default
    return minutes if 0 < minutes < 10080 else default


def _coerce_hour(value: object) -> int | None:
    """LLM target_hour 출력을 0..23 범위의 시각으로 정규화한다."""
    try:
        hour = int(float(value))
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


async def _location_exists(location_id: str) -> bool:
    """Location 노드 존재 여부를 확인한다."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (l:Location {id: $loc_id}) RETURN l.id AS id",
            loc_id=location_id,
        )
        return await rec.single() is not None


async def _ensure_planned_location(time_plan: dict, new_loc_id: str | None) -> str | None:
    """Create a planned new Location when the classifier supplied metadata for it."""
    if not new_loc_id:
        return None
    if await _location_exists(new_loc_id):
        return new_loc_id

    new_location = time_plan.get("new_location")
    if not isinstance(new_location, dict):
        new_location = {}
    if not new_location.get("name"):
        new_location = {
            **new_location,
            "name": new_loc_id,
            "description": str(new_location.get("description") or "Dynamically introduced location."),
            "prompt_hint": str(new_location.get("prompt_hint") or "Current scene location."),
            "tags": list(new_location.get("tags") or ["dynamic"]),
            "prompt_priority": new_location.get("prompt_priority") or 6,
        }

    return await ensure_location(
        location_id=new_loc_id,
        name=str(new_location.get("name") or new_loc_id),
        description=str(new_location.get("description") or ""),
        prompt_hint=str(new_location.get("prompt_hint") or new_location.get("description") or ""),
        parent_location_id=new_location.get("parent_location_id"),
        tags=list(new_location.get("tags") or ["dynamic"]),
        prompt_priority=new_location.get("prompt_priority") or 8,
    )


def _esc(value: str) -> str:
    """KuzuDB Cypher 문자열 리터럴용 단순 이스케이프 (내부 신뢰 값 전용)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


_PROSE_HEADER_LOCATION_RE = re.compile(r"\*\*[^*\n]*?\d{1,2}\s*분\s*,\s*([^*\n]+?)\s*\*\*")
_ANALYZE_BLOCK_RE = re.compile(r"<analyze>[\s\S]*?</analyze>", re.IGNORECASE)
_BOLD_HEADER_RE = re.compile(r"\*\*(?P<header>[^*\n]+)\*\*")
_HEADER_DATE_RE = re.compile(
    r"(?P<year>\d{4})\s*년\s*"
    r"(?P<month>\d{1,2})\s*월\s*"
    r"(?P<day>\d{1,2})\s*일"
    r"(?:\s*,?\s*\(?[월화수목금토일]\s*요일?\)?)?"
)
_HEADER_TIME_RE = re.compile(
    r"(?:(?P<ampm>오전|오후|새벽|아침|저녁|밤)\s*)?"
    r"(?P<hour>\d{1,2})\s*시"
    r"(?:\s*(?P<minute>\d{1,2})\s*분)?"
)


def _visible_prose(actor_response: str) -> str:
    """Return response text with analysis blocks removed for prose-header parsing."""
    return _ANALYZE_BLOCK_RE.sub("", actor_response or "").strip()


def _first_prose_header(actor_response: str) -> str | None:
    """Return the first bold prose header outside analyze blocks."""
    match = _BOLD_HEADER_RE.search(_visible_prose(actor_response))
    return match.group("header").strip() if match else None


def _coerce_header_hour(hour: int, ampm: str | None) -> int:
    """Convert Korean AM/PM markers into a 24-hour clock."""
    marker = str(ampm or "").strip()
    if marker in {"오후", "저녁", "밤"} and hour < 12:
        return hour + 12
    if marker in {"오전", "새벽", "아침"} and hour == 12:
        return 0
    return hour


def parse_prose_header_datetime(actor_response: str) -> datetime | None:
    """Parse the accepted Actor prose header into a datetime, if present."""
    header = _first_prose_header(actor_response)
    if not header:
        return None
    date_match = _HEADER_DATE_RE.search(header)
    time_match = _HEADER_TIME_RE.search(header)
    if not date_match or not time_match:
        return None
    try:
        hour = _coerce_header_hour(int(time_match.group("hour")), time_match.group("ampm"))
        minute = int(time_match.group("minute") or 0)
        return datetime(
            int(date_match.group("year")),
            int(date_match.group("month")),
            int(date_match.group("day")),
            hour,
            minute,
        )
    except ValueError:
        return None


def _parse_fallback_datetime(value: object) -> datetime | None:
    """Parse a fallback datetime value for downstream systems."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


async def commit_time_from_prose_header(
    actor_response: str,
    fallback_time: object = None,
) -> datetime | None:
    """Commit accepted Actor prose header time without using Manager time planning."""
    header_dt = parse_prose_header_datetime(actor_response)
    fallback_dt = _parse_fallback_datetime(fallback_time)
    if header_dt is None:
        return fallback_dt
    if fallback_dt and header_dt < fallback_dt:
        print(
            "[ActorHeaderTime] skipped backward header "
            f"{header_dt.strftime('%Y-%m-%d %H:%M')} < {fallback_dt.strftime('%Y-%m-%d %H:%M')}"
        )
        return fallback_dt

    safe_value = _esc(header_dt.isoformat())
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET gs.currentTime = '{safe_value}'"
        )
    print(f"[ActorHeaderTime] {header_dt.strftime('%Y-%m-%d %H:%M')}")
    return header_dt


def _parse_prose_header_location(actor_response: str) -> str | None:
    """Actor 산문 첫머리 헤더(**...분, 장소**)에서 장소명을 추출한다. 없으면 None."""
    header = _first_prose_header(actor_response)
    if header:
        date_match = _HEADER_DATE_RE.search(header)
        if date_match:
            suffix = header[date_match.end():].strip()
            time_match = _HEADER_TIME_RE.search(suffix)
            if time_match:
                suffix = suffix[time_match.end():].strip()
            suffix = re.sub(r"^\s*[,，.。]?\s*\(?[월화수목금토일]\s*요일?\)?\s*", "", suffix)
            suffix = suffix.lstrip(" ,，.。")
            if suffix:
                location = re.split(r"[,，.。]\s*", suffix)[-1].strip()
                if location:
                    return location

    match = _PROSE_HEADER_LOCATION_RE.search(_visible_prose(actor_response))
    return match.group(1).strip() if match else None


async def _fetch_location_name_index() -> tuple[dict[str, str], str | None]:
    """(이름·id 소문자 → location_id) 매핑과 현재 GlobalState.currentLocationId를 반환한다."""
    name_to_id: dict[str, str] = {}
    async with async_driver.session() as session:
        loc_result = await session.run("MATCH (l:Location) RETURN l.id AS id, l.name AS name")
        for row in await loc_result.fetch_all():
            data = dict(row)
            loc_id = data.get("id")
            if not loc_id:
                continue
            name_to_id.setdefault(str(loc_id).lower(), str(loc_id))
            name = str(data.get("name") or "").strip().lower()
            if name:
                name_to_id.setdefault(name, str(loc_id))
        gs_rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentLocationId AS loc"
        )
        gs_row = await gs_rec.single()
    current_id = dict(gs_row).get("loc") if gs_row else None
    return name_to_id, (str(current_id) if current_id else None)


async def reconcile_location_with_prose(
    actor_response: str,
    pc_id: str,
    npc_id: str,
    companion_ids: list[str] | None = None,
) -> str | None:
    """Actor 산문 헤더의 장소가 Manager 사전판정과 다르면 산문 우선으로 위치를 보정한다.

    헤더 장소명이 알려진 Location과 정확히 매칭되고 현재 currentLocationId와 다를 때만
    GlobalState와 등장 캐릭터(PC·주NPC·동행) 위치를 보정한다. 모호/미상이면 Manager 값 유지(보수적).
    반환: 보정된 location_id (보정 없으면 None).
    """
    header_name = _parse_prose_header_location(actor_response)
    if not header_name:
        return None

    name_to_id, current_id = await _fetch_location_name_index()
    if not name_to_id:
        return None

    # 헤더 전체 이름 또는 첫 콤마 이전 토큰을 알려진 Location 이름과 정확 매칭한다.
    resolved_id: str | None = None
    for candidate in (header_name, header_name.split(",")[0].strip()):
        resolved_id = name_to_id.get(candidate.lower())
        if resolved_id:
            break

    if not resolved_id or resolved_id == current_id:
        return None

    async with async_driver.session() as session:
        await session.run(
            f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET gs.currentLocationId = '{_esc(resolved_id)}'"
        )
    present_companions = await _present_companion_ids(pc_id, companion_ids or [])
    move_targets: list[str] = []
    for char_id in (pc_id, npc_id, *present_companions):
        if char_id and char_id not in move_targets:
            move_targets.append(char_id)
    for char_id in move_targets:
        await move_location(char_id, resolved_id)
    print(f"[LocationReconcile] prose overrides plan: {current_id} -> {resolved_id} ('{header_name}')")
    return resolved_id


async def _present_companion_ids(reference_char_id: str, companion_ids: list[str]) -> list[str]:
    """동행 후보 중 reference 캐릭터와 현재 같은 위치에 있는(=실제 동석 중인) NPC만 남긴다.

    scene NPC 집합에는 '언급되었지만 다른 장소에 있는' 인물도 포함될 수 있어, 그런 NPC가
    그룹 이동에 끌려가지 않도록 현재 위치 기준으로 필터링한다. 순서는 입력 순서를 유지한다.
    """
    if not reference_char_id or not companion_ids:
        return []
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character {id: $cid})-[:LOCATED_AT]->(l:Location) RETURN l.id AS id",
            cid=reference_char_id,
        )
        row = await rec.single()
        reference_loc = dict(row).get("id") if row else None
        if not reference_loc:
            return []
        result = await session.run(
            "MATCH (c:Character)-[:LOCATED_AT]->(l:Location {id: $loc}) RETURN c.id AS id",
            loc=reference_loc,
        )
        rows = await result.fetch_all()
    present_at_loc = {dict(r).get("id") for r in rows if dict(r).get("id")}
    return [cid for cid in companion_ids if cid in present_at_loc]


async def commit_time_plan(time_plan: dict, pc_id: str, npc_id: str, companion_ids: list[str] | None = None) -> datetime:
    """계산된 시간 계획을 GlobalState와 위치 관계에 확정 반영합니다."""
    new_time = datetime.fromisoformat(time_plan["new_time"])

    # KuzuDB parsed_parameter_expression.h:copy() 버그 우회:
    # SET 절에 $param 을 사용하면 쿼리 플래너가 KU_UNREACHABLE을 트리거하므로
    # 신뢰된 내부 값은 리터럴로 직접 삽입한다.
    set_clauses = [f"gs.currentTime = '{_esc(new_time.isoformat())}'"]

    if time_plan.get("new_weather"):
        set_clauses.append(f"gs.weather = '{_esc(time_plan['new_weather'])}'")

    new_loc_id = await _ensure_planned_location(time_plan, time_plan.get("new_location_id"))
    if new_loc_id:
        set_clauses.append(f"gs.currentLocationId = '{_esc(new_loc_id)}'")

    try:
        async with async_driver.session() as session:
            await session.run(
                f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET {', '.join(set_clauses)}"
            )

        if new_loc_id:
            # 장면이 이동하면 PC·주 NPC뿐 아니라 현재 PC와 같은 장소에 있던 동행 NPC도 함께 옮긴다.
            # 그러지 않으면 동행자의 LOCATED_AT가 이전 장소에 남아 다음 턴 presence에서 유령처럼 잔존한다.
            # 단지 '언급'만 된(실제로는 다른 장소에 있는) NPC는 _present_companion_ids로 걸러낸다.
            present_companions = await _present_companion_ids(pc_id, companion_ids or [])
            move_targets: list[str] = []
            for char_id in (pc_id, npc_id, *present_companions):
                if char_id and char_id not in move_targets:
                    move_targets.append(char_id)
            for char_id in move_targets:
                await move_location(char_id, new_loc_id)

        print(f"[TimeManager] {new_time.strftime('%Y-%m-%d %H:%M')} | {time_plan.get('reason', '')}")

    except Exception as e:
        print(f"[TimeManager] DB 업데이트 실패: {e}")
        raise

    return new_time


async def apply_time_updates(
    plan:      dict,
    base_time: datetime,
    pc_id:     str,
    npc_id:    str,
) -> datetime:
    """
    manager_agent에서 계산된 plan을 받아 GlobalState + 캐릭터 DB 반영.

    Returns: 새로운 인게임 datetime
    """
    time_plan = build_time_plan(plan, base_time)
    return await commit_time_plan(time_plan, pc_id, npc_id)
