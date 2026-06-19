# ================================
# src/agents/prompt_factory/ooc_handler.py
#
# OOC(*...* 마커) 텍스트를 파싱해 세계 상태를 즉각 반영합니다.
#
# Functions
#   - is_ooc(text: str) -> bool : 텍스트에 OOC 마커가 있는지 확인
#   - parse_ooc(text: str, npc_id: str, npc_name: str, pc_id: str | None = None, world_config: dict | None = None) -> dict : OOC 분석 후 DB 반영
#   - _render_schedule_context_for_ooc(schedule_context: dict) -> str : 스케줄 컨텍스트를 OOC 프롬프트용 텍스트로 렌더링
# ================================

import re
from datetime import datetime, timedelta
from pathlib import Path

from src.config import MODEL_STATE_UPDATER as OOC_MODEL
from src.core.database import update_dynamic_state, ensure_location, move_location, async_driver
from src.core.llm.client import extract_json_from_llm, get_model, get_response_text
from src.simulation.systems.scheduling.schedules import fetch_schedule_context, SCHEDULE_TIME_PARSE_WINDOW_MIN

_BOLD_RE = re.compile(r'\*\*.*?\*\*', re.DOTALL)
_TIME_SET_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_THREE_HOURS_LATER_RE = re.compile(r"3\s*(?:시간|hours?)\s*(?:후|뒤|later)", re.IGNORECASE)
_NEXT_MORNING_RE = re.compile(r"(?:다음\s*날|next\s*day).*(?:아침|morning)", re.IGNORECASE)
_DATE_KOR_RE = re.compile(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일(?:\s*(\d{1,2})시(?:\s*(\d{1,2})분)?)?')
_NEW_DATETIME_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}')
_GROUP_MOVE_RE = re.compile(r"(다\s*같이|모두|전원|네\s*명|4\s*명|일행|친구들|애들)")
_DESTINATION_MOVE_RE = re.compile(
    r"(?P<dest>[가-힣A-Za-z0-9_·''\-\s]{1,40}?)(?:으로|로)\s*(?:이동|간다|가자|향한다|간다|옮긴다)"
)
_DESTINATION_CLAUSE_SPLIT_RE = re.compile(r".*(?:라서|이라서|라|이라|때문에|니까|이니|라며)\s*")
_EXIT_LOCATION_RE = re.compile(r"(?:나온|나오|걸어\s*나온|걸어\s*나오|돌아온|돌아오|복귀)")
_EXIT_LOCATION_NEG_RE = re.compile(r"(?:나오|돌아오|복귀)\s*지\s*(?:못|않|않았|못했)")
# OOC 텍스트에 실제 이동 의도 단서가 있는지 substring으로 확인하는 키워드 목록.
# LLM이 단서 없이 location_id를 환각해도 위치 이동을 적용하지 않도록 막는 데 쓴다.
_MOVEMENT_CUES = (
    "이동", "이사", "간다", "갔", "가자", "가서", "향한", "향했", "향하",
    "들어가", "들어와", "들어섰", "나간", "나가", "나와", "나온", "나오",
    "돌아간", "돌아온", "돌아와", "복귀", "도착", "올라가", "내려가",
    "따라가", "따라와", "옮긴", "옮겨", "옮겼",
    "move", "enter", "leave", "arrive", "follow", "head to", "go to", "walk to",
)


def _has_movement_cue(text: str) -> bool:
    """OOC 텍스트에 이동 의도 단서가 있으면 True. 영어 키워드는 소문자로 비교한다."""
    lowered = text.lower()
    return any(cue in lowered for cue in _MOVEMENT_CUES)

_SYSTEM_PROMPT = (
    Path(__file__).resolve().parent / "prompts" / "ooc" / "system.md"
).read_text(encoding="utf-8")


def _compact_prompt_text(text: object, limit: int) -> str:
    """프롬프트 주입용 텍스트를 공백 정리 후 길이 제한합니다."""
    value = re.sub(r"\n{3,}", "\n\n", str(text or "").strip())
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"


