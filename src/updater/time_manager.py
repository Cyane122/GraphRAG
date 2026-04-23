import os
from datetime import datetime, timedelta

# 공통 유틸리티 Import
from src.utils.llm_utils import llm_client, extract_json_from_llm
from src.utils.db_utils import async_driver, move_location, advance_cycle_day

TIME_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")


async def _get_current_global_state(fallback_dt: str) -> dict:
    """DB에서 현재 전역 상태를 동기적으로 가져옵니다."""
    async with async_driver.session() as session:
        result = await session.run("""
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.currentTime AS currentTime, gs.weather AS weather, gs.currentLocationId AS currentLocationId
        """).single()
        record = await result.single()

        if result and result.get("currentTime"):
            return dict(result)

        return dict(record) if record else {
            "currentTime": fallback_dt,
            "weather": "Clear",
            "currentLocationId": "babe_villa_205"
        }

async def _get_allowed_locations() -> str:
    """DB에서 현재 등록된 Location 노드들을 가져와 문자열로 포맷팅합니다."""
    async with async_driver.session() as session:
        result = await session.run("MATCH (l:Location) RETURN l.id AS id, l.name AS name")
        records = await result.data()
        locations = [f'- "{rec["id"]}" ({rec["name"]})' for rec in records]
        return "\n".join(locations) if locations else "- No registered locations."


async def calculate_and_update_time(user_input: str, previous_context: str, pc_id: str, npc_id: str, fallback_dt: datetime) -> dict:
    """
    유저 입력과 이전 문맥을 분석하여 흐른 시간을 계산하고 DB를 업데이트합니다.
    """
    current_state = await _get_current_global_state(fallback_dt=fallback_dt.isoformat())
    current_time_iso = current_state["currentTime"]
    current_time_obj = datetime.fromisoformat(current_time_iso)

    # DB에서 동적으로 위치 정보 확보
    allowed_locations_str = await _get_allowed_locations()

    context_snippet = previous_context[-1000:] if previous_context else ""

    prompt = f"""
You are a highly deterministic Time & Environment state parser.
Your SOLE function is to analyze the user input and output a single, clean JSON object based on the rules.
DO NOT add any explanation, markdown, or commentary.

[Current World State]
- Current Time: {current_time_obj.strftime("%Y-%m-%d %H:%M")}
- Current Weather: {current_state['weather']}
- Current Location ID: {current_state['currentLocationId']}

[Allowed Locations]
{allowed_locations_str}

[Input to Analyze]
Context: "{context_snippet}"
User Input: "{user_input}"

[Step-by-Step Logic]
1.  **Determine `action_type`**: Classify the user input into EXACTLY ONE of the following types.
- "dialogue": Simple conversation. No significant physical action or movement.
- "action": A distinct physical action is described (e.g., showering, cooking, getting dressed).
- "movement": The user explicitly states intent to move to a new location or arrives at one.
- "ooc_jump": An OOC command like `(다음 날)` or `(저녁이 되었다)` is used to explicitly jump time.

2.  **Calculate Time Elapsed**:
- If `action_type` is "dialogue", set `elapsed_minutes` to `3`.
- If `action_type` is "action", set `elapsed_minutes` to `10`.
- If `action_type` is "movement", set `elapsed_minutes` to `25`.
- If `action_type` is "ooc_jump", leave `elapsed_minutes` as `null`.

3.  **Determine `target_hour` (for `ooc_jump` ONLY)**:
- If `action_type` is NOT "ooc_jump", set `target_hour` to `null`.
- If `action_type` is "ooc_jump", extract the hour from the OOC command using this map:
  - "새벽": 3, "아침": 8, "점심": 12, "오후": 15, "저녁": 19, "밤": 23

4.  **Determine `new_weather`**:
- Change weather ONLY IF explicitly requested via OOC (e.g., `(비가 내리기 시작했다)`) OR if an `ooc_jump` spans more than 12 hours.
- Choose the closest match from this list: "Clear", "Cloudy", "Foggy", "Drizzle", "Rain", "Heavy Rain", "Thunderstorm", "Snow", "Heavy Snow", "Windy".
- If no change, set to `null`.

5.  **Determine `new_location_id`**:
- Change location ONLY IF `action_type` is "movement".
- Check if the destination matches any ID in the [Allowed Locations] list.
- IF the location exists in the list, set `new_location_id` to that ID.
- IF the location DOES NOT exist in the list (unregistered place), MUST set `new_location_id` to `null`. (Another system handles creation of new locations).
- If no movement, set to `null`.

[Examples]
- User Input: "저녁 뭐 먹을까?"
-> {{"action_type": "dialogue", "target_hour": null, "elapsed_minutes": 3, "new_weather": null, "new_location_id": null, "reason": "Simple dialogue."}}
- User Input: "*밤이 깊었다. 밖에는 천둥번개가 친다.*"
-> {{"action_type": "ooc_jump", "target_hour": 23, "elapsed_minutes": null, "new_weather": "Thunderstorm", "new_location_id": null, "reason": "OOC jump with severe weather change."}}
- User Input: "이제 학교 헬스장 가야겠다." (Assuming 'babe_univ_gym' is in Allowed Locations)
-> {{"action_type": "movement", "target_hour": null, "elapsed_minutes": 25, "new_weather": null, "new_location_id": "babe_univ_gym", "reason": "Moving to a registered location."}}
- User Input: "우리 처음 보는 카페로 가자." (Assuming 'new_cafe' is NOT in Allowed Locations)
-> {{"action_type": "movement", "target_hour": null, "elapsed_minutes": 25, "new_weather": null, "new_location_id": null, "reason": "Moving to an unregistered location. Left null for complex updater."}}

[Required JSON Format]
Output ONLY the JSON object below.
{{
"action_type": "dialogue" | "action" | "movement" | "ooc_jump",
"target_hour": int or null,
"elapsed_minutes": int or null,
"new_weather": "string from weather list or null",
"new_location_id": "string (existing ID only) or null",
"reason": "Very short internal logic."
}}
"""
    # API 호출
    response = llm_client.messages.create(
        model=TIME_MODEL,
        max_tokens=256,
        temperature=0,
        messages=[{"role": "user", "content": prompt}]
    )
    plan = extract_json_from_llm(response.content[0].text)
    if not plan:
        plan = {"action_type": "dialogue", "elapsed_minutes": 5}

    await _apply_time_jump(plan, current_time_obj, [pc_id, npc_id])
    return plan



