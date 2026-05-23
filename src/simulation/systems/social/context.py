# ================================
# src/simulation/systems/social/context.py
#
# Build prompt-ready world context from nearby activity and SNS feeds.
#
# Functions
#   - build_world_context(npc_id: str, pc_id: str, location_id: str, current_time: datetime, enable_sns: bool = True) -> dict : Build nearby activity and optional SNS feed context
#   - fetch_sns_panel_state(npc_id: str, pc_id: str, current_time: datetime, limit: int = 12) -> dict : Build UI-ready SNS feed state
# ================================
import hashlib
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
SNS_PANEL_WINDOW_HOURS = 36


async def build_world_context(
    npc_id:       str,
    pc_id:        str,
    location_id:  str,
    current_time: datetime,
    enable_sns: bool = True,
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
    if enable_sns and sns_candidates:
        sns_posts = await _generate_sns_batch(sns_candidates[:MAX_SNS_POSTS])

    print(
        f"[WorldNarrator] nearby={len(nearby[:MAX_NEARBY])} "
        f"sns={len(sns_posts)}"
    )

    return {
        "nearby_activity": nearby[:MAX_NEARBY],
        "sns_posts":       sns_posts,
    }


async def fetch_sns_panel_state(
    npc_id: str,
    pc_id: str,
    current_time: datetime,
    limit: int = 12,
) -> dict:
    """Build UI-ready SNS feed state from recent social/fun autonomous events."""
    events = await _fetch_recent_auto_events(npc_id, pc_id, current_time, SNS_PANEL_WINDOW_HOURS)
    posts: list[dict] = []
    for event in events:
        handle = event.get("sns_handle")
        if not handle or event.get("need_name") not in ("social", "fun"):
            continue
        post_id = str(event.get("event_id") or "")
        if not post_id:
            continue
        posts.append({
            "id": post_id,
            "author": event.get("char_name") or handle,
            "handle": handle,
            "caption": _caption_from_summary(str(event.get("summary") or "")),
            "timestamp": event.get("timestamp") or "",
            "locationId": event.get("location_id") or "",
            "likes": _stable_count(post_id, 18, 420),
            "comments": _stable_count(f"{post_id}:comments", 0, 48),
            "accent": _stable_count(f"{handle}:accent", 0, 5),
        })
        if len(posts) >= limit:
            break
    return {"snsPosts": posts}


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


def _caption_from_summary(summary: str) -> str:
    """Convert a recent activity summary into a short feed caption."""
    text = " ".join(summary.split())
    if not text:
        return "오늘 기록 하나 남겨두기."
    if len(text) > 90:
        text = text[:87].rstrip() + "..."
    return text


def _stable_count(seed: str, low: int, high: int) -> int:
    """Return a deterministic UI count for a post without storing extra graph state."""
    if high <= low:
        return low
    digest = hashlib.blake2s(seed.encode("utf-8"), digest_size=4).hexdigest()
    return low + (int(digest, 16) % (high - low + 1))


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

    system_instruction = "Generate realistic Korean SNS posts for fictional characters in a slice-of-life roleplay."

    prompt = f"""Write a short SNS post each character might upload after their recent action.
1-2 lines, casual Korean. Match personality from name/action. Natural emoji (or none). No direct need mentions. Sound like a real person. Varied styles.

{json.dumps(items, ensure_ascii=False, indent=2)}

Return ONLY JSON array: [{{"id":"<same event_id>","post_text":"<post only — no handle/prefix>"}},...]"""

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