async def _fetch_ooc_rule_hints() -> list[str]:
    """OOC 해석에 참고할 활성 Rule 힌트를 우선순위순으로 조회합니다."""
    try:
        async with async_driver.session() as session:
            result = await session.run(
                """
                MATCH (r:Rule)
                WHERE r.status = 'active' OR r.status IS NULL
                RETURN r.name AS name, r.summary AS summary, r.prompt_hint AS prompt_hint
                ORDER BY r.prompt_priority DESC, r.id
                LIMIT 8
                """
            )
            rows = await result.fetch_all()
    except Exception as exc:
        print(f"[OOC] rule hint fetch failed (ignored): {exc}")
        return []

    hints: list[str] = []
    for row in rows:
        name = str(row.get("name") or "rule").strip()
        summary = str(row.get("summary") or "").strip()
        prompt_hint = str(row.get("prompt_hint") or "").strip()
        body = prompt_hint or summary
        if body:
            hints.append(f"- {name}: {body}")
    return hints


async def _render_ooc_world_context(world_config: dict | None) -> str:
    """월드/시나리오 프롬프트와 Rule 힌트를 OOC 파서용 컨텍스트로 렌더링합니다."""
    sections = (world_config or {}).get("prompt", {}).get("sections", {})
    parts: list[str] = []

    world_lore = _compact_prompt_text(sections.get("world"), 1800)
    if world_lore:
        parts.append("### World Lore\n" + world_lore)

    scenario_lore = _compact_prompt_text(sections.get("scenario"), 5000)
    if scenario_lore:
        parts.append("### Scenario Lore\n" + scenario_lore)

    focus_map = (world_config or {}).get("prompt", {}).get("characters", {}).get("focus", {})
    focus_text = _compact_prompt_text("\n\n".join(str(v) for v in focus_map.values() if v), 1800)
    if focus_text:
        parts.append("### Character Focus\n" + focus_text)

    rule_hints = await _fetch_ooc_rule_hints()
    if rule_hints:
        parts.append("### Active Rules\n" + "\n".join(rule_hints))

    return "\n\n".join(parts) if parts else "none"


def _render_schedule_context_for_ooc(schedule_context: dict) -> str:
    """스케줄 컨텍스트를 OOC 시간 파서용 텍스트로 렌더링합니다."""
    schedules = schedule_context.get("schedules") or []
    routines = schedule_context.get("routine_schedules") or []
    lines: list[str] = []

    if schedules:
        lines.append("Same-day schedules:")
        for s in schedules[:6]:
            owner = s.get("owner_name") or s.get("owner_id") or "character"
            name = s.get("name") or s.get("activity") or "schedule"
            start = s.get("start_time") or "?"
            end = s.get("end_time") or "?"
            location = s.get("location_name") or s.get("location_id") or "?"
            timing = s.get("timing") or "today"
            lines.append(f"- {owner}: {name} {start}-{end} at {location} ({timing})")

    today_routines = [r for r in routines if r.get("is_today")]
    if today_routines:
        lines.append("Today routines:")
        for s in today_routines[:6]:
            owner = s.get("owner_name") or s.get("owner_id") or "character"
            name = s.get("name") or s.get("activity") or "routine"
            start = s.get("start_time") or "?"
            end = s.get("end_time") or "?"
            lines.append(f"- {owner}: {name} {start}-{end}")

    return "\n".join(lines) if lines else "none"


def is_ooc(text: str) -> bool:
    """텍스트에 단일 별표 OOC 마커가 포함되어 있는지 반환합니다."""
    stripped = _BOLD_RE.sub('', text)
    return '*' in stripped


async def _get_allowed_locations() -> str:
    async with async_driver.session() as session:
        result = await session.run("""
            MATCH (l:Location)
            OPTIONAL MATCH (l)-[:PART_OF]->(p:Location)
            RETURN l.id AS id, l.name AS name, l.tags AS tags, p.id AS parent_id
            ORDER BY l.prompt_priority DESC, l.id
        """)
        records = await result.data()
        locations = []
        for rec in records:
            tags = ", ".join(rec.get("tags") or [])
            parent = f", parent={rec['parent_id']}" if rec.get("parent_id") else ""
            tag_text = f", tags=[{tags}]" if tags else ""
            locations.append(f'- "{rec["id"]}" ({rec["name"]}{parent}{tag_text})')
        return "\n".join(locations) if locations else "- No registered locations."


