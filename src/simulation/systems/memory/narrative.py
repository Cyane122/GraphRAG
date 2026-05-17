# ================================
# src/simulation/systems/memory/narrative.py
#
# N턴마다 최근 대화를 타임라인 로그로 압축해 GlobalState.flags에 저장합니다.
#
# Functions
#   - compress_to_narrative_log(recent_turns, current_dt, npc_id, pc_id) -> None : 최근 10턴을 타임라인으로 압축
#   - fetch_narrative_log() -> str : GlobalState에서 타임라인 로그를 반환
# ================================

import json

from src.config import MODEL_EVENT_CREATOR as NARRATIVE_MODEL
from src.core.database import async_driver
from src.core.llm.client import get_model


_SYSTEM_PROMPT = (
    "You are a narrative archivist for a roleplay simulation. "
    "Convert conversation turns into a structured timeline log. "
    "Record only confirmed information. Be concise and factual."
)

_LOG_PROTOCOL = """\
[PROTOCOLS]
- Output: timeline log only. No prose, analysis, markdown headers, or summaries.
- Date header: **[YYYY.MM.DD]**
- Entry format: `HH:MM (Location - Atmosphere): Log body.`
- If exact time is unclear, infer from context or add broad tag: Morning / Afternoon / Evening / Night.
  e.g. `09:00 (Morning, Location - Atmosphere):`
- Relative dates → absolute dates from reference date.
- Record confirmed facts only. Do not invent time, location, emotion, dialogue, or motivation.
- Preserve proper nouns exactly: names, places, items, skills, unique terms.

[ROLEPLAY LOG RULES]
- Input: alternating [User] (player action/speech) and [Actor] (NPC response as narrative prose).
- Focus on: key actions, NPC's emotional/physical state changes, relationship dynamics, meaningful exchanges.
- Tag explicit psychological changes: **[Change: State A ➔ State B]** — only when clearly shown in text.
- Record NPC notable dialogue or reaction when it marks a significant moment.
- Prefer observable fact over interpretation.
  Bad: "they grew closer"
  Good: "Hana held Junho's hand for the first time."
- Do NOT force change tags onto: movement, casual conversation, routine actions."""


async def _fetch_missed_events(log_text: str, npc_id: str) -> str:
    """로그에 언급되지 않은 중요 이벤트를 DB에서 조회해 보충 섹션을 반환한다."""
    try:
        async with async_driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Character {id: $npc_id})-[:INVOLVED_IN]->(e:Event)
                WHERE e.importance >= 5
                RETURN e.summary AS summary, e.timestamp AS ts, e.importance AS importance
                ORDER BY e.timestamp DESC
                LIMIT 5
                """,
                npc_id=npc_id,
            )
            rows = await result.data()

        missed = []
        for row in rows:
            summary = (row.get("summary") or "").strip()
            if not summary:
                continue
            words = [w for w in summary.split() if len(w) > 2][:5]
            if words and not any(w in log_text for w in words):
                ts = row.get("ts") or ""
                imp = row.get("importance", "?")
                missed.append(f"  - [importance {imp}] {summary}  ({ts})")

        if missed:
            return "\n\n[DB Events — not captured in log]\n" + "\n".join(missed)
        return ""
    except Exception as e:
        print(f"[NarrativeLog] 이벤트 검증 실패 (무시): {e}")
        return ""


async def compress_to_narrative_log(
    recent_turns: list[dict],
    current_dt,
    npc_id: str,
    pc_id: str,
) -> None:
    """최근 10턴 (user/actor 쌍)을 타임라인 로그로 압축해 GlobalState.flags.narrative_log에 저장한다."""
    if not recent_turns:
        return

    lines = []
    for turn in recent_turns[-10:]:
        u = (turn.get("user") or "").replace("\n", " ").strip()
        a = (turn.get("actor") or "").replace("\n", " ").strip()
        if u:
            lines.append(f"[User]: {u}")
        if a:
            lines.append(f"[Actor]: {a}")
    combined = "\n".join(lines)

    ref_date = current_dt.strftime("%Y.%m.%d") if current_dt else "unknown"
    ref_time = current_dt.strftime("%H:%M") if current_dt else "unknown"

    prompt = f"""{_LOG_PROTOCOL}

Reference date: {ref_date} {ref_time}
Characters: {npc_id} (NPC), {pc_id} (PC)

Conversation turns:
{combined[:10000]}

Output timeline log:"""

    try:
        model = get_model(model_name=NARRATIVE_MODEL, system_prompt=_SYSTEM_PROMPT)
        resp = await model.generate_content_async(
            prompt,
            generation_config={"temperature": 0.0, "max_output_tokens": 1024},
        )
        log_text = (resp.text or "").strip()
        if not log_text:
            return

        missed = await _fetch_missed_events(log_text, npc_id)
        if missed:
            log_text += missed

        await _store_narrative_log(log_text)
        print(f"[NarrativeLog] 압축 완료 ({len(log_text)}자)")
    except Exception as e:
        print(f"[NarrativeLog] 압축 실패 (무시): {e}")


async def fetch_narrative_log() -> str:
    """GlobalState.flags.narrative_log를 반환한다. 없으면 빈 문자열."""
    try:
        async with async_driver.session() as session:
            rec = await session.run(
                "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.flags AS flags"
            )
            row = await rec.single()
        if not row or not row.get("flags"):
            return ""
        flags = json.loads(row["flags"])
        return flags.get("narrative_log", "")
    except Exception:
        return ""


async def _store_narrative_log(log_text: str) -> None:
    """GlobalState.flags.narrative_log에 새 로그를 누적 저장한다 (최대 4000자)."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.flags AS flags"
        )
        row = await rec.single()

    flags: dict = {}
    if row and row.get("flags"):
        try:
            flags = json.loads(row["flags"])
        except (json.JSONDecodeError, TypeError):
            pass

    existing = flags.get("narrative_log", "")
    combined = (existing + "\n\n" + log_text).strip() if existing else log_text
    if len(combined) > 4000:
        combined = combined[-4000:]
    flags["narrative_log"] = combined

    flags_json = json.dumps(flags, ensure_ascii=False).replace("\\", "\\\\").replace("'", "\\'")
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET gs.flags = '{flags_json}'"
        )
