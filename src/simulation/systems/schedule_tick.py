# ================================
# src/simulation/systems/schedule_tick.py
#
# Turn-end hook: scheduled activity 시작 시 NPC를 해당 위치로 이동하고
# 경량 Event 노드를 생성합니다. LLM 호출 없이 순수 DB 작업만 수행합니다.
#
# Functions
#   - run_schedule_tick(pc_id: str, npc_id: str, prev_time: datetime, current_time: datetime, scene_chars: list[str] | None = None, schedule_rows: list[dict] | None = None) -> dict : 스케줄 시작 NPC 위치 이동 + Event 생성
# ================================

import hashlib
from datetime import date, datetime, time, timedelta

from src.core.database import async_driver, move_location
from src.simulation.systems.schedules import (
    _coerce_minute,
    _fetch_schedule_rows,
    _matches_date,
    _parse_material,
)


async def run_schedule_tick(
    pc_id: str,
    npc_id: str,
    prev_time: datetime,
    current_time: datetime,
    scene_chars: list[str] | None = None,
    schedule_rows: list[dict] | None = None,
) -> dict:
    """
    Turn-end hook. 이번 턴에 시작된 스케줄을 감지해 off-scene NPC를
    해당 장소로 이동하고 schedule_start Event를 생성합니다.

    Returns:
        {
            "moved":          [{"npc_id": ..., "location_id": ..., "schedule_name": ...}],
            "events_created": [event_id, ...]
        }
    """
    if current_time <= prev_time:
        return {"moved": [], "events_created": []}

    # scene_set: PC·NPC ID + 한국어 이름 혼합 세트
    scene_set = set(scene_chars or []) | {pc_id, npc_id}

    rows = schedule_rows if schedule_rows is not None else await _fetch_schedule_rows()

    # 이번 interval 안에서 시작한 off-scene schedule 중 NPC별 마지막 일정만 최종 상태에 반영
    latest_by_owner: dict[str, tuple[datetime, dict]] = {}

    for row in rows:
        owner_id = row.get("owner_id") or ""
        location_id = row.get("location_id") or ""
        if not owner_id or not location_id or owner_id == pc_id:
            continue

        start_minute = _coerce_minute(row.get("start_minute"), row.get("start_time"))
        if start_minute < 0:
            continue

        started_at = _latest_start_in_interval(row, start_minute, prev_time, current_time)
        if not started_at:
            continue

        if _npc_in_scene(owner_id, row, scene_set):
            continue

        previous = latest_by_owner.get(owner_id)
        if previous is None or started_at > previous[0]:
            latest_by_owner[owner_id] = (started_at, row)

    moved = []
    events_created = []
    for owner_id, (started_at, row) in sorted(
        latest_by_owner.items(),
        key=lambda item: item[1][0],
    ):
        location_id = row.get("location_id") or ""
        try:
            await move_location(owner_id, location_id)
            if not await _character_at_location(owner_id, location_id):
                print(f"[ScheduleTick] {owner_id} move verification failed (ignored)")
                continue

            moved.append({
                "npc_id": owner_id,
                "location_id": location_id,
                "schedule_name": row.get("name") or row.get("activity") or row.get("id"),
            })

            event_id = await _create_schedule_event(row, started_at)
            if event_id:
                events_created.append(event_id)
        except Exception as e:
            print(f"[ScheduleTick] {owner_id} schedule move failed (ignored): {e}")

    if moved:
        print(f"[ScheduleTick] moved {len(moved)}: {[m['npc_id'] for m in moved]}")

    return {"moved": moved, "events_created": events_created}


def _arrival_jitter(owner_id: str, day: date, max_jitter: int) -> int:
    """(캐릭터ID + 날짜) 해시 기반 결정론적 일별 도착 지터 (분 단위).

    같은 날 같은 캐릭터는 항상 동일한 오프셋을 반환하므로
    재실행해도 일관성이 유지되고, 날이 바뀌면 값이 달라진다.
    """
    raw = hashlib.md5(f"{owner_id}:{day.isoformat()}".encode()).digest()
    seed = int.from_bytes(raw[:4], "big")
    return (seed % (2 * max_jitter + 1)) - max_jitter