async def _get_all_characters() -> list[dict]:
    """DB의 모든 캐릭터 이름·aliases·ID를 조회합니다."""
    async with async_driver.session() as session:
        result = await session.run(
            "MATCH (c:Character) RETURN c.id AS id, c.name AS name, c.aliases AS aliases"
        )
        rows = await result.fetch_all()
    return [
        {
            "id": str(row["id"]),
            "name": row["name"] or str(row["id"]),
            "aliases": list(row["aliases"] or []),
        }
        for row in rows
        if row["id"]
    ]


def _coerce_delta_minutes(value: object) -> int:
    """OOC time_delta_minutes 출력을 안전한 분 단위 정수로 변환한다."""
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return 0
    return minutes if 0 <= minutes < 10080 else 0


def _parse_current_time(raw: object) -> datetime:
    """GlobalState.currentTime 값을 datetime으로 안전하게 정규화합니다."""
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return datetime.now()


def _augment_time_plan_from_text(text: str, plan: dict, current_time: datetime) -> dict:
    """LLM이 흔한 시간 표현을 누락했을 때 deterministic rule로 보완합니다."""
    plan = dict(plan)

    if not plan.get("new_datetime"):
        m = _DATE_KOR_RE.search(text)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour   = int(m.group(4)) if m.group(4) else current_time.hour
            minute = int(m.group(5)) if m.group(5) else (current_time.minute if m.group(4) else 0)
            try:
                plan["new_datetime"] = datetime(year, month, day, hour, minute, 0).isoformat()
            except ValueError:
                pass

    if not plan.get("new_datetime"):
        if not plan.get("time_delta_minutes") and _THREE_HOURS_LATER_RE.search(text):
            plan["time_delta_minutes"] = 180
        if not plan.get("time_set") and _NEXT_MORNING_RE.search(text):
            plan["time_delta_minutes"] = max(_coerce_delta_minutes(plan.get("time_delta_minutes")), 1440)
            plan["time_set"] = "08:00"

    return plan


async def _location_exists(location_id: str) -> bool:
    """OOC가 반환한 location_id가 실제 Location인지 확인한다."""
    async with async_driver.session() as session:
        result = await session.run(
            "MATCH (l:Location {id: $loc_id}) RETURN l.id AS id",
            loc_id=location_id,
        )
        return await result.single() is not None


def _extract_destination_from_text(text: str) -> str | None:
    """OOC 이동 문장에서 목적지 표현을 일반적으로 추출합니다."""
    cleaned = re.sub(r"[*\n\r]+", " ", text).strip()
    match = _DESTINATION_MOVE_RE.search(cleaned)
    if not match:
        return None
    destination = _DESTINATION_CLAUSE_SPLIT_RE.sub("", match.group("dest")).strip()
    destination = re.sub(r"^\d+\s*교시가?\s*", "", destination).strip()
    destination = re.sub(r"^(다\s*같이|모두|전원|네\s*명(?:이서)?|4\s*명(?:이서)?|일행(?:이)?|친구들(?:이)?|애들(?:이)?)\s+", "", destination).strip()
    return destination or None


def _normalize_location_text(value: str) -> str:
    """Location 매칭용으로 비문자 및 공백을 제거합니다."""
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value).lower())


def _resolve_char_id(char_name: str, name_to_id: dict[str, str]) -> str | None:
    """LLM이 반환한 캐릭터 이름을 DB ID로 변환합니다.

    1단계: 정확 일치
    2단계: 언더스코어·공백 제거 후 정확 일치 ("이_지수" → "이지수")
    3단계: 분리 파트를 name/alias와 비교 ("한_유람" → "유람" alias 매칭)
    """
    if char_name in name_to_id:
        return name_to_id[char_name]

    stripped = re.sub(r"[\s_\-]+", "", char_name)
    if stripped in name_to_id:
        return name_to_id[stripped]

    for part in re.split(r"[\s_\-]+", char_name):
        if len(part) >= 2 and part in name_to_id:
            return name_to_id[part]

    return None


