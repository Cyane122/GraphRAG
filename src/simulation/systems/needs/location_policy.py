# ================================
# src/simulation/systems/needs/location_policy.py
#
# NPC autonomous need actions에 사용할 장소 후보와 스케줄 지연 정책을 계산합니다.
#
# Functions
#   - action_time_after_schedule(npc_id: str, need_time: datetime, current_time: datetime, schedule_rows: list[dict] | None = None) -> datetime | None : 스케줄을 고려한 욕구 해소 가능 시각을 반환합니다.
#   - filter_locations_for_need(need_name: str, current_location_id: str, locations: list[dict]) -> list[dict] : 욕구별로 허용되는 장소 후보를 반환합니다.
# ================================

from datetime import datetime, time, timedelta

from src.simulation.systems.scheduling.schedules import _coerce_minute, _matches_date

_FOOD_TOKENS = {
    "food", "meal", "restaurant", "cafeteria", "canteen", "kitchen", "convenience",
    "store", "snack", "cafe", "dining", "식당", "급식", "매점", "편의점", "카페", "주방",
}
_REST_TOKENS = {
    "home", "bedroom", "bed", "dorm", "room", "quiet", "private", "rest", "sleep",
    "집", "방", "침실", "기숙사", "휴게", "보건실", "양호실",
}
_SOCIAL_TOKENS = {
    "lounge", "club", "classroom", "cafe", "hall", "yard", "park", "social", "public",
    "교실", "동아리", "카페", "복도", "운동장", "공원", "라운지",
}
_FUN_TOKENS = {
    "game", "arcade", "pc", "club", "park", "cafe", "room", "media", "library",
    "게임", "오락", "피시", "동아리", "공원", "카페", "방", "도서관",
}
_PRIVATE_TOKENS = {
    "private", "bathroom", "restroom", "toilet", "washroom", "shower", "bedroom",
    "home", "dorm", "room", "stall", "화장실", "욕실", "샤워", "침실", "집", "기숙사", "개인실",
}


def action_time_after_schedule(
    npc_id: str,
    need_time: datetime,
    current_time: datetime,
    schedule_rows: list[dict] | None = None,
) -> datetime | None:
    """스케줄 중이면 종료 뒤로 미루고, 현재 턴 안에 처리할 수 없으면 None을 반환합니다."""
    active_end = _active_schedule_end(npc_id, need_time, schedule_rows or [])
    if active_end is None:
        return need_time
    if active_end > current_time:
        return None
    return active_end


def filter_locations_for_need(
    need_name: str,
    current_location_id: str,
    locations: list[dict],
) -> list[dict]:
    """욕구별 후보 장소를 보수적으로 제한하고, 없으면 현재 장소 후보로 후퇴합니다."""
    if not locations:
        return []

    current = [loc for loc in locations if loc.get("id") == current_location_id]
    tokens = _tokens_for_need(need_name)
    if not tokens:
        return current or locations[:8]

    scored = [
        (score, loc)
        for loc in locations
        if (score := _location_score(loc, tokens, current_location_id)) > 0
    ]
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
    filtered = [loc for _, loc in scored[:8]]
    if filtered:
        return filtered

    if need_name == "libido":
        return current if current and _location_score(current[0], _PRIVATE_TOKENS, current_location_id) > 0 else []
    return current or locations[:8]


def _active_schedule_end(
    npc_id: str,
    at_time: datetime,
    schedule_rows: list[dict],
) -> datetime | None:
    """해당 시각에 NPC가 진행 중인 스케줄의 종료 시각을 반환합니다."""
    current_minute = at_time.hour * 60 + at_time.minute
    latest_end: datetime | None = None
    for row in schedule_rows:
        if row.get("owner_id") != npc_id or not _matches_date(row, at_time):
            continue
        start_minute = _coerce_minute(row.get("start_minute"), row.get("start_time"))
        end_minute = _coerce_minute(row.get("end_minute"), row.get("end_time"))
        if start_minute < 0 or end_minute < 0:
            continue
        if not _minute_in_window(current_minute, start_minute, end_minute):
            continue
        end_dt = datetime.combine(at_time.date(), time.min) + timedelta(minutes=end_minute)
        if end_minute < start_minute:
            end_dt += timedelta(days=1)
        if latest_end is None or end_dt > latest_end:
            latest_end = end_dt
    return latest_end


def _minute_in_window(current_minute: int, start_minute: int, end_minute: int) -> bool:
    """분 단위 시간이 스케줄 구간 안에 있는지 확인합니다."""
    if end_minute < start_minute:
        return current_minute >= start_minute or current_minute <= end_minute
    return start_minute <= current_minute <= end_minute


def _tokens_for_need(need_name: str) -> set[str]:
    """욕구 이름에 맞는 장소 키워드 세트를 반환합니다."""
    if need_name == "hunger":
        return _FOOD_TOKENS
    if need_name == "rest":
        return _REST_TOKENS
    if need_name == "social":
        return _SOCIAL_TOKENS
    if need_name == "fun":
        return _FUN_TOKENS
    if need_name == "libido":
        return _PRIVATE_TOKENS
    return set()


def _location_score(location: dict, tokens: set[str], current_location_id: str) -> int:
    """장소 id/name/tags/parent_ids가 후보 키워드와 얼마나 맞는지 점수화합니다."""
    haystack = " ".join(
        str(part).lower()
        for part in (
            location.get("id") or "",
            location.get("name") or "",
            " ".join(str(tag) for tag in (location.get("tags") or [])),
            " ".join(str(parent) for parent in (location.get("parent_ids") or [])),
        )
    )
    score = sum(1 for token in tokens if token.lower() in haystack)
    if location.get("id") == current_location_id:
        score += 1
    return score
