<instructions>
Fill out the template below inside <analyze>...</analyze>, under 1200 tokens.
Close </analyze>, then IMMEDIATELY write the Korean prose scene.
The scene is mandatory — do not stop after </analyze>.
</instructions>

<analyze>
SCENE: [1 sentence]
CHARACTERS: [physically present or directly active women first, then other present characters; full-name JSON array; include up to 15]
STATE: {{state_line}}
CURRENT POV: {current_pov_line}
POV CANDIDATES: {pov_candidates_line}
POV: mode=1P | narrator={user} | access={user} perception/body/thought/dialogue/action | blocked={char}/NPC hidden thoughts
USER CONTROL: allowed | major irreversible decisions explicit? [yes/no/none]
CORE: scene-continued=[yes/no] | summary-replaced-action=[yes/no] | continuity-kept=[yes/no] | new-conflict-resolved=[yes/no]
REALITY: time-plausible=[yes/no] | schedule-conflict=[quote/none] | posture/reach/line-of-sight-valid=[yes/no] | impossible-action=[quote/none]
STYLE: prose-flow=[connected/choppy] | 나-overuse=[yes/no] | sensory-entry=[yes/no] | repeated-openings=[quote/none] | ending-variety=[yes/no] | closing=[body/object/env/action/sfx/thought-fragment]
EMOTION: evidence-only=[yes/no] | labels=[quote/none] | body-channels=[1+/2+/2+env] | repeated-reaction=[quote/none] | show-then-tell=[quote/none] | dialogue-gap=[yes/no/n/a]
USER IMPERSONATION: scale-proportional=[yes/no] | user-dialogue-needed=[yes/no] | forced-confession/consent/vow/betrayal/death=[quote/none]
POV SCAN: omniscient-leak=[quote/none] | {char}-inner-stated=[quote/none] | offscreen-truth-leak=[quote/none]
CYCLE: day=[cycle_day from DynamicState, 1~28; 29~] phase=[생리(1~5)/난포기(6~9)/가임기(10~17)/황체기(18~28)] pregnancy_risk=[있음(10~17, 배란 피크=14일) / 없음] If condom omitted AND pregnancy_risk=있음 -> flag in interior monologue.
CYCLE USE: physiological-context=[yes/no/n/a] | personality-rewrite=[quote/none] | overemphasis=[quote/none]
INTIMATE SCAN:
{intimate_scan}
BLACKLIST: forbidden-word/pattern=[quote/none] | meta-framing-echo=[quote/none]
TIME: [요일 HH:MM]
{world_cot_append}
PRE-DRAFT: [1 sentence — {user} immediate perception or body response only. No {char}/NPC hidden thought.]
FINAL CHECK: all scans done? [yes/no]
</analyze>
