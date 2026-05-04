"""
사회정서지원과 일정 자동 생성기.

인게임 시간이 18:00을 넘을 때 check_and_trigger_schedule()이
당일 일정을 생성 → Neo4j 저장 → SMS 문자열 반환.
app.py가 반환값을 Chainlit 메시지로 출력한다.

호출 시점: 매 턴 Actor 응답 처리 직후 (app.py _commit_pending 끝).
중복 방지: GlobalState.schedule_date == 인게임 날짜면 스킵.
"""

import random
import hashlib
import json
from datetime import datetime

from src.utils.db_utils import async_driver

# ════════════════════════════════════════════════════════════
# 성화시 지명 데이터
# ════════════════════════════════════════════════════════════

_DISTRICTS = {
    "화서구": [
        "솔빛로", "햇살로", "새봄길", "청아로", "은하수길",
        "하늘채로", "별빛로", "푸른솔길", "나리로",
    ],
    "중원구": [
        "번영로", "중앙대로", "대로변길", "시장길", "성화대로",
        "구름길", "오래된골목", "종로", "평화로",
    ],
    "동락구": [
        "매화길", "느티나무로", "고즈넉길", "산자락로", "돌담길",
        "황혼로", "노을길", "텃밭길", "동락중앙로",
    ],
}

# 구별 노인 비율 가중치 (동락구 > 중원구 > 화서구)
_DISTRICT_AGE_WEIGHT = {
    "화서구": {"young": 0.35, "mid": 0.45, "old": 0.15, "very_old": 0.05},
    "중원구": {"young": 0.15, "mid": 0.45, "old": 0.30, "very_old": 0.10},
    "동락구": {"young": 0.05, "mid": 0.30, "old": 0.40, "very_old": 0.25},
}

_AGE_RANGE = {
    "young":    (20, 32),
    "mid":      (33, 55),
    "old":      (56, 72),
    "very_old": (73, 88),
}

# ════════════════════════════════════════════════════════════
# 한국 남성 이름 풀
# ════════════════════════════════════════════════════════════

_LAST_NAMES = [
    "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
    "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍",
    "전", "고", "문", "양", "손", "배", "백", "허", "유", "남",
]

_FIRST_NAMES = [
    "민준", "서준", "예준", "도윤", "시우", "주원", "하준", "지호", "지후", "준서",
    "준우", "현우", "도현", "지훈", "건우", "우진", "선우", "서진", "민재", "현준",
    "재원", "승우", "승현", "정우", "지원", "재윤", "성민", "진우", "태양", "한결",
    "동현", "성준", "재현", "민호", "태민", "상현", "영호", "철수", "병철", "성철",
    "용호", "재호", "영재", "광수", "동수", "현식", "영식", "병호", "재만", "순철",
    "봉수", "만수", "영길", "정식", "한수", "덕수", "갑수", "춘식", "복동", "영달",
]

_SLOTS = ["오전 1타임", "오전 2타임", "오후 1타임", "오후 2타임"]

# ════════════════════════════════════════════════════════════
# 고객 생성 로직
# ════════════════════════════════════════════════════════════

def _pick_age(district: str) -> int:
    weights = _DISTRICT_AGE_WEIGHT[district]
    band = random.choices(
        list(weights.keys()),
        weights=list(weights.values()),
    )[0]
    lo, hi = _AGE_RANGE[band]
    return random.randint(lo, hi)


def _build_stats(age: int) -> dict:
    """나이 기반 기본 stat 생성. 대부분 낮은 범위."""
    def jitter(base: float, spread: float = 0.15) -> float:
        return round(max(0.0, min(1.0, base + random.uniform(-spread, spread))), 2)

    # penis_size 분포 — 이 서비스 이용자는 평균 이하 비율이 높음
    penis_size = random.choices(
        ["very_small", "small", "average", "good", "large", "very_large"],
        weights=[0.08, 0.22, 0.40, 0.20, 0.08, 0.02],
    )[0]

    stats = {
        "hygiene":            jitter(0.45),
        "appearance":         jitter(0.35),
        "physique":           random.choice(["average", "overweight", "lean", "frail"]),
        "age_presentation":   "average",
        "nervousness":        jitter(0.70),
        "attitude":           random.choices(
            ["polite", "awkward", "blunt", "passive", "demanding", "creepy"],
            weights=[0.30, 0.30, 0.15, 0.12, 0.08, 0.05],
        )[0],
        "social_skill":       jitter(0.40),
        "consideration":      jitter(0.50),
        "physical_condition": "healthy",
        "stamina":            jitter(0.60),
        "odor":               random.choices(
            ["none", "mild", "strong", "tobacco", "alcohol", "elderly"],
            weights=[0.15, 0.35, 0.10, 0.15, 0.10, 0.15],
        )[0],
        "emotional_state":    random.choices(
            ["calm", "anxious", "depressed", "excited", "desperate", "detached"],
            weights=[0.20, 0.30, 0.15, 0.10, 0.15, 0.10],
        )[0],
        "attachment_risk":    jitter(0.35),
        "expectation_gap":    jitter(0.20),
        "penis_size":         penis_size,
    }

    # 나이 보정
    if age >= 70:
        stats["age_presentation"]   = "very_aged"
        stats["stamina"]            = round(max(0.05, stats["stamina"] - 0.30), 2)
        stats["odor"]               = "elderly"
        stats["physical_condition"] = random.choice(["frail", "chronic_pain", "mobility_limited"])
    elif age >= 55:
        stats["age_presentation"]   = "aged"
        stats["stamina"]            = round(max(0.15, stats["stamina"] - 0.15), 2)
        if stats["odor"] == "none":
            stats["odor"] = "mild"
    elif age <= 25:
        stats["nervousness"]        = round(min(1.0, stats["nervousness"] + 0.10), 2)
        stats["age_presentation"]   = "young_for_age"

    return stats