def _score_location_match(row: dict, destination: str | None, context: dict) -> int:
    """Location 후보와 목적지 표현의 적합도를 점수화합니다."""
    loc_id = str(row["id"])
    loc_name = str(row.get("name") or "")
    tags = {str(tag).lower() for tag in (row.get("tags") or [])}
    parent_id = str(row.get("parent_id") or "")
    destination_norm = _normalize_location_text(destination or "")
    if not destination_norm:
        return 0

    id_norm = _normalize_location_text(loc_id)
    name_norm = _normalize_location_text(loc_name)
    tag_norms = {_normalize_location_text(tag) for tag in tags}
    candidates = {id_norm, name_norm, *tag_norms}
    candidates = {c for c in candidates if c}
    if not candidates:
        return 0

    score = 0
    if destination_norm in {id_norm, name_norm}:
        score = 100
    elif destination_norm in tag_norms:
        score = 80
    elif len(destination_norm) >= 2 and destination_norm in name_norm:
        score = 75
    elif len(destination_norm) >= 2 and any(destination_norm in c or c in destination_norm for c in candidates):
        score = 55
    if score <= 0:
        return 0

    current_id = context.get("current_id") or ""
    ctx_parent_id = context.get("parent_id") or ""
    ancestor_ids = context.get("ancestor_ids") or set()

    if loc_id == current_id:
        score += 60
    elif parent_id == current_id:
        score += 60  # 현재 위치의 직계 자식
    elif ctx_parent_id and parent_id == ctx_parent_id:
        score += 50  # 현재 위치와 형제 (같은 부모)
    elif loc_id in ancestor_ids or parent_id in ancestor_ids:
        score += 30  # 조상 체인과 연결

    score += min(len(name_norm), 30)
    try:
        score += int(row.get("prompt_priority") or 0)
    except (TypeError, ValueError):
        pass
    return score


def _find_context_duplicate(destination: str, rows: list[dict], context: dict) -> str | None:
    """신규 노드 생성 전, 동일 맥락 내 유사 이름의 Location을 찾습니다."""
    dest_norm = _normalize_location_text(destination)
    if not dest_norm or len(dest_norm) < 2:
        return None
    ancestor_ids = context.get("ancestor_ids") or set()
    if not ancestor_ids:
        return None
    best_id: str | None = None
    best_len = 0
    for row in rows:
        row_parent_id = str(row.get("parent_id") or "")
        loc_id = str(row.get("id") or "")
        name_norm = _normalize_location_text(str(row.get("name") or ""))
        if not name_norm:
            continue
        if row_parent_id not in ancestor_ids and loc_id not in ancestor_ids:
            continue
        if dest_norm in name_norm or name_norm in dest_norm:
            if len(name_norm) > best_len:
                best_len = len(name_norm)
                best_id = loc_id
    return best_id


async def _fetch_location_context() -> dict:
    """현재 위치 + 조상 체인을 반환합니다. current_id, parent_id, ancestor_ids, chain 포함."""
    ctx: dict = {"current_id": None, "parent_id": None, "ancestor_ids": set(), "chain": []}
    async with async_driver.session() as session:
        loc_rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentLocationId AS location_id"
        )
        loc_row = await loc_rec.single()
        current_id = loc_row.get("location_id") if loc_row else None
        if not current_id:
            return ctx
        ctx["current_id"] = str(current_id)
        ctx["ancestor_ids"].add(str(current_id))
        loc_id = str(current_id)
        first = True
        for _ in range(5):
            rec = await session.run(
                """
                MATCH (l:Location {id: $id})
                OPTIONAL MATCH (l)-[:PART_OF]->(p:Location)
                RETURN l.id AS id, l.name AS name, p.id AS parent_id
                """,
                id=loc_id,
            )
            row = await rec.single()
            if not row:
                break
            ctx["chain"].append({"id": str(row["id"]), "name": str(row["name"] or row["id"])})
            parent = row.get("parent_id")
            if first and parent:
                ctx["parent_id"] = str(parent)
                first = False
            if parent:
                ctx["ancestor_ids"].add(str(parent))
                loc_id = str(parent)
            else:
                break
    return ctx


