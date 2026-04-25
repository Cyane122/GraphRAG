"""
세상은 움직인다 + SNS 피드 생성기.

역할:
  1. build_world_context() — 최근 NPC 자율행동 수집 + SNS 포스트 생성 → dict 반환
  2. format_world_section() — promptBuilder용 문자열 포맷팅 (static)

흐름:
  manager_agent → build_world_context() → promptBuilder.build_world_section()

SNS 포스트 기준:
  - need_name = "social" 또는 "fun" 인 자율행동 이벤트 우선 채택
  - 캐릭터 StaticProfile에 sns_handle 필드가 있어야 생성됨
  - Haiku가 행동 요약 기반 게시글 텍스트 생성 (배치 1회)

묘사 주입 형식 (Actor LLM에 전달):
  <world_context>
  [Nearby Activity]
  - 강지희: 카페에서 혼자 아메리카노를 마셨다

  [SNS Feed]
  - kang._.Ji 님이 새 게시글을 올렸습니다: '오늘 왜 이렇게 센치함...'
  </world_context>
"""

import os
import json
from datetime import datetime, timedelta

from src.utils.db_utils import async_driver
from src.utils.llm_utils import async_llm_client, extract_json_from_llm

NARRATOR_MODEL      = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")
NEARBY_WINDOW_HOURS = 2    # 근처 활동 수집 창 (게임 내 시간)
WORLD_WINDOW_HOURS  = 8    # 전체 world context 창
MAX_SNS_POSTS       = 2    # 한 턴에 최대 노출할 SNS 게시글 수
MAX_NEARBY          = 3    # 최대 근처 활동 수


# ════════════════════════════════════════════════════════════
# 퍼블릭 진입점
# ════════════════════════════════════════════════════════════

async def build_world_context(
    npc_id:       str,
    pc_id:        str,
    location_id:  str,
    current_time: datetime,
) -> dict:
    """
    manager_agent에서 위치 확정 직후 호출.

    Returns:
        {
            "nearby_activity": [{"name": str, "summary": str}],
            "sns_posts":       [str],
        }
    """
    events = await _fetch_recent_auto_events(npc_id, pc_id, current_time, WORLD_WINDOW_HOURS)
    if not events:
        return {"nearby_activity": [], "sns_posts": []}

    # ── 근처 활동 필터 ────────────────────────────────────
    cutoff_nearby = current_time - timedelta(hours=NEARBY_WINDOW_HOURS)
    nearby = [
        {"name": e["char_name"], "summary": e["summary"]}
        for e in events
        if e.get("location_id") == location_id
        and _parse_ts(e["timestamp"]) >= cutoff_nearby
    ]

    # ── SNS 후보 필터 (social/fun 욕구 해소 이벤트 + sns_handle 보유) ──
    sns_candidates = [
        e for e in events
        if e.get("need_name") in ("social", "fun")
        and e.get("sns_handle")
    ]

    sns_posts: list[str] = []
    if sns_candidates:
        sns_posts = await _generate_sns_batch(sns_candidates[:MAX_SNS_POSTS])

    print(
        f"[WorldNarrator] nearby={len(nearby[:MAX_NEARBY])} "
        f"sns={len(sns_posts)}"
    )

    return {
        "nearby_activity": nearby[:MAX_NEARBY],
        "sns_posts":       sns_posts,
    }


# ════════════════════════════════════════════════════════════
# DB 조회
# ════════════════════════════════════════════════════════════

async def _fetch_recent_auto_events(
    npc_id:       str,
    pc_id:        str,
    current_time: datetime,
    window_hours: int,
) -> list[dict]:
    """
    최근 자율행동 Events + 캐릭터 StaticProfile 조인.
    npc_id (main NPC) 와 pc_id (플레이어) 본인 이벤트는 제외.
    """
    cutoff = (current_time - timedelta(hours=window_hours)).isoformat()

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:INVOLVED_IN]->(e:Event)
            WHERE c.id <> $npc_id
              AND c.id <> $pc_id
              AND e.impact = "autonomous need resolution"
              AND e.timestamp >= $cutoff
            OPTIONAL MATCH (c)-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN c.id          AS char_id,
                   c.name        AS char_name,
                   e.id          AS event_id,
                   e.summary     AS summary,
                   e.timestamp   AS timestamp,
                   e.location_id AS location_id,
                   e.need_name   AS need_name,
                   sp.sns_handle AS sns_handle
            ORDER BY e.timestamp DESC
            LIMIT 15
        """, npc_id=npc_id, pc_id=pc_id, cutoff=cutoff)
        rows = await rec.data()

    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════
# SNS 게시글 생성
# ════════════════════════════════════════════════════════════

async def _generate_sns_batch(candidates: list[dict]) -> list[str]:
    """
    Haiku에 SNS 게시글 생성 배치 요청 (1회 호출).

    candidates: [{event_id, char_name, sns_handle, summary, ...}, ...]
    Returns:    ["kang._.Ji 님이 새 게시글을 올렸습니다: '...'", ...]
    """
    items = [
        {
            "id":         c["event_id"],
            "char_name":  c["char_name"],
            "sns_handle": c["sns_handle"],
            "action":     c["summary"],
        }
        for c in candidates
    ]

    prompt = f"""You are generating realistic Korean SNS (Instagram-style) posts for fictional characters in a slice-of-life roleplay.

Each character just did something. Write a short post they might upload to their feed.

Rules:
- 1–2 lines max, casual Korean
- Match the character's personality if inferable from their name/action
- Natural emoji use — but no spam. Some characters may use none.
- Do NOT mention the need (hunger/social/etc.) directly
- Sound like a real person, not AI
- Varied styles: some melancholic, some cheerful, some mundane

Input:
{json.dumps(items, ensure_ascii=False, indent=2)}

Return ONLY a JSON array. Each element:
{{
  "id": "<same event_id>",
  "post_text": "<post content only — no handle, no prefix>"
}}"""

    try:
        resp = await async_llm_client.messages.create(
            model=NARRATOR_MODEL,
            max_tokens=512,
            temperature=0.85,
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = extract_json_from_llm(resp.content[0].text)
        if not isinstance(parsed, list):
            return []
    except Exception as e:
        print(f"[WorldNarrator] SNS 배치 생성 실패: {e}")
        return []

    # ── 결과 포맷팅 ───────────────────────────────────────
    id_to_handle = {c["event_id"]: c["sns_handle"] for c in candidates}
    posts: list[str] = []

    for item in parsed:
        event_id  = item.get("id", "")
        post_text = item.get("post_text", "").strip()
        handle    = id_to_handle.get(event_id)
        if handle and post_text:
            posts.append(f"{handle} 님이 새 게시글을 올렸습니다: '{post_text}'")

    return posts


# ════════════════════════════════════════════════════════════
# 내부 유틸
# ════════════════════════════════════════════════════════════

def _parse_ts(ts: str | None) -> datetime:
    """ISO 8601 또는 YYYYMMDD_HHMM → naive datetime. 파싱 실패 시 datetime.min."""
    if not ts:
        return datetime.min
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return datetime.strptime(ts, "%Y%m%d_%H%M")
    except ValueError:
        return datetime.min