def _make_client_id(name: str, age: int, address: str) -> str:
    raw = f"{name}_{age}_{address}"
    return "client_" + hashlib.md5(raw.encode()).hexdigest()[:10]


def _make_location_id(address: str) -> str:
    return "loc_" + hashlib.md5(address.encode()).hexdigest()[:10]


def generate_daily_schedule() -> list[dict]:
    """
    오늘 일정 생성. 2~4명, 슬롯 순서 보존.
    반환:
      [{"slot", "name", "age", "address", "district",
        "client_id", "location_id", "stats"}, ...]
    """
    n_clients = random.choices([2, 3, 4], weights=[0.10, 0.40, 0.50])[0]
    slots     = _SLOTS[:n_clients]

    entries = []
    for slot in slots:
        district = random.choice(list(_DISTRICTS.keys()))
        street   = random.choice(_DISTRICTS[district])
        number   = random.randint(1, 150)
        address  = f"경기도 성화시 {district} {street} {number}"

        last   = random.choice(_LAST_NAMES)
        first  = random.choice(_FIRST_NAMES)
        name   = last + first
        age    = _pick_age(district)
        stats  = _build_stats(age)

        entries.append({
            "slot":        slot,
            "name":        name,
            "age":         age,
            "address":     address,
            "district":    district,
            "client_id":   _make_client_id(name, age, address),
            "location_id": _make_location_id(address),
            "stats":       stats,
        })

    return entries


def format_sms(entries: list[dict]) -> str:
    lines = ["[Web발신] 사회정서지원과 내일 일정입니다."]
    for e in entries:
        lines.append(f"{e['slot']}: {e['name']}({e['age']}) {e['address']}")
    lines.append("")
    lines.append("소모품 보충 신청: 031-820-1400")
    lines.append("블랙리스트 신청: 031-820-1401")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
# Neo4j 저장
# ════════════════════════════════════════════════════════════

async def _save_to_db(entries: list[dict]) -> None:
    async with async_driver.session() as session:
        for e in entries:
            cid   = e["client_id"]
            lid   = e["location_id"]
            stats = e["stats"]

            await session.run("""
                MERGE (c:Character {id: $cid})
                ON CREATE SET c.name = $name, c.type = 'client'
                ON MATCH  SET c.name = $name
            """, cid=cid, name=e["name"])

            await session.run("""
                MERGE (sp:StaticProfile {id: $sid})
                ON CREATE SET sp.age = $age, sp.gender = 'male', sp.role = 'client'
                WITH sp
                MATCH (c:Character {id: $cid})
                MERGE (c)-[:HAS_PROFILE]->(sp)
            """, sid=f"{cid}_static", cid=cid, age=e["age"])

            await session.run("""
                MERGE (ds:DynamicState {id: $dsid})
                ON CREATE SET
                    ds.hygiene            = $hygiene,
                    ds.appearance         = $appearance,
                    ds.physique           = $physique,
                    ds.age_presentation   = $age_presentation,
                    ds.nervousness        = $nervousness,
                    ds.attitude           = $attitude,
                    ds.social_skill       = $social_skill,
                    ds.consideration      = $consideration,
                    ds.physical_condition = $physical_condition,
                    ds.stamina            = $stamina,
                    ds.odor               = $odor,
                    ds.emotional_state    = $emotional_state,
                    ds.attachment_risk    = $attachment_risk,
                    ds.expectation_gap    = $expectation_gap,
                    ds.penis_size         = $penis_size
                WITH ds
                MATCH (c:Character {id: $cid})
                MERGE (c)-[:HAS_DYNAMIC_STATE]->(ds)
            """, dsid=f"{cid}_dynamic", cid=cid, **stats)

            await session.run("""
                MERGE (l:Location {id: $lid})
                ON CREATE SET l.name = $addr, l.description = '고객 자택', l.district = $district
                WITH l
                MATCH (c:Character {id: $cid})
                MERGE (c)-[:LOCATED_AT]->(l)
            """, lid=lid, addr=e["address"], district=e["district"], cid=cid)

        # 저장용 entries (stats 포함하되 직렬화 가능하게)
        schedule_json = json.dumps(entries, ensure_ascii=False)
        await session.run("""
            MERGE (gs:GlobalState {id: 'singleton'})
            SET gs.today_schedule = $schedule,
                gs.clients_total  = $total,
                gs.clients_done   = 0,
                gs.schedule_slot  = $first_slot,
                gs.schedule_date  = $date
        """,
            schedule   = schedule_json,
            total      = len(entries),
            first_slot = entries[0]["slot"] if entries else "none",
            date       = datetime.now().strftime("%Y-%m-%d"),
        )

    print(f"[Scheduler] {len(entries)}건 일정 저장 완료.")