async def _resolve_location_from_ooc_text(text: str, plan: dict, context: dict) -> tuple[str | None, dict]:
    """OOC 위치 계획을 기존 Location 또는 새 Location 메타데이터로 정규화합니다."""
    raw_location_id = plan.get("location_id")
    raw_location_id = raw_location_id.strip() if isinstance(raw_location_id, str) else None
    loc_meta = plan.get("new_location") if isinstance(plan.get("new_location"), dict) else {}
    destination = (
        str(loc_meta.get("name")).strip()
        if loc_meta.get("name")
        else _extract_destination_from_text(text)
    )

    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (l:Location)
            OPTIONAL MATCH (l)-[:PART_OF]->(p:Location)
            RETURN l.id AS id, l.name AS name, l.tags AS tags,
                   l.prompt_priority AS prompt_priority, p.id AS parent_id
            """
        )
        rows = [dict(row) for row in await result.fetch_all()]

    if raw_location_id:
        for row in rows:
            if str(row["id"]) == raw_location_id:
                return raw_location_id, {}

    if not destination:
        return raw_location_id, loc_meta or {}

    scored = [(_score_location_match(row, destination, context), row) for row in rows]
    scored = [(score, row) for score, row in scored if score > 0]
    if scored:
        scored.sort(
            key=lambda item: (
                item[0],
                int(item[1].get("prompt_priority") or 0),
                len(_normalize_location_text(str(item[1].get("name") or ""))),
            ),
            reverse=True,
        )
        return str(scored[0][1]["id"]), {}

    # 스코어링 실패 시 맥락 내 중복 체크 (신규 노드 생성 방지)
    dup_id = _find_context_duplicate(destination, rows, context)
    if dup_id:
        return dup_id, {}

    default_meta = loc_meta or {
        "name": destination,
        "aliases": [destination],
        "description": f"{destination} location introduced by OOC movement.",
        "prompt_hint": f"Current scene location: {destination}.",
        "tags": ["dynamic"],
        "prompt_priority": 8,
    }

    if raw_location_id:
        return raw_location_id, default_meta

    return destination, default_meta


async def _fetch_dynamic_state_values(char_id: str, fields: list[str]) -> dict[str, object]:
    """OOC 적용 전 캐릭터 DynamicState 필드값을 반환합니다."""
    allowed = {
        "mood",
        "mental_condition",
        "stress_level",
        "physical_condition",
        "injury_detail",
        "emotional_state",
    }
    selected = [field for field in fields if field in allowed]
    if not selected:
        return {}
    projection = ", ".join(f"d.{field} AS {field}" for field in selected)
    async with async_driver.session() as session:
        result = await session.run(
            f"""
            MATCH (c:Character {{id: $char_id}})-[:HAS_STATE]->(d:DynamicState)
            RETURN {projection}
            """,
            char_id=char_id,
        )
        row = await result.single()
    return {field: row.get(field) for field in selected} if row else {}


async def _fetch_character_location_id(char_id: str) -> str:
    """캐릭터의 현재 위치 ID를 LOCATED_AT 우선으로 반환합니다."""
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (c:Character {id: $char_id})
            OPTIONAL MATCH (c)-[:LOCATED_AT]->(l:Location)
            OPTIONAL MATCH (c)-[:HAS_STATE]->(d:DynamicState)
            RETURN l.id AS location_id, d.location_id AS state_location_id
            """,
            char_id=char_id,
        )
        row = await result.single()
    if not row:
        return ""
    return str(row.get("location_id") or row.get("state_location_id") or "")


