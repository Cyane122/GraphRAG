# ================================
# src/simulation/systems/social/context.py
#
# Build prompt-ready world context from nearby activity and SNS feeds.
#
# Functions
#   - build_world_context(npc_id: str, pc_id: str, location_id: str, current_time: datetime) -> dict : Build nearby activity and SNS feed context
# ================================
import json
from datetime import datetime, timedelta

from src.config import MODEL_STATE_UPDATER
from src.core.database import async_driver
from src.core.llm.client import get_model, extract_json_from_llm

NARRATOR_MODEL = MODEL_STATE_UPDATER
NEARBY_WINDOW_HOURS = 2
WORLD_WINDOW_HOURS = 8
MAX_SNS_POSTS = 2
MAX_NEARBY = 3

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

    cutoff_nearby = current_time - timedelta(hours=NEARBY_WINDOW_HOURS)
    nearby = [
        {"name": e["char_name"], "summary": e["summary"]}
        for e in events
        if e.get("location_id") == location_id
        and _parse_ts(e["timestamp"]) >= cutoff_nearby
    ]

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


async def _fetch_recent_auto_events(
    npc_id:       str,
    pc_id:        str,
    current_time: datetime,
    window_hours: int,
) -> list[dict]:
    """최근 자율행동 Events + 캐릭터 StaticProfile 조인. npc_id/pc_id 본인 이벤트 제외."""
    cutoff = (current_time - timedelta(hours=window_hours)).isoformat()

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:INVOLVED_IN]->(e:Event)
            WHERE c.id <> $npc_id
              AND c.id <> $pc_id
              AND e.impact IN ["autonomous need resolution", "schedule_start"]
              AND e.timestamp >= $cutoff
            OPTIONAL MATCH (c)-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN c.id          AS char_id,
                   c.name        AS char_name,
                   e.id          AS event_id,
                   e.summary     AS summary,
                   e.timestamp   AS timestamp,
                   e.location_id AS location_id,
                   e.need_name   AS need_name,
                   sp.props      AS profile_props
            ORDER BY e.timestamp DESC
            LIMIT 15
        """, npc_id=npc_id, pc_id=pc_id, cutoff=cutoff)
        rows = await rec.data()

    result = []
    for r in rows:
        row = dict(r)
        profile_props = row.pop("profile_props", None) or "{}"
        try:
            profile = json.loads(profile_props) if isinstance(profile_props, str) else (profile_props or {})
            row["sns_handle"] = profile.get("sns_handle")
        except Exception:
            row["sns_handle"] = None
        result.append(row)
    return result


async def _generate_sns_batch(candidates: list[dict]) -> list[str]:
    """Haiku에 SNS 게시글 생성 배치 요청 (1회 호출)."""
    items = [
        {
            "id":         c["event_id"],
            "char_name":  c["char_name"],
            "sns_handle": c["sns_handle"],
            "action":     c["summary"],
        }
        for c in candidates
    ]

    system_instruction = "You are generating realistic Korean SNS posts for fictional characters in a slice-of-life roleplay."

    prompt = f"""Each character just did something. Write a short post they might upload to their feed.

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
        model = get_model(NARRATOR_MODEL, system_prompt=system_instruction)
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 512,
                "temperature": 0.85,
                "response_mime_type": "application/json",
            }
        )
        parsed = extract_json_from_llm(resp.text)
        if not isinstance(parsed, list):
            return []
    except Exception as e:
        print(f"[WorldNarrator] SNS 배치 생성 실패: {e}")
        return []

    id_to_handle = {c["event_id"]: c["sns_handle"] for c in candidates}
    posts: list[str] = []

    for item in parsed:
        event_id  = item.get("id", "")
        post_text = item.get("post_text", "").strip()
        handle    = id_to_handle.get(event_id)
        if handle and post_text:
            posts.append(f"{handle} 님이 새 게시글을 올렸습니다: '{post_text}'")

    return posts


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
