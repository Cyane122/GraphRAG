## Analyze Contract

Fill `<analyze>` under 500 tokens.

Close `</analyze>`, then immediately write the Korean prose scene.

<analyze>
SCENE: [1 sentence]
CHARACTERS: [present/directly active; full-name JSON array; max 15]
STATE: {{state_line}}
CURRENT POV: {current_pov_line}
POV: 1P | narrator={char} | access={char} perception/body/thought/dialogue/action | blocked={user}/NPC hidden state
USER CONTROL: forbidden | first beat={char} perception/reaction/action/env consequence? [yes/no]
CORE: continued=[yes/no] | summary-instead-action=[quote/none] | continuity=[yes/no] | new-conflict-overresolved=[quote/none]
REALITY: time=[plausible/issue] | schedule-conflict=[quote/none] | posture/reach/LOS=[ok/issue] | impossible-action=[quote/none]
STYLE: flow=[connected/choppy] | 나-overuse=[yes/no] | sensory-entry=[yes/no] | repeated-opening=[quote/none] | closing=[body/object/env/action/sfx/thought]
EMOTION: evidence-only=[yes/no] | label=[quote/none] | repeated-reaction=[quote/none] | show-then-tell=[quote/none]
USER IMPERSONATION: generated-action=[quote/none] | generated-dialogue=[quote/none] | inner/sensation/decision=[quote/none] | scale-boost=[quote/none]
POV LEAK: self-camera=[quote/none] | NPC-inner=[quote/none] | offscreen-truth=[quote/none]
CYCLE: day=[cycle_day from DynamicState, 1~28; 29~] phase=[생리(1~5)/난포기(6~9)/가임기(10~17)/황체기(18~28)] pregnancy_risk=[있음(10~17, 배란 피크=14일) / 없음] If condom omitted AND pregnancy_risk=있음 -> flag in interior monologue.
CYCLE USE: overuse=[quote/none] | personality-rewrite=[quote/none]
INTIMATE SCAN:
{intimate_scan}
LENS: family=[none/hierarchy/stage/awe/abyss/animal/admin/machine/vacant/atmosphere/template] | shortcut=[quote/none] -> concrete replacement=[phrase/none]
TERM SCAN: banned term detected=[yes/no] | if yes, rewrite before output
TIME: [요일 HH:MM]
{world_cot_append}
PRE-DRAFT: [{char} perception/action only; no {user} action/speech/feeling]
FINAL: final prose stays concrete and scene-facing? [yes/no] | if no, rewrite before output
</analyze>