async def _get_character_location_parent(char_id: str) -> str | None:
    """캐릭터의 현재 Location이 하위 장소면 부모 Location ID를 반환합니다."""
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (c:Character {id: $char_id})-[:LOCATED_AT]->(l:Location)-[:PART_OF]->(p:Location)
            RETURN p.id AS parent_id
            """,
            char_id=char_id,
        )
        row = await result.single()
        if row and row.get("parent_id"):
            return str(row["parent_id"])

        fallback = await session.run(
            """
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            MATCH (l:Location {id: d.location_id})-[:PART_OF]->(p:Location)
            RETURN p.id AS parent_id
            """,
            char_id=char_id,
        )
        fallback_row = await fallback.single()
    return str(fallback_row["parent_id"]) if fallback_row and fallback_row.get("parent_id") else None


async def _apply_time_change(
    delta_minutes: object,
    time_set: object,
    new_datetime: object = None,
    current_time: datetime | None = None,
) -> dict:
    """OOC가 요청한 시간 이동을 GlobalState.currentTime에 반영한다."""
    new_dt_str  = str(new_datetime or "").strip()
    use_new_dt  = bool(_NEW_DATETIME_RE.match(new_dt_str))
    delta       = _coerce_delta_minutes(delta_minutes)
    match       = _TIME_SET_RE.match(str(time_set or ""))

    if not use_new_dt and delta <= 0 and not match:
        base_time = current_time
        if base_time is None:
            async with async_driver.session() as session:
                result = await session.run(
                    "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct"
                )
                row = await result.single()
                base_time = _parse_current_time(row["ct"] if row else None)
        return {
            "time_changed": False,
            "time_before": base_time.isoformat(),
            "time_after": base_time.isoformat(),
            "elapsed_minutes": 0.0,
            "days_passed": 0,
            "applied_time_delta_minutes": 0,
            "applied_time_set": None,
        }

    if current_time is None:
        async with async_driver.session() as session:
            result = await session.run(
                "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct"
            )
            row = await result.single()
            current_time = _parse_current_time(row["ct"] if row else None)

    if use_new_dt:
        try:
            new_time = datetime.fromisoformat(new_dt_str)
        except ValueError:
            new_time = current_time
    else:
        new_time = current_time + timedelta(minutes=delta)
        if match:
            hour   = int(match.group(1))
            minute = int(match.group(2))
            target = new_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if delta == 0 and target <= current_time:
                target += timedelta(days=1)
            new_time = target

    # KuzuDB SET + $param 버그 우회 — time_plan.py와 동일한 리터럴 삽입 방식 사용
    _safe_iso = new_time.isoformat().replace("\\", "\\\\").replace("'", "\\'")
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET gs.currentTime = '{_safe_iso}'"
        )
    elapsed_minutes = max(0.0, (new_time - current_time).total_seconds() / 60)
    return {
        "time_changed": True,
        "time_before": current_time.isoformat(),
        "time_after": new_time.isoformat(),
        "elapsed_minutes": elapsed_minutes,
        "days_passed": (new_time.date() - current_time.date()).days,
        "applied_time_delta_minutes": int(elapsed_minutes) if use_new_dt else delta,
        "applied_time_set": None if use_new_dt else (f"{match.group(1).zfill(2)}:{match.group(2)}" if match else None),
    }


async def _fetch_colocated_character_ids(npc_id: str, pc_id: str | None) -> list[str]:
    """현재 NPC/PC와 같은 장소에 있는 캐릭터 ID를 조회합니다."""
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (anchor:Character)-[:LOCATED_AT]->(l:Location)<-[:LOCATED_AT]-(c:Character)
            WHERE anchor.id = $npc_id OR anchor.id = $pc_id
            RETURN DISTINCT c.id AS id
            """,
            npc_id=npc_id,
            pc_id=pc_id or npc_id,
        )
        rows = await result.fetch_all()
        if rows:
            return sorted(str(row["id"]) for row in rows if row.get("id"))

        location_rec = await session.run(
            """
            MATCH (anchor:Character)-[:HAS_STATE]->(d:DynamicState)
            WHERE anchor.id = $npc_id OR anchor.id = $pc_id
            RETURN d.location_id AS location_id
            LIMIT 1
            """,
            npc_id=npc_id,
            pc_id=pc_id or npc_id,
        )
        location_row = await location_rec.single()
        location_id = location_row.get("location_id") if location_row else None
        if not location_id:
            return []

        state_result = await session.run(
            """
            MATCH (c:Character)-[:HAS_STATE]->(d:DynamicState)
            WHERE d.location_id = $location_id
            RETURN c.id AS id
            """,
            location_id=location_id,
        )
        state_rows = await state_result.fetch_all()
    return sorted(str(row["id"]) for row in state_rows if row.get("id"))


async def _resolve_ooc_move_targets(
    text: str,
    characters: list[dict],
    npc_id: str,
    pc_id: str | None,
) -> list[str]:
    """OOC 위치 변경이 적용될 캐릭터 ID 목록을 결정합니다."""
    primary_targets = {npc_id}
    if pc_id:
        primary_targets.add(pc_id)

    targets = set()
    if pc_id and pc_id in text:
        targets.add(pc_id)

    for char in characters:
        char_id = str(char["id"])
        names = {char_id, str(char.get("name") or "")}
        names.update(str(alias) for alias in (char.get("aliases") or []))
        if any(name and name in text for name in names):
            targets.add(char_id)

    if _GROUP_MOVE_RE.search(text):
        # Group wording applies to the active scene group, not every character
        # sharing the same stored Location. Fresh worlds can colocate many NPCs.
        targets.update(primary_targets)

    if targets:
        return sorted(targets)

    return sorted(primary_targets)