def _latest_start_in_interval(
    row: dict,
    start_minute: int,
    prev_time: datetime,
    current_time: datetime,
) -> datetime | None:
    """Return the last schedule start datetime covered by this turn interval.

    Schedule.material에 jitter_minutes가 있으면 결정론적 일별 지터를 적용한다.
    """
    max_jitter = int(_parse_material(row.get("material") or {}).get("jitter_minutes") or 0)
    owner_id = row.get("owner_id") or ""

    latest: datetime | None = None
    day = prev_time.date()
    while day <= current_time.date():
        jitter = _arrival_jitter(owner_id, day, max_jitter) if max_jitter > 0 else 0
        effective_minute = max(0, min(1439, start_minute + jitter))
        start_dt = datetime.combine(day, time.min) + timedelta(minutes=effective_minute)
        if prev_time < start_dt <= current_time and _matches_date(row, start_dt):
            latest = start_dt
        day += timedelta(days=1)
    return latest


def _npc_in_scene(owner_id: str, row: dict, scene_set: set[str]) -> bool:
    """스케줄 소유자가 현재 활성 씬에 있는지 확인합니다."""
    tokens = {owner_id, row.get("owner_name") or ""}
    return bool(tokens & scene_set)


async def _create_schedule_event(
    row: dict,
    started_at: datetime,
) -> str | None:
    """
    LLM 없이 스케줄 시작 Event 노드를 생성합니다.
    Returns event_id, or None on failure.
    """
    from src.agents.resolver import _unique_event_id

    owner_id = row.get("owner_id") or ""
    schedule_name = row.get("name") or row.get("activity") or row.get("id", "")
    location_id = row.get("location_id") or "unknown"
    location_name = row.get("location_name") or location_id
    owner_name = row.get("owner_name") or owner_id

    # prompt_priority(0~20) → importance(1~5) 선형 스케일
    prompt_priority = int(row.get("prompt_priority") or 0)
    importance = max(1, min(5, 1 + prompt_priority // 5))

    context = f"{location_name}에 도착했다"
    summary = f"{owner_name}이(가) {schedule_name}을(를) 시작했다. {context}."

    ts_str = started_at.strftime("%Y%m%d_%H%M")
    base_id = f"schedule_{owner_id}_{ts_str}"

    try:
        event_id = await _unique_event_id(base_id)

        async with async_driver.session() as session:
            await session.run(
                """
                CREATE (e:Event {
                    id:                $eid,
                    summary:           $summary,
                    timestamp:         $ts,
                    location_id:       $loc,
                    impact:            'schedule_start',
                    importance:        $importance,
                    decay_rate:        0.03,
                    summary_level:     0,
                    safety_impact:     0.0,
                    safety_resolved:   true,
                    safety_decay_rate: 0.0
                })
                """,
                eid=event_id,
                summary=summary,
                ts=started_at.isoformat(),
                loc=location_id,
                importance=importance,
            )
            await session.run(
                """
                MATCH (c:Character {id: $cid}), (e:Event {id: $eid})
                CREATE (c)-[:INVOLVED_IN]->(e)
                """,
                cid=owner_id,
                eid=event_id,
            )
            await session.run(
                """
                MATCH (e:Event {id: $eid}), (l:Location {id: $loc})
                CREATE (e)-[:OCCURRED_AT]->(l)
                """,
                eid=event_id,
                loc=location_id,
            )

        return event_id
    except Exception as e:
        print(f"[ScheduleTick] {owner_id} event 생성 실패: {e}")
        return None


async def _character_at_location(char_id: str, location_id: str) -> bool:
    """Verify that move_location actually placed the character at the target location."""
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (c:Character {id: $cid})-[:LOCATED_AT]->(l:Location {id: $lid})
            RETURN l.id AS id
            """,
            cid=char_id,
            lid=location_id,
        )
        return await result.single() is not None