# ════════════════════════════════════════════════════════════
# 인게임 시간 기반 일정 트리거
# ════════════════════════════════════════════════════════════

async def check_and_trigger_schedule() -> str | None:
    """
    매 턴 종료 후 app.py에서 호출.
    GlobalState.currentTime이 18:00 이상이고
    오늘 날짜(인게임) 일정이 아직 없으면 생성 → DB 저장.

    반환값:
      str  — SMS 포맷 문자열 (app.py가 Chainlit 메시지로 출력)
      None — 트리거 조건 미충족 (아무것도 하지 않음)

    트리거 조건:
      1. GlobalState.currentTime의 시각 >= 18
      2. GlobalState.schedule_date != 오늘 인게임 날짜 (YYYY-MM-DD)
         → 같은 날 중복 발송 방지
    """
    async with async_driver.session() as session:
        result = await session.run("""
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.currentTime   AS current_time,
                   gs.schedule_date AS schedule_date
        """)
        record = await result.single()
        if not record or not record["current_time"]:
            return None

    try:
        current_dt = datetime.fromisoformat(record["current_time"])
    except ValueError:
        return None

    ingame_date = current_dt.strftime("%Y-%m-%d")

    # 이미 오늘 일정이 발송됐으면 스킵
    if record["schedule_date"] == ingame_date:
        return None

    # 18:00 미만이면 스킵
    if current_dt.hour < 18:
        return None

    # 조건 충족 → 생성
    entries  = generate_daily_schedule()
    await _save_to_db(entries)          # schedule_date도 함께 저장됨
    sms_text = format_sms(entries)
    print(f"[Schedule] 인게임 {ingame_date} 18:00 — 일정 {len(entries)}건 생성.")
    return sms_text


# ════════════════════════════════════════════════════════════
# advance_slot (타임 완료 시 app.py에서 호출)
# ════════════════════════════════════════════════════════════

async def advance_slot() -> None:
    """한 타임 완료 후 clients_done +1, schedule_slot 업데이트."""
    async with async_driver.session() as session:
        result = await session.run("""
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.clients_done    AS done,
                   gs.clients_total   AS total,
                   gs.today_schedule  AS schedule
        """)
        record = await result.single()
        if not record:
            return

        done     = (record["done"] or 0) + 1
        total    = record["total"] or 0
        schedule = json.loads(record["schedule"] or "[]")

        next_slot = schedule[done]["slot"] if done < len(schedule) else "done"

        await session.run("""
            MATCH (gs:GlobalState {id: 'singleton'})
            SET gs.clients_done  = $done,
                gs.schedule_slot = $slot
        """, done=done, slot=next_slot)

    print(f"[Scheduler] 타임 완료 {done}/{total} — 다음: {next_slot}")


async def get_current_client_id() -> str | None:
    """현재 진행 중인 고객 ID. 전체 완료 시 None."""
    async with async_driver.session() as session:
        result = await session.run("""
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.today_schedule AS schedule,
                   gs.clients_done   AS done
        """)
        record = await result.single()
        if not record or not record["schedule"]:
            return None
        schedule = json.loads(record["schedule"])
        done     = record["done"] or 0
        if done >= len(schedule):
            return None
        return schedule[done]["client_id"]


# ════════════════════════════════════════════════════════════
# CLI 테스트
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import asyncio

    async def _test():
        entries = generate_daily_schedule()
        print(format_sms(entries))
        print("\n=== stats 샘플 ===")
        for e in entries:
            print(f"{e['name']}({e['age']}, {e['district']}): "
                  f"hygiene={e['stats']['hygiene']}, "
                  f"attitude={e['stats']['attitude']}, "
                  f"odor={e['stats']['odor']}")

    asyncio.run(_test())