async def _apply_time_jump(plan: dict, base_time: datetime, char_ids: list[str]):
    """계산된 플랜을 바탕으로 시간을 조작하고, GlobalState와 캐릭터 노드를 모두 DB에 반영합니다."""
    action_type = plan.get("action_type", "dialogue")

    if action_type == "ooc_jump" and plan.get("target_hour") is not None:
        target_hour = int(plan["target_hour"])
        days_to_add = 1 if target_hour <= base_time.hour else 0
        new_time = base_time.replace(hour=target_hour, minute=0) + timedelta(days=days_to_add)
    else:
        minutes = plan.get("elapsed_minutes", 3)
        if not isinstance(minutes, int) or not (0 < minutes < 120):
            minutes = 3
        new_time = base_time + timedelta(minutes=minutes)

    # ── GlobalState 업데이트 파라미터 준비 ──
    update_fields = ["gs.currentTime = $new_time"]
    params = {"new_time": new_time.isoformat()}

    if plan.get("new_weather") and plan["new_weather"] != "null":
        update_fields.append("gs.weather = $weather")
        params["weather"] = plan["new_weather"]

    new_loc_id = plan.get("new_location_id")
    if new_loc_id and new_loc_id != "null":
        update_fields.append("gs.currentLocationId = $loc_id")
        params["loc_id"] = new_loc_id

    # ── 일(Day) 수 변화 계산 ──
    days_passed = (new_time.date() - base_time.date()).days

    # ── DB 트랜잭션 실행 ──
    try:
        async with async_driver.session() as session:
            # 1. GlobalState 갱신
            await session.run(f"""
                MATCH (gs:GlobalState {{id: 'singleton'}})
                SET {", ".join(update_fields)}
            """, **params)

            # 2. 날짜가 바뀌었다면 생리 주기(cycle_day) 갱신
            if days_passed > 0:
                for c_id in char_ids:
                    await advance_cycle_day(c_id, days_passed)

            # 3. 장소가 바뀌었다면 캐릭터들 위치 이동
            if new_loc_id and new_loc_id != "null":
                for c_id in char_ids:
                    await move_location(c_id, new_loc_id)

        print(f"[TimeManager] Updated: {new_time.strftime('%Y-%m-%d %H:%M')} | Plan: {plan.get('reason', 'N/A')}")
    except Exception as e:
        print(f"[TimeManager Error] DB 업데이트 실패: {e}")