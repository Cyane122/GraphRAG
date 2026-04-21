import json
import os
import re
from datetime import datetime, timedelta
import anthropic
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()
TIME_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")


class TimeManager:
    def __init__(self, driver: GraphDatabase.driver):
        self.driver = driver

    def _get_current_global_state(self) -> dict:
        """DB에서 현재 전역 상태를 동기적으로 가져옵니다."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (gs:GlobalState {id: 'singleton'})
                RETURN gs.currentTime AS currentTime, gs.weather AS weather, gs.currentLocationId AS currentLocationId
            """).single()
            return dict(result) if result else {
                "currentTime": datetime.now().isoformat(),
                "weather": "Clear",
                "currentLocationId": "babe_villa_205"
            }

    def _get_allowed_locations(self) -> str:
        """DB에서 현재 등록된 Location 노드들을 가져와 문자열로 포맷팅합니다."""
        with self.driver.session() as session:
            records = session.run("MATCH (l:Location) RETURN l.id AS id, l.name AS name")
            locations = [f'- "{rec["id"]}" ({rec["name"]})' for rec in records]
            return "\n".join(locations) if locations else "- No registered locations."

    def _parse_llm_json(self, raw_text: str) -> dict:
        """Haiku의 응답에서 JSON만 안전하게 추출합니다."""
        try:
            clean = re.sub(r"```json|```", "", raw_text).strip()
            start = clean.find('{')
            end = clean.rfind('}')
            if start != -1 and end != -1:
                clean = clean[start:end + 1]
            return json.loads(clean)
        except json.JSONDecodeError as e:
            print(f"[TimeManager Error] JSON 파싱 실패: {e}\nRaw: {raw_text[:100]}")
            return {}

    def calculate_and_update_time(self, user_input: str, previous_context: str, pc_id: str, npc_id: str):
        """
        유저 입력과 이전 문맥을 분석하여 흐른 시간을 계산하고 DB를 업데이트합니다.
        """
        current_state = self._get_current_global_state()
        current_time_iso = current_state["currentTime"]
        current_time_obj = datetime.fromisoformat(current_time_iso)

        # DB에서 동적으로 위치 정보 확보
        allowed_locations_str = self._get_allowed_locations()

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
        try:
            response = client.messages.create(
                model=TIME_MODEL,
                max_tokens=256,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            raw_text = response.content[0].text
            plan = self._parse_llm_json(raw_text)
        except Exception as e:
            print(f"[TimeManager Error] API 호출 실패: {e}")
            plan = {}

        if not plan:
            plan = {"action_type": "dialogue", "elapsed_minutes": 5}

        self._apply_time_jump(plan, current_time_obj, [pc_id, npc_id])
        return plan

    def _apply_time_jump(self, plan: dict, base_time: datetime, char_ids: list[str]):
        """계산된 플랜을 바탕으로 시간을 조작하고, GlobalState와 캐릭터 노드를 모두 DB에 반영합니다."""
        new_time = base_time

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
            with self.driver.session() as session:
                # 1. GlobalState 갱신
                session.run(f"""
                    MATCH (gs:GlobalState {{id: 'singleton'}})
                    SET {", ".join(update_fields)}
                """, **params)

                # 2. 날짜가 바뀌었다면 생리 주기(cycle_day) 갱신
                if days_passed > 0:
                    for c_id in char_ids:
                        self._advance_cycle_day(session, c_id, days_passed)

                # 3. 장소가 바뀌었다면 캐릭터들 위치 이동
                if new_loc_id and new_loc_id != "null":
                    for c_id in char_ids:
                        self._move_location(session, c_id, new_loc_id)

            print(f"[TimeManager] Updated: {new_time.strftime('%Y-%m-%d %H:%M')} | Plan: {plan.get('reason', 'N/A')}")
        except Exception as e:
            print(f"[TimeManager Error] DB 업데이트 실패: {e}")

    # ── 이관된 DB 헬퍼 메서드들 ──
    def _advance_cycle_day(self, session, char_id: str, days: int) -> None:
        """여성 캐릭터 등의 생리/상태 주기 일수를 계산합니다."""
        session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            // null 체크 (PC 등 주기가 없는 캐릭터 방어)
            WHERE d.cycle_day IS NOT NULL
            SET d.cycle_day = ((d.cycle_day + $days - 1) % 28) + 1
        """, char_id=char_id, days=days)

    def _move_location(self, session, char_id: str, new_loc_id: str) -> None:
        """캐릭터의 LOCATED_AT 관계를 끊고 새로운 장소로 연결합니다."""
        # 1. 기존 장소 연결 해제
        session.run("""
            MATCH (c:Character {id: $char_id})-[old:LOCATED_AT]->(prev:Location)
            DELETE old
            SET prev.current_chars = [x IN prev.current_chars WHERE x <> $char_id]
        """, char_id=char_id)
        # 2. 새 장소 연결 및 상태 업데이트
        session.run("""
            MATCH (c:Character {id: $char_id})
            MATCH (next:Location {id: $new_loc_id})
            MERGE (c)-[:LOCATED_AT]->(next)
            SET next.current_chars = coalesce(next.current_chars, []) + [$char_id]
        """, char_id=char_id, new_loc_id=new_loc_id)
        # 3. 캐릭터의 DynamicState의 문자열 ID도 업데이트
        session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            SET d.location_id = $new_loc_id
        """, char_id=char_id, new_loc_id=new_loc_id)