async def _infer_exit_location_from_ooc_text(
    text: str,
    characters: list[dict],
    npc_id: str,
    pc_id: str | None,
) -> tuple[str | None, list[str]]:
    """목적지 없이 하위 장소에서 '나온다/돌아온다'는 OOC를 부모 장소 이동으로 해석합니다."""
    if not _EXIT_LOCATION_RE.search(text):
        return None, []
    if _EXIT_LOCATION_NEG_RE.search(text):
        return None, []

    targets = await _resolve_ooc_move_targets(text, characters, npc_id, pc_id)
    if not targets:
        return None, []

    parent_by_target: dict[str, str] = {}
    for char_id in targets:
        parent_id = await _get_character_location_parent(char_id)
        if parent_id:
            parent_by_target[char_id] = parent_id

    unique_parents = set(parent_by_target.values())
    if len(unique_parents) != 1:
        return None, []

    return next(iter(unique_parents)), sorted(parent_by_target)


async def _set_global_location(location_id: str) -> None:
    """GlobalState.currentLocationId를 OOC 위치 변경과 동기화합니다."""
    safe_location_id = location_id.replace("\\", "\\\\").replace("'", "\\'")
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET gs.currentLocationId = '{safe_location_id}'"
        )


async def parse_ooc(
    text: str,
    npc_id: str,
    npc_name: str,
    pc_id: str | None = None,
    world_config: dict | None = None,
) -> dict:
    """OOC 텍스트를 분석하고 DB에 즉각 반영합니다 (비동기)"""

    # 현재 인게임 시간
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct"
        )
        row = await rec.single()
        current_time = _parse_current_time(row["ct"] if row else None)

    try:
        schedule_context = await fetch_schedule_context(
            current_time=current_time,
            window_minutes=SCHEDULE_TIME_PARSE_WINDOW_MIN,
        )
    except Exception as e:
        print(f"[OOC] schedule context fetch failed (ignored): {e}")
        schedule_context = {}

    locations = await _get_allowed_locations()
    characters = await _get_all_characters()
    location_context = await _fetch_location_context()
    world_context_block = await _render_ooc_world_context(world_config)

    # 프롬프트용 캐릭터 목록 문자열
    char_lines = []
    for c in characters:
        line = f'- id="{c["id"]}" name="{c["name"]}"'
        if c["aliases"]:
            line += f' (aliases: {", ".join(c["aliases"])})'
        char_lines.append(line)
    characters_str = "\n".join(char_lines) if char_lines else "- 등록된 캐릭터 없음"

    # 현재 위치 체인 문자열 (innermost → outermost)
    chain = location_context.get("chain") or []
    current_location_chain = (
        " → ".join(f'{c["name"]} ({c["id"]})' for c in chain)
        if chain else "알 수 없음"
    )

    # name/alias/id → id 역매핑
    name_to_id: dict[str, str] = {}
    for c in characters:
        name_to_id[c["id"]] = c["id"]
        name_to_id[c["name"]] = c["id"]
        for alias in c["aliases"]:
            name_to_id[alias] = c["id"]

    schedule_block = _render_schedule_context_for_ooc(schedule_context)
    system_prompt = (_SYSTEM_PROMPT
        .replace("{locations_str}", locations)
        .replace("{characters_str}", characters_str)
        .replace("{current_time}", current_time.isoformat())
        .replace("{current_location_chain}", current_location_chain)
        .replace("{schedule_block}", schedule_block)
        .replace("{world_context_block}", world_context_block))

    model = get_model(model_name=OOC_MODEL, system_prompt=system_prompt)

    response = await model.generate_content_async(
        text,
        generation_config={
            "max_output_tokens": 4096,
            "temperature": 0.0,
            "thinking_config": {"thinking_budget": 0},
            "response_mime_type": "application/json",
            "log_source": "ooc_parser",
        },
    )

    plan = extract_json_from_llm(get_response_text(response), source="ooc_parser")
    if not plan:
        plan = {"state_changes": {}, "summary": "parse failed"}
    plan = _augment_time_plan_from_text(text, plan, current_time)

    # state_changes: {char_name: {fields}} → 각 캐릭터 DB 갱신
    raw_state_changes = plan.get("state_changes") or {}
    if not isinstance(raw_state_changes, dict):
        raw_state_changes = {}

    applied_state_changes: dict[str, dict] = {}
    state_change_diffs: dict[str, dict[str, dict[str, object]]] = {}
    for char_name, char_state in raw_state_changes.items():
        if not isinstance(char_state, dict) or not char_state:
            continue
        char_id = _resolve_char_id(char_name, name_to_id)
        if not char_id:
            print(f"[OOC] 캐릭터 '{char_name}' ID 미매칭 — 건너뜀")
            continue
        before_state = await _fetch_dynamic_state_values(char_id, list(char_state.keys()))
        await update_dynamic_state(char_id, char_state)
        applied_state_changes[char_name] = char_state
        state_change_diffs[char_id] = {
            field: {
                "before": before_state.get(field),
                "after": value,
            }
            for field, value in char_state.items()
        }

    inferred_location, inferred_location_meta = await _resolve_location_from_ooc_text(text, plan, location_context)
    inferred_move_targets: list[str] | None = None
    if not inferred_location:
        inferred_location, inferred_move_targets = await _infer_exit_location_from_ooc_text(
            text,
            characters,
            npc_id,
            pc_id,
        )
    # 이동 단서가 전혀 없는 OOC에서는 위치 변경을 적용하지 않는다.
    # 목적지 추출·퇴장 추론 경로(inferred_move_targets 설정)는 이미 이동 동사를 요구하므로,
    # 이 가드는 LLM이 plan.location_id를 단서 없이 환각해 캐릭터를 멋대로 이동시키던 문제를 막는다.
    if inferred_location and inferred_move_targets is None and not _has_movement_cue(text):
        inferred_location = None
        inferred_location_meta = {}
    new_location = inferred_location
    if new_location and not await _location_exists(new_location):
        loc_meta = plan.get("new_location") if isinstance(plan.get("new_location"), dict) else {}
        if not loc_meta:
            loc_meta = inferred_location_meta
        if loc_meta.get("name"):
            new_location = await ensure_location(
                location_id=new_location,
                name=str(loc_meta.get("name") or new_location),
                description=str(loc_meta.get("description") or ""),
                prompt_hint=str(loc_meta.get("prompt_hint") or loc_meta.get("description") or ""),
                parent_location_id=loc_meta.get("parent_location_id"),
                tags=list(loc_meta.get("tags") or ["dynamic"]),
                prompt_priority=loc_meta.get("prompt_priority") or 8,
            )
        else:
            new_location = None
    if new_location and await _location_exists(new_location):
        moved_character_ids = inferred_move_targets or await _resolve_ooc_move_targets(text, characters, npc_id, pc_id)
        location_changes = {
            char_id: {
                "before": await _fetch_character_location_id(char_id),
                "after": new_location,
            }
            for char_id in moved_character_ids
        }
        for char_id in moved_character_ids:
            await move_location(char_id, new_location)
        await _set_global_location(new_location)
    else:
        new_location = None
        moved_character_ids = []
        location_changes = {}

    time_result = await _apply_time_change(
        plan.get("time_delta_minutes"),
        plan.get("time_set"),
        plan.get("new_datetime"),
        current_time=current_time,
    )
    time_changed = time_result["time_changed"]

    summary = plan.get("summary", "상태 변경 없음")

    if applied_state_changes or new_location or time_changed:
        print(f"[OOC / {OOC_MODEL}] {summary}")

    return {
        "state_changes": applied_state_changes,
        "state_change_diffs": state_change_diffs,
        "location_id": new_location,
        "location_changes": location_changes,
        "moved_character_ids": moved_character_ids,
        "time_changed": time_changed,
        "time_before": time_result["time_before"],
        "time_after": time_result["time_after"],
        "elapsed_minutes": time_result["elapsed_minutes"],
        "days_passed": time_result["days_passed"],
        "applied_time_delta_minutes": time_result["applied_time_delta_minutes"],
        "applied_time_set": time_result["applied_time_set"],
        "summary": summary,
    }
