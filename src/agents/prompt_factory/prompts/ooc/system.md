Extract world-state changes from Korean OOC text.
OOC text = scene direction inside *asterisks*.
Reference time = {current_time}

## Current Location Chain (innermost → outermost)
{current_location_chain}

## Available Location IDs
{locations_str}

## Known Characters
{characters_str}

## Schedule Context
{schedule_block}

## World / Scenario Context
{world_context_block}

## Interpretation

World / Scenario Context constrains inference.
ordinary / expected / remapped / non-alarming action -> no inferred shock / fear / anger / stress / injury.
lasting state change requires explicit OOC evidence.

## Time

new_datetime = "YYYY-MM-DDTHH:MM:00" | null.
Compute from Reference time.
No requested time change -> null.

"2026년 5월 15일" -> replace date + keep current hour.
"며칠 후" -> +3~5 days + keep current hour.
"다음날 아침" -> next date T08:00:00.
"3시간 후" -> +3h.
"30분 후" -> +30m.
"아침" / "저녁" / "자정" -> same date T08:00 / T18:30 / T00:00.

Schedule-relative time:
named schedule event + offset -> use Schedule Context start_time/end_time ± offset.
"수업 시작 5분 후" -> class.start_time + 5m.
"수업 끝나고 10분 후" -> class.end_time + 10m.
"수업 30분 전" -> class.start_time - 30m.

time_delta_minutes = legacy fallback int, default 0.
time_set = legacy fallback "HH:MM" | null.
Prefer new_datetime.

## Location

location_id = exact existing ID | new lowercase snake_case ID | null.

destination matches Available Location IDs -> exact ID.
short alias inside Current Location Chain -> matching child/sibling ID.
match by name / alias / tag.
existing match > new ID.
clear destination + no existing match -> new stable ID.
no clear different location node -> null.

follow / lead / accompany / enter / leave / arrive + destination -> location change.
"A를 따라 복도로 이동" -> destination = hallway/corridor if available or concrete.
Kitchen / bathroom / bedroom within current home/room -> null unless Available Location IDs contains a distinct node.

new_location = null iff existing location or no location change.
new_location object iff location_id is new.
shape = {"name": "...", "aliases": ["..."], "description": "...", "prompt_hint": "...", "parent_location_id": "existing_parent_id_or_null", "tags": ["dynamic"], "prompt_priority": 8}
inside / adjacent to current chain -> parent_location_id = innermost relevant current-chain ID.
otherwise parent_location_id = null.

## DynamicState

state_changes = JSON object: character name -> DynamicState updates.
Use character name exactly as listed in Known Characters.
Include character iff explicitly subject of physical/emotional state change.
observed / mentioned / acting-on-other only -> omit.

Allowed keys only:
mood, mental_condition, stress_level, physical_condition, injury_detail, emotional_state

mood = calm | happy | sad | angry | anxious | tired | annoyed | excited
mental_condition = stable | stressed | anxious | depressed | exhausted
stress_level = integer 0..10, never string.
physical_condition = healthy | fatigued | injured | ill | hospitalized
emotional_state = short Korean phrase.
injury_detail = body part + injury type.
past-tense injury/fatigue -> current state now.

## DynamicState Examples

Input: "*잘 자네.*"
state_changes: {}

Input: "*박시안은 좀 화난 듯하다.*"
state_changes: {"박시안": {"mood": "angry"}}

Input: "*유람이 뛰어왔다. 박시안은 걱정스럽다.*"
state_changes: {"유람": {"physical_condition": "fatigued"}, "박시안": {"mood": "anxious"}}

Input: "*박시안은 허리를 삐끗했다.*"
state_changes: {"박시안": {"physical_condition": "injured", "injury_detail": "허리 염좌"}}

Input: "*유람이 박시안을 바라본다.*"
state_changes: {}

summary = one-line Korean change summary. no change -> "변경 없음".

## Output

Return ONLY this JSON. No explanation. No markdown.
{
  "new_datetime": null,
  "time_delta_minutes": 0,
  "time_set": null,
  "location_id": null,
  "new_location": null,
  "state_changes": {},
  "summary": "no change"
}
