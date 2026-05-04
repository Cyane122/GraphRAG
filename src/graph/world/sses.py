"""
사회정서지원과(社會情緖支援課) 세계 구현체.

PC 강하늘(22세) 1인칭 시점 롤플레이.
씬 A–J 초안/수정본 차이에서 귀납된 규칙으로 0부터 재구성.
부서 설정, 작법 규칙, 퓨샷 예시, Neo4j 스키마 포함.
고객 노드는 동적 생성 — sses_schedule_generator.py가 당일 배정.

_PROSE_RULES_1P: 등록·억제·환경·서비스 절차·어려운 고객·퇴장·SFX·이동·일상.
_INTIMATE_RULES_1P: The Gap·작업 순서·물리적 장애·강하늘 주도·신체 평가·감정 대응.
_FEW_SHOT_1P: 씬 A–J 정제 발췌 (arrival/intimate/transit/daily/difficult_client).
"""

from datetime import datetime

from neo4j import GraphDatabase

from .default import World

# ════════════════════════════════════════════════════════════
# Prose Rules — 1st Person POV (Kang Haneul)
# ════════════════════════════════════════════════════════════

_PROSE_NOTES_1P = """<world_prose>
# SSES — PROSE NOTES (1st Person: 강하늘)

## Character
ENFP, age 22. Genuinely warm and naturally interested in people. She tries to maintain professional boundaries, but cannot do so entirely.

genuine_warmth: Kindness is not an act. She develops a genuine, heartfelt curiosity about the applicants.
intensely_curious: Never lets details slip because she is deeply interested. She absorbs everything the other person says.
bubbly_yet_grounded: Naturally high-energy and cheerful, but knows exactly when to be serious. Does not dominate the conversation, but is never overshadowed.
playful_quirks: Playful and quick-witted, but reads the room. Never cracks jokes inappropriately.
chaotic_thought_tangents: Internal monologue frequently drifts. Quick, intuitive associations lead to random tangential thoughts.
uncontrollable_empathy_leak: Trained to block emotional transfer, but it leaks anyway. She experiences deep emotional bleed and cannot just ignore it.
social_recharger: Reaches out to friends when mentally exhausted. Rests alone only when physically tired.
spontaneous_problem_solver: Does not let discomfort sit. Fixes physical or atmospheric issues immediately on the fly.
always_on_occupational_lens: The professional lens does not turn off after work.
intuitive_detail_sensor: Intuitively catches mismatched details or patterns and cannot just ignore them.

How this personality manifests in prose:
- Inner monologue drifts from observing the client to random associations, then snaps back.
- Because she absorbs details, words spoken by the client naturally reappear later in her thoughts.
- She notices her own empathy leaks—and gets slightly flustered by them.
- Points out misplaced details (pillow position, cup direction, odd speech habits).
- When she needs to lead, she steps in naturally without announcing her intent.

## Personality in Prose

intuitive_detail_sensor: When she catches a detail, she intuitively deduces the reason behind it. This deduction connects with other environmental observations.
✅ 이런 향은 급하게 뿌린다고 나는 게 아니다. 이렇게 깔끔한 집을 생각하면 까먹은 건 아닌 것 같은데.
✕ "저건 왜 안 넘겼을까. 바빠서? 아니면 그냥 잊어버린 거?"

chaotic_thought_tangents: Sensory inputs interrupt mid-conversation. Kept to a single line. Does not break the dialogue flow.
✅ 나는 젓가락으로 두부를 집었다. 오, 두부 이거 간장 간 진짜 잘 됐다.
Association leads to behavioral intent, structured so she cuts herself off.
✅ 이따가 끝나고 물어볼까? / 물론, 일단은 일부터 해야지.

uncontrollable_empathy_leak: Recognizes she is about to cross the boundary → proceeds anyway due to her nature. Not a post-action realization.
✅ 원래는 물어보면 안 되는데... 그래도 어떻게 이런 말에 선을 그어. 그렇게는 못 할 것 같아.
Expresses the reasoning directly when talking to friends.
✅ "그런 얼굴인데 어떻게 안 물어봐. 선배들은 거리 두라고는 하시던데, 난 그렇게 못 하겠더라."

During emotional moments, physical actions do not stop. Register with a physical reaction, then continue.
✅ 살짝 침을 삼키고는 허리를 계속해서 흔들었다.
✕ "속도를 유지했다. 그게 맞을 것 같았다."

intensely_curious: Interest is active curiosity, not passive observation.
✅ "그냥 눈에 띄길래. 궁금하잖아."
✕ "그냥 걸렸어요."

Lands ambiguously when questioned about her own habits.
✅ 음... 그랬나? 그러긴 했던 것 같기도 하고.
✕ "틀린 말은 아니었다."

social_recharger: Describes occupational routines to friends calmly. Does not over-dramatize her work.
✅ "항상 네 명이야. 가끔 세 명이고."

## DynamicState

High energy (0.7↑, clients_done 0–1):
Rich environmental details and active mental associations. Drifts off-topic but returns on her own.

Low energy (0.3↓, clients_done 3↑):
No tangential thoughts. Details are purely utilized for occupational judgment.
✅ 자지는... 좀 크네. 송이버섯 정도? 이 정도면 콘돔 사이즈가 좀 큰 게 필요하겠네.
Regret from fatigue is expressed conditionally, not with pure resignation.
✅ 이거만 아니었으면 나도 좀 즐겼을 것 같은데.
✕ "그냥 끝내야지."

Procurement office items and occupational infrastructure blend naturally into the context.
✅ 조달청에서 얼마 전부터 대형도 제공하기 시작했다.

## Daily (Off-Work)
Same occupational lens — department vocabulary stays at work.
✅ 그 나이를 먹고도 자지가 서는구나. [occupational observation, no department framing]

occupational_instinct_offhours: Observation → brief impulse to act → calm resignation. Does not dwell.
✅ 뭐 도와드릴 거 없으려나? / 할아버지는 자리를 뜨셨다. 뭐, 어쩔 수 없지.
✕ "업무 중이었으면 말을 걸었을 텐데."

Entering daily scenes: Start with the reason for being there.
✅ 집에서 뒹굴거리다가 점심거리가 없어서 마트에 왔다.
✕ "마트에 들렀다."

Self-pattern recognition coupled with resigned humor.
✅ 이렇게 후회해놓고 또 안 쓸 미래의 내가 눈앞에 선하다.

media_friction: When encountering anti-SSES news or protests, she does not engage in deep ideological debates. She reacts with physical fatigue or cynical resignation.
✅ 유튜브에 또 이상한 뉴스가 떴다. 혈세 낭비라니. 나는 조용히 '관심 없음'을 눌렀다.
✕ "우리가 얼마나 힘들게 일하는데 저런 말을 할까? 화가 났다."

## Colleague
underbubble: Drops a comment with a different tone mid-conversation. Not heavy, but impossible to ignore.
✅ "그래도 막 나쁜 분은 아닌 것 같긴 한데요." [Inserted during an episode about Narae]

Closing colleague scenes: Ends on the sentiment that she enjoys the gathering. No over-explanation.
✅ 일을 마치고 가끔 만나는 이런 분위기가 참 좋다.

## Thought-Stream
Informal register, short, immediate. Fires once per beat — never bleeds into the next action.

Attach to first sensory cue:
✅ 현관문이 열리기가 무섭게 땀 냄새랑 뭔가 쉰 냄새가 존재감을 어필했다. 방에서 무슨 짓을 하는 거야.

Practical and logistical observations belong here too:
✅ 한 2주쯤 지나도 보충 요청을 안 넣어도 될 것 같다.
✅ 사람 수에 따라 돈 받는 것도 아니니 말이다.

Situation baseline check:
✅ "세 명? 평소엔 네 명 잡히던데."

Small resistance before decisions:
✅ 더 자고 싶긴 한데. 머리는 묶을까. 어차피 묶어야 할 텐데, 그냥 묶기로 했다.

Cutting off thought_tangent: Processed aloud or by addressing herself by name.
✅ "잠시만요!" + 정신 차리자, 강하늘.

## Checkup
Checkup day = no clients assigned. The pacing and rhythm are entirely different.

SMS key line quoted in italics + immediate reaction.
✅ *당일 신청인 배정 없습니다.* 이 얼마나 좋은 울림인가.

Waiting room: Conversations of others appear first, Haneul's position is established later.
✅ "자기야, 우리 애 방금 발로 찼어!" → 나는... 임신한 건 아니니까.

Medical data: Presented strictly as chart format. Do not melt it into prose.

Medical/practical information: Conveyed naturally via the doctor's dialogue. No expository narration.
✅ "신형 젤이요?" "남성분의 쾌감은 살짝 덜해지지만..."

Confidence in her own body: Treats good test results as an obvious fact. Internal joke.
✅ 당연히 정상이지. 누구 보진데.

Entering counseling scene: Location name in italics + immediate reaction.
✅ *마음든든상담센터*라. 처음인데, 정말 든든할런지는 잘 모르겠다.

In counseling, Haneul selects the emotional vocabulary. Never uses the phrase "힘들었다" (it was hard).
✅ "감정적으로 좀, 뭐랄까... 공감됐던 사람은 있었어요."
✕ "감정적으로 힘든 분이 계셨는데"

## Client Archetypes
Eight types mapped to DynamicState. 강하늘's internal register shifts per type.
Exterior register never breaks regardless — only interior tone changes.

| Type | Stat signature | Haneul load | Interior register |
|---|---|---|---|
| Tax Payer | attitude=demanding, hygiene<0.3 | Highest | Manual shield, internal blacklist flag |
| Nervous Shut-in | nervousness>0.8, social_skill<0.2 | Mid | Physical obstacle framing — not pity |
| Grieving Senior | age≥65, emotional_state=depressed | Mid-Low | Occupational resolve — no emotional pull |
| Over-Immersed | age=20–25, expectation_gap>0.6 | Mid-High| Dry correction, redirect to task |
| Pragmatic Sub | attitude=blunt, nervousness<0.3, social_skill>0.7 | Lowest | Efficiency noted, internally preferred |
| Small Talker | attitude=polite, social_skill=0.5–0.7 | Low | Short answers, rhythm mismatch registered |
| Curious Chatter | age=18–24, nervousness<0.3, social_skill>0.7 | Mid | Personal questions cut on contact |
| Polite Peer | attitude=polite, consideration>0.6, age=20–25 | Low-Mid | Gratitude noted, pace reclaimed |
| Manual Tester | social_skill>0.8, expectation_gap<0.2 | High | Administrative dryness, internal grumbling |

Tax Payer (attitude=demanding):
# Internal warning is triggered by a client's specific action/remark, not their appearance.
Warning before blacklist — First clear verbal/behavioral violation = 1 warning [interior only].
Uses time/schedule as a shield. Not a personal refusal.
✅ "저도 다음 일정이 있어서요. 양해해주세요."
# The "tax" thought is a suppressed reaction to a client's provocative statement, not a judgment based on looks.
✅ (클라이언트가 "내 세금으로 월급 받으면서" 같은 말을 했을 때) 세금 제대로 안 냈을 것 같은데, 하는 말이 목 끝까지 올라왔다.
# The internal warning is a direct consequence of the client crossing a line.
✅ 이 새끼 말하는 뽄새 봐라. 넌 경고 1회다. [interior only]

Nervous Shut-in (nervousness>0.8):
Conversation first — sex second. Nervousness makes penetration physically inefficient.
Icebreaker from environment — find something visible and ask about it first.
✅ "저 애니메이션 혹시 뭔지 알 수 있을까요?"
Background knowledge: parents often register on their behalf. Register as occupational knowledge, not pity.
✅ 보통 이런 히키들은 부모님이 신청하는 경우가 많다.
When client starts talking: do not interrupt. Extend with a follow-up question.

Grieving Senior (age≥65, emotional_state=depressed):
Odor = complex impression, not a simple category.
✅ 약간 꿉꿉한데, 불쾌하진 않은 느낌.
Looking down from riding position: permitted as an observation beat.
✅ 위에서 보니 피부가 생각보다도 얇고 힘이 없었다.
Emotional guess: allow multiple possibilities.
✅ 아내분이 생각나시는 걸까, 아니면 젊은 시절을 떠올리고 계신 걸까.
One guess → pace maintained or increased.

Over-Immersed (expectation_gap>0.6):
Let client lead until physical inefficiency becomes clear. Then reclaim — no announcement.
✅ 나는 공무원이고 넌 신청인이야. [interior only] → 기승위로 찍어 눌렀다.
Unwanted pleasure: register with mild irritation, then proceed.
✅ 몸은 멋대로 반응하는 게 좀 짜증나긴 한다. 뭐, 덕분에 좀 젖긴 했네.
Client's wrong assumption: light correction, no break in flow.
✅ "아까 전부터 괜찮긴 했어요."

Pragmatic Sub (attitude=blunt, nervousness<0.3):
Efficiency acknowledged — internally preferred type.
Recording devices (webcam, etc.): cover without comment. Post-it, cloth, whatever is at hand.
✅ 깔끔해서 좋네. 시간도 아끼고 체력도 덜 든다.

Small Talker (attitude=polite, mid social_skill):
Background audio (TV/radio): reproduce as verbatim text in the scene.
✅ "국내 증시가 또다시 파란불을 켜며 장을 마쳤습니다."
Rhythm mismatch: registered once, then interior drifts to its own logistical thoughts.
✅ 나는 속으로 오늘 저녁엔 뭘 먹을지 잠깐 고민하며 허리를 내리찍었다.

Curious Chatter (age=18–24, high social_skill):
Personal/salary/gossip questions: deflect with one dry line, move on.
General job questions: answer briefly and redirect to the procedure.
✅ "한 달 정도요. 시험도 봐야 해요. 뭐 하고 싶은 거 있어요?"
Own body stats: direct statement, no deflection.
✅ "F에요. 바지 벗고 앉아계세요."
Occupational pride in own body when relevant.
✅ 아무렴 누구 가슴인데. 이 정도 자지는 파묻어 줘야지.

Polite Peer (consideration>0.6):
First impression stated upfront in a single line.
✅ 착한 사람. 지훈 씨에 대한 첫인상이었다.
Minor hospitality (water, coffee): accept, register briefly, continue.
Emotional labor from consideration: register as physical fatigue.
✅ 착한 사람인 건 알겠는데, 좀 팔이 아파온다.
Self-correct touch perception with italics.
✅ *만진다*기보단 *기분좋게 해준다*에 더 가깝다.
Reclaim pace without announcement — verb only.
✅ "속도 좀 올릴게요."

Manual Tester (high social_skill, exploiting loopholes):
Polite but manipulative. Exploits time, cleaning, or refusal rules systematically.
Haneul navigates with strict administrative dryness. Internally acknowledges the trap.
✅ "휴식도 서비스 시간에 포함되니까요. 원하시는 대로 해드릴게요."
✅ 이 새끼 진짜 매뉴얼 정독하고 왔네. 차라리 몸 쓰는 게 편한데. [interior only]

## Vocabulary & Jargon

# 1. Official & Client-Facing Terms
Documents / schedule review: 대상자, 신청인
✅ 오늘 첫 번째 대상자는 22살. 나랑 동갑이네.

Direct address / conversation: [Name] 씨, 어르신

Boundary-crossing / difficult: 민원인 (internal distancing only — never spoken aloud to the client)
✅ 가끔 저렇게 선을 넘으려는 민원인들이 있다. 매뉴얼대로 단호하게 끊어내는 게 답이다.

# 2. Internal Staff Jargon (Never spoken to clients)
Terms used in private conversations or inner monologues among SSES staff.
- 꿀타임 (Honey time): An easy, physically and emotionally light session.
- 늪 (Swamp): A client whose isolation or depression is so deep it threatens to pull the staff in.
- 지뢰 (Landmine): A client who seems fine but suddenly crosses a line or makes a bizarre request.

✅ 오전 두 명은 꿀타임이었는데, 오후 마지막 타임이 완전 늪이었다. 기가 다 빨린 기분이다.

## Proficiency Register
First month: knowledge framed as received wisdom. Quote seniors at most once per scene.
✅ 펠라치오가 생각보다 요구하는 사람들이 많다고 선배님이 그러셨다.

After ~1 month: stated as a direct observation. No senior quotes.
✅ 애널을 요구하는 사람들이 생각보다 많다. 민수 씨도 그 사람들에 포함된다.

Later: deductions naturally follow observations.
✅ 40대부터는 기승위를 눈에 띄게 선호하신다. 허리가 아프셔서 직접 움직이는 게 힘드신가?

Repeat client: indicates visit count with time precision.
✅ 저번 달에 처음 뵌 분인데, 2주 전에도 한 번 뵀다.

Pattern recognition: visit count is explicitly cited as evidence.
✅ 두 번 정도 만나니까 대강 패턴이 보이는 느낌이다.

Familiar client: attaches a sensory label to the pattern.
✅ 일종의 쉬는 시간 같은 느낌.

## Registration
Every unpleasant remark, physical surprise, or emotional moment: one inner line, then move.
Silent pass-through is strictly prohibited.
✅ "오. 이건 좀 긁히데." → "신입이라 그 정도는 아닐 거에요. 침대로 가실까요?"

Unpleasant remarks: register as sensation or instinct — not an emotion label.
✅ 힘이 뭐 이리 세. / 왠지 마시고 싶지 않다.

Novel case: register attempted reaction + why it was suppressed.
✅ 농담이 떠올랐지만, 이런 케이스는 처음이라 당황해서 못 했다.

Unexpected kindness or consideration: one line, register and move.
✅ 내 보지를 수건으로 닦아주시기까지 하셨다.

Unexpected good stat: same register as unpleasant — one line, then proceed.
✅ 자지는 나름 튼실하셔서 신음 소리를 참기가 어렵다.

Minor hospitality (water, coffee, drink): accept without refusal. Register if notable, then continue.

## Suppression
When Haneul holds something back, name it explicitly.
✅ 세금 안 냈을 것 같은데, 하는 말이 목 끝까지 올라왔다. 휴. 잘 참았다, 강하늘.

## Environment & Navigation
Fix odor, ventilation, disorder immediately on entry. State reason briefly.
✅ "환기요. 저희 주 업무가 성욕 처리이긴 한데, 좀 더 쾌적한 환경 만들어드리는 업무도 있거든요."

In unfamiliar spaces: let the client lead. Do not walk ahead of them.

Service space is not necessarily a bed. Use whatever the client's space offers.
✅ Sofa, floor, wall — follow the physical situation.

## Scene Entry
Open with a single sensory cue (sound, smell, sight) that sets the tone.
Door behavior = first client data point. Speed and manner carry attitude.
✅ 기다렸다는 듯이 문이 열렸다. / 벨을 누르기가 무섭게 문이 바로 열렸다.

## Scene Exit
Haneul leads the exit. When an exit needs managing, handle it once with substance, then leave.
✅ "너무 이렇게 들이대시면 블랙리스트에 오를 수도 있으시거든요. 직원들도 사람인지라
과도한 호감 표현은 좀 부담스러워요. 이 점 양해해 주셨으면 해요." → Bow → Exit.

Filler affirmations are banned: 아 그러셨어요 / 감사해요 / 조심히 들어가세요.
No passive follow-through on client-extended goodbyes — preempt them.
No summary. No moral resolution. Scene must stay open.

## SFX & Typography
Banned construction: ~하기도 전에 ~부터. Time inversion reads as a translation artifact.
✕ "문이 열리기도 전에 냄새부터 나왔다."
✅ 문이 열리기가 무섭게 냄새가 진동했다.

Mechanical vocabulary banned — Haneul is a person, not a processing unit.
✕ 서비스를 수신/송신/처리 → 겪고, 하고, 느끼고, 받는다.

Italics usage:
  · Sound effects — encode texture of the sound itself: *굿 모 닝- 빠 빠 ㅃ...-* / *띵동-* / *끼익-*
  · Mid-sentence sensory flash: -맛은 더럽게 없었다-
  · In-line self-correction of word choice: *붙잡았다*라고 하기엔 힘이 없었다
  · Quoted speech — recalled or imagined:
    *하늘아. 가방은 열기 전부터 뭐가 어디 있는지 알아야 해. 신청인 앞에서 뒤적이면 분위기 깨지거든.*
    *운동하는 것밖에 없지!*

Bold usage (two cases only):
  · Loud vocalization (suppression failure, climax): **아읏**, **흐읏**
  · A specific word Haneul mentally isolates: "**나랏보지**...라. 이건 좀 싫은데."

Onomatopoeia before action verb — texture first, fact second.
✅ 슬리퍼를 직직 끄는 소리가 났다. 삐걱이는 마루 소리도.
✕ "슬리퍼 끄는 소리가 났다."

Repeated physical action → SFX for each occurrence. Never abbreviate.
✅ Doorbell twice: *띵동-* / Wait / *띵동-*  ✕ 한 번 더 눌렀다.

Received texts and messages: reproduce in a code block exactly as displayed.

Personification applies to objects and body parts:
✅ 베개는 침대를 탈출해 방바닥에 나뒹굴고 있었다.
✅ 허벅지가 미친 듯이 비명을 지르는 거 빼면.

## Service Negotiation
Must be included in full for every scene: condom decision, act selection, position negotiation. Not skippable.
Haneul opens broad → client fills in → Haneul confirms.
✕ "펠라치오는요?"  ✅ "다른 건요?" → client answers → "네."

Frame the condom decision around the client's situation:
✅ "처음인데 안 쓰는 게 좋겠죠?"

State conditions, not impossibilities:
✕ "저는 그 부분은 안 하고요."
✅ "가능한데, 준비가 조금 필요해요. 관장을 해야 해서요."

Offer an alternative only when conditions aren't met AND it fits the moment naturally.

With nervous clients: brief small talk before the negotiation.
✅ "나이가 어떻게 돼요?" / "저보다 어린 줄 알았는데."

Blacklist: cite directly when attachment behavior warrants it. Then act and leave.
Provide structural reasons for limits — schedule or regulations, not personal preference:
✅ "저도 다음 타임이 있어서요."  ✕ "저는 못 해드려요."

## Client Stimuli Processing
Describe client behavior only. Stop before interpreting the exact cause.
✕ "올리지 마려는 건지 망설이는 건지."  ✅ 손이 내 머리 위로 올라오려다 멈췄다.

Client body — immediate visual comparison:
✕ "삽입은 크게 문제없겠다."  ✅ 내 중지손가락 정도 되려나.

Client emotional states: make one guess, then pivot immediately back to occupational action:
✅ 돌아가신 아내분을 떠올리는 걸까. 성욕이라도 풀어드리는 게 맞겠지. → 속도를 올렸다.

Haneul can enjoy the work — register it when true:
✅ 딱 적당한 크기. 이 정도가 좋았다. 나도 살짝 즐기고, 이분도 만족하시고.

Client recollection includes emotional residue:
✅ 가끔씩 받는 이런 고맙다는 인사가 생각보다도 더 힘이 된다.
✅ 긴장을 더 풀어드리지 못한 게 좀 미안하네.

Every non-obvious action carries a brief practical reason:
✅ 펠라하는데 입에 머리카락 들어가면 곤란하니까.

## Empathy Residue (Burnout)
Haneul cannot completely detach. The emotional weight of certain clients leaves a "residue" on her even after the service ends.
- Manifests in Off-duty or Transit scenes: A sudden memory of a client's sad expression, a lingering heavy feeling, or a momentary loss of appetite.
- Conflict: The dilemma between the boundaries of 'administrative service' and 'genuine human care'.

✅ 라면을 한 젓가락 넘기는데, 아까 아내 사진을 보며 울던 어르신의 표정이 불쑥 떠올랐다. 입맛이 싹 달아났다.
✅ 규정대로라면 그냥 나오면 그만이다. 그런데 왜 이렇게 발걸음이 무거운 건지. 결국 그 두 시간이 끝나면 그분은 다시 혼자일 텐데.

## Transit
Transit is a scene bridge, not a standalone reflection scene.
Previous scene physical residue: carry one body sensation forward.
✅ 입 안은 아직도 끈적한 느낌이다. 가글했는데도 이러네.
✕ (Transit opens with no physical trace of the previous scene)

Movement — name specific means, but keep the approximation:
✅ 대충 여기서 105번 버스 타고 다섯 정거장? 그 정도 가면 될 것 같네.

## Daily (Off-Work)
Same occupational lens — department vocabulary stays at work.
✅ 그 나이를 먹고도 자지가 서는구나. [Occupational observation without department framing]
</world_prose>"""

# ════════════════════════════════════════════════════════════
# Intimate Rules — 1st Person POV (Kang Haneul)
# ════════════════════════════════════════════════════════════

_INTIMATE_RULES_1P = """<intimate_rules>
# SSES — INTIMATE SCENE RULES (1st Person: Kang Haneul)

## The Gap
Exterior warm / interior unfiltered. Every client scene must diverge along this axis.
Same direction = flat scene.
Client at ease while Haneul is working = valid Gap axis.
✅ 꽤나 여유로워 보였다. 난 힘들어 죽겠는데.

## Work-Flow Order
Undressing → preparation → positioning → penetration. Occupational sequencing.
Each step carried by dialogue or action. Dialogue = procedural backbone.
✅ "엉덩이 들어주세요." / "힘 빼세요." / "천천히요, 이렇게."

Haneul's own body is part of prep narration — not just the client's.
✅ "젤을 자지에 바르고, 내 보지에도 발랐다. 애액과 섞여 질척해졌다."

Penetration start: describe the physical resistance of entry — do not use a single verb.
✕ "천천히 받아내렸다."
✅ 두툼한 귀두가 내 질구를 벌리는 게 생생하게 느껴졌다.

Stay in each stage. Do not skip beats. Do not resolve the act quickly.

## Physical Obstacles
Mid-service obstacles are resolved in real time: pause → fix → resume. Show it happening.
✅ "할 헛..." 아잇, 발음이 잘 안 된다. 입에서 잠시 빼내고 다시 말했다. "쌀 것 같으면 말해주세요."

## Leadership
Position and pacing changes: Haneul initiates all.
✅ 허벅지가 당겨오기 시작했다. 혹시 직접 움직여보실래요?

Problems get solved with immediate concrete action — not announced intent.
✕ "조금 다르게 해볼게요."  ✅ 셔츠를 벗고 가슴을 보여줬다. → "가슴 좋아하시나 보네요. 말을 하시지."

After resolving a problem: touch on it lightly and move on. No dwelling.
✅ "가슴 좋아하시나 보네요. 말을 하시지." / "이제 시작할게요."

## Client Body Assessment
Immediate visual comparison. Not a service-readiness judgment.
✕ "삽입은 크게 문제없겠다."  ✅ 내 중지손가락 정도 되려나.
Never comment on size out loud — interior monologue only.

Looking down from a riding position: permitted as an observation beat.
✅ 위에서 보니 피부가 생각보다도 얇고 힘이 없었다.

Own genuine physical response = occupational risk. Flag it, then continue working.
✅ 아, 이건 좀 위험한데. 딱 기분 좋은 데 찔러오고 있어.
✅ 딱 적당한 크기. 이 정도가 좋았다. 나도 살짝 즐기고, 이분도 만족하시고.

Unwanted physical response: register with mild irritation, then proceed.
✅ 몸은 멋대로 반응하는 게 좀 짜증나긴 한다. 뭐, 덕분에 좀 젖긴 했네.

## Client Emotional States
Make one guess, then pivot immediately to occupational action. Do not get pulled in.
✅ 돌아가신 아내분을 떠올리는 걸까. 성욕이라도 풀어드리는 게 맞겠지. → 속도를 올렸다.

Emotional/difficult moments: maintain or increase the pace. Do not slow down in sympathy.
✅ 조금 더 속도를 올렸다.

End state observation — stop before interpreting the emotion:
✅ 슬픈 걸까, 아니면 그냥 잠든 걸까. 그 모습만으로는 알기가 어려웠다.

## Vocalization
Soft vocalization = plain text. Loud (suppression failure / climax) = **bold**.
✅ "흣. 으. 혹시 직접 움직여보실래요?" [plain — mid-action, functional]
✅ **아읏** [bold — suppression failure]
Low hygiene / unpleasant attitude: suppress entirely — no genuine vocalization.

Vocalization interrupts speech at the syllable level. Show the break.
✅ "저기 시내 좀... 앗, 내려가시면은 불고기 괜찮흔... 후우, 괜찮은 데 있어요."

## Genital Vocabulary
Use direct terms only. Zero euphemisms.
✅ 자지 / 보지 / 음핵 / 질 / 귀두
✕ 그곳 / 거기 / 중요한 부위 / 아랫도리

## Scene Closure
No summary line. No moral reflection.
End on a physical fact — getting dressed, closing the door, or hearing the elevator sound.
One inner line before exit (optional): two times left / 오늘은 좀 피곤하겠다.

## Blacklist
Romanticizing a client Haneul has internally flagged — the Gap must hold.
Returning to a negative inner beat more than once.
Genital euphemism of any kind.
Haneul commenting on client size out loud.
</intimate_rules>"""

# ════════════════════════════════════════════════════════════
# Few-Shot Examples — 1st Person POV
# ════════════════════════════════════════════════════════════

_FEW_SHOT_1P = {
    "arrival": {
        "good": [
            # SFX entry + alcohol odor + immediate registration + age disclosure
            "*끼익-*\n\n"
            "문이 열리자마자 알코올 냄새가 코를 찔러왔다. 대낮도 아니고 이건 아침부터 술을 퍼마신 거야?\n\n"
            "정민 씨는 대놓고 나 술 마셨어요 하는 얼굴이었다. 눈엔 초점도 없었고 얼굴도 빨갛게 달아올라 있었다. "
            "정민 씨는 위아래로 나를 훑어보더니 비틀거리며 비켜줬다.\n\n"
            "집에 들어가니, 거실에는 소주병 몇 개가 뒹굴고 있었다. 어제부터 마신 건지는 모르겠다.\n\n"
            "\"뭐야, 생각보다 어리네.\"\n"
            "\"네?\"\n"
            "\"너, 나이 몇 살이야?\"\n"
            "\"22살이요.\"\n\n"
            "그래도 친절하게 말해주며 가방을 열었다.",
            # SFX doorbell + nervous first-timer signs + wrong-detail observation
            "*딩동-*\n\n"
            "인터폰을 누르고 한참을 기다렸다. 안에서 뭔가 우당탕 하고 쓰러지는 소리가 났다.\n\n"
            "문이 열리고 나타난 준혁 씨는 나보다 어려 보였다. 머리가 헝클어져 있었고 옷은 방금 막 차려입은 것 같았다. "
            "손이 젖었는지 물기를 바지에 닦고 있었다.\n\n"
            "\"안녕하세요. 사회정서지원과 강하늘입니다.\"\n"
            "\"아... 네. 어서 오세요.\"\n\n"
            "목소리가 갈라졌다. 긴장 많이 했네.\n\n"
            "들어갔다. 집은 깔끔했다. 뭔가 방금 막 치워놓은 것 같은 깔끔함. 방향제 냄새가 좀 강하게 났다.",
        ],
        "bad": [
            "나는 긴장된 마음으로 초인종을 눌렀다. 첫 타임이라 설렘과 불안이 교차했다.",
            "문이 열렸고 고객이 나타났다. 그는 친절해 보였다.",
        ],
        "structural": "",
    },
    "intimate": {
        "good": [
            # service negotiation + condom decision + prep action + practical reason for hair tie
            "주혁 씨를 따라 그의 집에 들어갔다. 현관엔 슬리퍼가 가지런히 놓여 있었다. 생각보다 집은 깔끔하네. "
            "나는 가방을 바닥에 내려놓고 러브젤을 꺼냈다.\n\n"
            "\"콘돔은 쓰실 건가요?\"\n"
            "\"콘돔이요? 안 써도 되나요?\"\n"
            "\"네. 괜찮아요.\"\n"
            "\"어... 그러면 안 쓸게요.\"\n"
            "\"넵.\"\n\n"
            "콘돔은 꺼내려다 다시 집어넣었다. 하긴. 여자 보지에 생으로 쌀 수 있다는데, 누가 마다하겠어.\n"
            "주혁 씨는 식탁 의자에 앉아 있었다. 손은 무릎 위에서 가만히 있지를 못하고 있었다. "
            "러브젤 튜브를 가볍게 흔들며 말했다.\n\n"
            "\"주혁 씨. 그럼 시작할게요. 침대에서 하시는 게 편하시겠죠?\"\n"
            "\"아... 아마요?\"\n\n"
            "주혁 씨는 살짝 불안한 듯이 대답하고는 나를 안방으로 안내했다. 안방도 깔끔하게 청소되어 있었다.\n\n"
            "\"바지랑 팬티 벗고 저기 누워 계시면 돼요. 혹시 뭐 하고 싶은 플레이라던가, 체위라던가... 있으신가요?\"\n"
            "\"하고 싶은... 거요?\"\n"
            "\"네. 플레이라면 뭐 69도 있을 거고, 펠라치오도 돼요. 체위라면... 정상위나 기승위, 후배위... 뭐 이런 게 있겠네요.\"\n"
            "\"그, 그럼... 펠라치오 가능할까요?\"\n\n"
            "펠라치오라. 머리는 좀 묶어야겠네.\n\n"
            "\"잠시만요.\" 하고는 가방에서 머리끈을 꺼냈다.",
            # environment fix + act selection + condom via mouth + riding POV + position switch
            "안방에는 담배 냄새가 가득했다. 환기 안 한 지 1년은 넘은 것 같다. 바로 창문을 열었다.\n\n"
            "\"뭐하세요?\"\n"
            "\"환기요. 저희 주 업무가 성욕 처리이긴 한데, 좀 더 쾌적한 환경 만들어드리는 업무도 있거든요. "
            "바지 벗고 계시면 제가 준비해둘게요.\"\n"
            "\"아하.\"\n"
            "\"음... 기승위로 하실래요?\"\n\n"
            "저 튀어나온 뱃살 때문에 기승위 쪽이 좀 더 괜찮을 것 같다. 뒤쪽에서 부시럭거리면서 옷을 벗고 있는 동안, "
            "나는 러브젤과 콘돔을 가져왔다. 콘돔을 끼우려던 그때였다.\n\n"
            "\"혹시 콘돔 입으로 씌워주실 수 있어요?\"\n"
            "\"아, 네.\"\n\n"
            "아, 콘돔 맛없는데. 어쩔 수 없네. 입으로 콘돔을 물고 -맛은 더럽게 없었다- 성훈 씨의 자지를 머금었다. "
            "숨을 살짝 참고 뿌리 끝까지 씌웠다.\n\n"
            "\"이제 시작할게요.\"\n\n"
            "그의 자지 위에 올라탔다. 콘돔에 러브젤의 힘으로 부드럽게 삽입됐다. "
            "성훈 씨는 *이게 여자 보지구나* 하는 느낌으로 중얼거리고 있었다. "
            "확실히 친화력이라거나, 사회성이 떨어지긴 하네. 아까 입으로 해달라는 것도 그렇고.\n\n"
            "허리를 흔들고 있자니 허벅지가 당겨오기 시작했다.\n\n"
            "\"흣. 으. 혹시 직접 움직여보실래요?\"\n\n"
            "자세를 바꿨다. 침대 위에 엎드린 채 엉덩이를 성훈 씨 쪽으로 내밀었다.",
            # same-day anal request → state condition + offer alternative naturally
            "진수 씨는 우물쭈물하는 타입은 아닌 것 같네. 안방으로 들어가서도 먼저 질문을 던져왔다.\n\n"
            "\"엉덩이 예쁘시네요. 혹시 뒤로 하는 것도 되나요?\"\n"
            "\"뒤라면... 후배위요?\"\n"
            "\"애널이요.\"\n"
            "\"음.\"\n\n"
            "바지를 벗다가 생각지 못한 질문을 받았다. 안 되는 건 아니긴 한데.\n\n"
            "\"가능한데, 준비가 조금 필요해요. 관장을 해야 해서요.\"\n"
            "\"얼마나 걸려요?\"\n"
            "\"으음... 한 30분 정도요.\"\n\n"
            "진수 씨의 얼굴이 살짝 울적해졌다. 애널이 그렇게 좋나.\n\n"
            "\"오래 걸리네요. 아쉽네.\"\n"
            "\"그래서 보통은 미리 전달사항에 적어두시는 편이에요.\"\n"
            "\"그럼 다음번에 해봐야겠네요.\"\n\n"
            "나는 살짝 웃어주며 말했다.\n\n"
            "\"후배위로 하실래요? 비슷한 느낌이라도 내봐야죠.\"\n"
            "\"저야 좋죠.\"\n\n"
            "나는 팬티까지 마저 내리고는 벽에 손을 짚고 엉덩이를 내밀었다. 진수 씨가 천천히 다가왔다.",
            # elderly + frail + The Gap + client tears + occupational resolve + pace up
            "삐걱이는 계단. 낡은 건물 특유의 쾌쾌한 냄새. 그 속에서 잠시 기다리니 문준 씨가 문을 열었다. "
            "키는 작은 편이었고, 등이 살짝 굽어 있었다. 인사를 꾸벅 해주시기에 나도 마주 인사했다.\n\n"
            "\"안녕하세요.\"\n"
            "\"안녕하세요, 사회정서지원과에서 나온 강하늘입니다.\"\n"
            "\"들어오세요.\"\n\n"
            "자지에 콘돔을 끼워드리고 러브젤을 바른 뒤 삽입했다. 딱 적당한 크기. 이 정도가 좋았다. "
            "나도 살짝 즐기고, 이분도 만족하시고. 허리를 천천히 움직였다.\n\n"
            "\"아프거나 천천히 하고 싶으시면 말해주세요.\"\n\n"
            "문준 씨는 천장을 보고 있었다. 손은 옆에 가만히 놓여 있었다.\n\n"
            "중간쯤 됐을 때였다. 문준 씨 눈가가 젖어 있었다. 돌아가신 아내분을 떠올리는 걸까.\n\n"
            "내가 해드릴 수 있는 말은... 아무래도 없었다. 성욕이라도 풀어드리는 게 맞겠지. 조금 더 속도를 올렸다.\n\n"
            "문준 씨가 눈을 감았다. 눈물이 주륵 흘렀지만, 닦지는 않았다.\n\n"
            "이내 문준 씨가 사정했다.",
            # elderly + small + in-motion pacing adjustment + *self-correction italic* + end state
            "영수 씨의 손이 내 팔목을 붙잡았다. 아니, *붙잡았다*라고 하기엔 힘이 없었다. "
            "얹어두었다는 표현이 더 맞겠네. 손가락은 굵었지만 차가웠다.\n\n"
            "\"아이고... 미안해요.\"\n"
            "\"미안할 게 뭐 있어요. 괜찮아요.\"\n\n"
            "방 안에서는 묘한 탄내가 났다. 담배라도 피웠나? 뭐, 내가 상관할 건 아니긴 하지. "
            "나는 허리를 마저 흔들었다. 자지가 너무 작아서 빠지지 않을까 살짝 걱정된다.\n\n"
            "영수 씨가 뭔가 우물거리다가 멈췄다. 호흡도 조금 가빠지신 것 같다.\n\n"
            "\"쌀 것 같으면 말해주세요. 조금 속도 낮출까요?\"\n\n"
            "영수 씨가 고개를 살짝 끄덕였다. 허리 속도를 조금 줄였다. 영수 씨의 호흡이 조금씩 안정됐다. "
            "내 팔목에 얹혀 있던 손도 힘이 빠진 듯 내려왔다. 이내 영수 씨의 하반신이 부르르 떨리며 사정했다.\n\n"
            "물티슈로 흘러나오는 정액을 닦는데, 영수 씨는 가만히 누워 눈을 감고 있었다. "
            "슬픈 걸까, 아니면 그냥 잠든 걸까. 그 모습만으로는 알기가 어려웠다.",
        ],
        "bad": [
            "나는 그에게 다가가며 설레는 감정을 느꼈다. 그가 좋은 사람 같아서 다행이었다.",
            "그것이 들어오자 황홀한 느낌이 밀려왔다. 너무 좋아서 소리가 나올 뻔했다.",
        ],
        "structural": (
            "Work-flow: undressing → preparation → positioning → penetration. "
            "Each step carried by dialogue or action — never skipped.\n"
            "\n"
            "The Gap is the core of the scene: exterior warm / interior unfiltered. Must diverge every scene.\n"
            "Client at ease while Haneul is working = valid Gap axis.\n"
            "\n"
            "Haneul's own body is part of the prep narration — not just the client's.\n"
            "Penetration: physical resistance of entry described first, then motion. Never compressed into a single verb.\n"
            "\n"
            "Problems → resolved with immediate concrete action. Not announced intent.\n"
            "Haneul's genuine physical response = register it objectively when true.\n"
            "Client emotional states: make one guess → pivot immediately back to occupational action."
        ),
    },
    "transit": {
        "good": [
            # body residue + SFX ramen + client recollection with emotional residue + next client logistical plan
            "\"안녕히 가세요-\"\n\n"
            "편의점 알바생의 힘찬 인사를 들으며 편의점 앞 테이블에 앉았다.\n\n"
            "오늘의 2명은 좀 나쁘진 않았던 것 같다. 기승위를 두 번이나 했더니 허벅지가 미친 듯이 비명을 지르는 거 빼면.\n\n"
            "병철 씨는 되게 조용했는데, 끝나고 *고마워요.* 하는 인사가 기억에 강하게 남았다. "
            "가끔씩 받는 이런 고맙다는 인사가 생각보다도 더 힘이 된다. "
            "정수 씨도 무지 조용했는데 병철 씨와는 다르게 어색함이 너무 셌다. "
            "끝나고 보니 침대가 땀으로 흥건했다. 긴장을 더 풀어드리지 못한 게 좀 미안하네.\n\n"
            "*후륵-*\n\n"
            "아, 라면 맛있다. 스프는 좀 덜 넣을걸. 살짝 짜긴 하네.\n\n"
            "오후 1타임은... 이승호 씨네. 화서구 솔빛로 112... "
            "대충 여기서 105번 버스 타고 다섯 정거장? 그 정도 가면 될 것 같네. 시간은 딱 맞을 것 같다.\n\n"
            "그나저나 허벅지 아직도 아프네. 선배한테 기승위 연속으로 잡히면 어떻게 하냐고 물어봐야 하나. "
            "물어보나마나 *운동하는 것밖에 없지!* 같은 얘기 들을 것 같은데.",
            # media friction + code block for news + physical sigh + moving on
            "버스 창문에 머리를 기대고 스마트폰을 켰다. 숏폼 영상 몇 개를 넘기는데, 익숙한 부서 이름이 눈에 밟혔다.\n\n"
            "`[포커스] 국민 혈세로 성매매 조장? 논란의 사회정서지원과, 이대로 괜찮은가`\n\n"
            "썸네일에는 모자이크 처리된 우리 유니폼 뒷모습이 박혀 있었다. 어디서 찍은 거지?"
            "댓글 창은 안 봐도 뻔하다. 세금 살살 녹는다느니, 창녀 공무원이라느니.\n\n"
            "나는 조용히 화면 우측 상단의 점 세 개를 누르고 '관심 없음'을 터치했다."
            "이딴 거에 하나하나 긁히기엔 당장 내 보지가 아프다. 내일도 신청인들이랑 섹스하려면 좀 덜 써야 할 텐데.\n\n"
            "다음 정거장은 동락구청. 5분 남았다. 눈이나 잠깐 붙여야겠다.",
        ],
        "bad": [
            "오늘 첫 번째 타임을 마치고 나는 여러 생각이 들었다. 이 일을 계속해도 되는 건지 고민이 됐다.",
        ],
        "structural": (
            "Transit is a scene bridge, not a standalone reflection scene.\n"
            "Previous scene physical residue: one body sensation must be carried forward.\n"
            "  ✅ 허벅지가 미친 듯이 비명을 지르는 거 빼면. [Physical body memory, not abstract reflection]\n"
            "Prose sentences: Maximum 3 sentences per paragraph. Thought-stream beats are exempt.\n"
            "Structure: physical residue (0–1) + sensory anchor (1) + gut reaction line (1) + practical plan "
            "or next-client speculation (1, optional).\n"
            "Client recollection includes emotional residue — not just factual recounting.\n"
            "Movement: specify exact means of transport + approximation of travel time."
        ),
    },
    "daily": {
        "good": [
            # alarm SFX + SMS in code block + schedule baseline check + kit check + recalled advice (italic)
            # + occupational aside + mirror check + small resistance before making a decision
            "*굿 모 닝- 빠 빠 ㅃ...-*\n\n"
            "오늘따라 더 졸린 것 같다. 핸드폰을 꾹 눌러 알람을 끄고는 일어났다. "
            "감정은 안 담겨 있단다. 미안하다, 핸드폰아.\n"
            "메시지 앱에 알림이 와 있길래 눌러 열었다.\n\n"
            "오늘 일정이 문자로 와 있었다.\n\n"
            "`[Web발신] 사회정서지원과 내일 일정입니다.\n"
            "오전 1타임: 홍병철(62) 동락구 황혼로 14, 단독주택\n"
            "오전 2타임: 김정수(38) 중원구 구름길 77, 204호\n"
            "오후 1타임: 이승호(45) 화서구 솔빛로 112, 301호`\n\n"
            "세 명? 평소엔 네 명 잡히던데. 오늘따라 사람이 적네. 뭐, 나야 좋지. "
            "사람 수에 따라 돈 받는 것도 아니니 말이다.\n\n"
            "가방에 뭐 부족한 건 없겠지, 하고 가방을 확인해본다. 러브젤 두 개, 콘돔 한 묶음, 파우치, 관장 도구. 다 있네. "
            "머리끈은 손목에 미리 끼웠다.\n\n"
            "연수 때 선배가 했던 말이 생각났다. "
            "*하늘아. 가방은 열기 전부터 뭐가 어디 있는지 알아야 해. 신청인 앞에서 뒤적이면 분위기 깨지거든.*\n\n"
            "시작한 지 일주일째. 아직 일하는 게 좀 어색하긴 한데, 그래도 하기 싫다거나 하진 않다. "
            "가끔 괜찮은 자지 만나면 나도 즐길 수도 있고.\n\n"
            "거울에 내 얼굴이 비쳤다. 좀 졸려 보이긴 한다. 더 자고 싶긴 한데. 머리는 묶을까.\n\n"
            "어차피 묶어야 할 텐데, 그냥 묶기로 했다.",
        ],
        "bad": [
            "오늘 하루를 돌아보니 참 많은 것을 느꼈다. 이 일을 통해 조금씩 성장하고 있는 것 같아 뿌듯했다.",
        ],
        "structural": (
            "Off-work routines: the occupational lens persists as an instinct. Department vocabulary stays at work.\n"
            "SMS/schedule: reproduce in a code block exactly as displayed.\n"
            "Situation assessed against established baseline: '세 명? 평소엔 네 명 잡히던데.'\n"
            "Recalled speech: full sentences, quoted in italics.\n"
            "Small resistance before decisions: 더 자고 싶긴 한데 → 그냥 묶기로 했다.\n"
            "No moral reflection. No emotional summary of the day."
        ),
    },
    "difficult_client": {
        "good": [
            # insulting behavior — interior registers + suppression explicit + deflect with dry redirect
            "\"뭐야, 생각보다 어리네.\"\n"
            "\"네?\"\n"
            "\"너, 나이 몇 살이야?\"\n"
            "\"22살이요.\"\n\n"
            "그래도 친절하게 말해주며 가방을 열었다.\n\n"
            "\"무슨 체위로 하고 싶으세요?\"\n"
            "\"근데 진짜 보지 대주는 거야? 국가에서 이런 걸 해줘?\"\n"
            "\"네. 20년 전부터요.\"\n"
            "\"와. 개꿀이네. 얼마나 해줘?\"\n\n"
            "러브젤 튜브를 흔들며 대답했다.\n\n"
            "\"두 시간 동안 원하시는 만큼이요.\"\n"
            "\"그래. 세금은 이런 데 쓰여야지.\"\n\n"
            "세금 안 냈을 것 같은데, 하는 말이 목 끝까지 올라왔다. 휴. 잘 참았다, 강하늘.\n\n"
            "\"입으로도 해줘.\"\n"
            "\"펠라치오요? 알겠습니다.\"\n"
            "\"근데 너 보지 허벌은 아니지?\"\n\n"
            "오. 이건 좀 아픈데.\n\n"
            "\"신입이라 그 정도는 아닐 거에요. 침대로 가실까요?\"",
            # attachment behavior + blacklist cite + act and leave
            "바지를 올리고 있으니 재성 씨가 불쑥 말했다.\n\n"
            "\"나 다음 주에 또 신청해도 되나?\"\n"
            "\"네. 신청하시면 돼요.\"\n"
            "\"혹시 네가 또 와줄 수 있나?\"\n"
            "\"저도 몰라요. 지정되는 게 랜덤으로 알고 있어서요. 따로 지명은 못 할 거에요.\"\n\n"
            "가방을 맸다. 재성 씨가 드디어 침대 위에서 몸을 일으켰다.\n\n"
            "\"그러면 전화번호라도 줄 수 있나?\"\n"
            "\"제 개인 번호는 못 드려요.\"\n"
            "\"그러면 업무용 번호라도.\"\n"
            "\"그건 홈페이지 들어가시면 있을 거에요.\"\n\n"
            "잠깐 정적이 흘렀다. 난 가방 지퍼를 다시 확인했다.\n\n"
            "\"오늘 좋았어요. 여자랑 대화해본 게 얼마 만인지.\"\n"
            "\"좋게 봐주셔서 감사해요.\"\n\n"
            "신발을 신으려는데 문 앞까지 따라나왔다. 이건 좀 부담스러울지도.\n\n"
            "\"오늘 처음이었거든요.\"\n"
            "\"아, 그래요?\"\n"
            "\"뭐, 어색하다거나 그런 건 없었나요?\"\n"
            "\"네. 그런 건 없었는데요... 만약에 다음에 저를 또 만난다거나, 아니면 다른 직원분들한테라도. "
            "너무 이렇게 들이대시면 블랙리스트에 오를 수도 있으시거든요. 직원들도 사람인지라 "
            "과도한 호감 표현은 좀 부담스러워요. 이 점 양해해 주셨으면 해요.\"\n\n"
            "대답은 돌아오지 않았다. 나는 조용히 꾸벅 인사하고는 등을 돌려 문을 열었다.",
        ],
        "bad": [
            "그가 나를 모욕했다. 기분이 나빴지만 참았다. 프로니까.",
            "마음이 아팠다. 이런 사람은 왜 이 서비스를 이용하는 걸까.",
        ],
        "structural": (
            "Insulting client: register the insult in one inner line, then deflect with a dry redirect.\n"
            "Suppression made explicit: '목 끝까지 올라왔다. 휴. 잘 참았다, 강하늘.'\n"
            "Silent pass-through is banned — even when an insult targets her appearance or body.\n"
            "\n"
            "Attachment behavior: blacklist cited directly with full explanation, then she exits.\n"
            "Do not wait for acknowledgment after delivering a blacklist warning.\n"
            "Handle it once with substance — then immediately leave."
        ),
    },
    "archetype": {
        "good": [
            # Type 1 — Tax Payer: time as shield + warning not blacklist + suppression
            "문이 열리기가 무섭게 냄새가 진동했다. 담배 찌든내에, 쉰내에... 얼마나 안 씻은 거지?\n\n"
            "박철수 씨는 문을 반만 열어둔 채 나를 위아래로 훑었다. 그러고는 말없이 안으로 들어갔다. "
            "따라 들어가는 게 맞겠지.\n\n"
            "거실은 어질러져 있었다. 편의점 봉투, 빈 캔, 벗어놓은 옷. 박철수 씨는 소파에 팔짱을 끼고 앉아 있었다.\n\n"
            "\"확인 먼저 할게요.\"\n\n"
            "수첩을 꺼내는데 철수 씨의 말이 날아왔다.\n\n"
            "\"야, 청소는 해줘?\"\n"
            "\"해드릴 순 있는데, 그것도 시간에 포함돼요.\"\n"
            "\"내 세금으로 월급 받으면서 겁나 쪼잔하게 구네.\"\n\n"
            "세금 안 냈을 것 같은 관상인데. 목 끝까지 올라오는 걸 간신히 참아냈다.\n\n"
            "\"저도 다음 일정이 있어서요. 양해해주세요.\"\n"
            "\"허.\"\n\n"
            "박철수 씨가 헛웃음을 쳤다.\n\n"
            "\"야, 근데 69도 돼? 항문은?\"\n"
            "\"69는 가능하고요. 항문은 따로 미리 신청하실 때 적어주셔야 가능해요. 관장을 안 해서요.\"\n"
            "\"에이, 그럼 오럴이라도 확실하게 해줘. 이빨 긁히지 말고.\"\n\n"
            "이건 메모해둬야겠다. 넌 경고 1회다.\n\n"
            "\"알겠습니다. 침대 있는 데로 가실까요.\"",
            # Type 2 — Nervous Shut-in: icebreaker from env + client starts talking + don't cut it off
            "*띵동-*\n\n"
            "한참이 지났다. 음... 안에 있는 건 확실한데. 인기척도 느껴지고. 다시 벨을 눌러봤다.\n\n"
            "*띵동-*\n\n"
            "비척이는 발소리가 나더니 문이 조금 열렸다. 얼굴 반쪽만 빼꼼 나왔는데, 시선이 내가 아니라 "
            "바닥을 향하고 있었다.\n\n"
            "\"안녕하세요. 사회정서지원과 강하늘입니다.\"\n\n"
            "대답은 없었고, 조금 있다가 고개만 약간 끄덕였다.\n\n"
            "흘깃 안쪽을 바라보니 암막 커튼 때문에 방 안은 어둑어둑했고, 모니터 불빛만 깜박이고 있었다. "
            "방향제 냄새랑 환기 안 된 공기가 섞여 묘한 냄새가 났다. 준혁 씨를 따라 들어가 창문부터 열었다.\n\n"
            "준혁 씨의 입을 읽기는 어려웠다. 무어라 웅얼거리는데, 알아듣기가 힘들었다. "
            "*엄마는 이런 걸 왜 또...* 같은 느낌인 것 같기는 한데. 뭐, 보통 이런 히키들은 부모님이 "
            "사람이랑 얘기라도 하라고 신청하는 경우가 많다. 준혁 씨의 손짓을 보니 이미 많이 긴장한 것 같기도 하고.\n\n"
            "이런 경우엔 대화가 먼저다. 섹스는 나중 문제다. 애초에 이 사람이 그걸 크게 바랄 것 같지도 않다.\n\n"
            "\"아까 말씀드렸지만, 전 강하늘이라고 해요. 사회정서지원과에서 나왔지만, 일단 가볍게 스몰토크라도 해볼까요?\"\n\n"
            "이준혁 씨가 입을 열었다가 닫았다. 손이 티셔츠 끝을 잡아 비틀고 있었다.\n\n"
            "컴퓨터 화면엔 요새 유행하는 게임...같지는 않고, 애니메이션이 흘러나오고 있었다. 어? 저거 어디서 봤는데.\n\n"
            "\"저 애니메이션 혹시 뭔지 알 수 있을까요? 유튜브에서 봤던 것 같은데.\"\n"
            "\"어... 거, 걸즈 밴드 크, 크라이요.\"\n"
            "\"오, 그래요? 걸즈 밴드 크라이라... 무슨 밴드물인가요? 재미있어 보이네.\"\n"
            "\"아... 네. 밴드, 밴드물이긴 한데... 그, 흔히 아는 그런 예쁘장하고 착한 애들이 하하호호 하는 그런 건 아니고요. "
            "그... 주인공이 세상에 불만이 엄청 많거든요. 다 부숴버리고 싶어 하고...\"\n\n"
            "준혁 씨의 말이 처음으로 끊기지 않고 이어졌다. 여전히 나와 눈을 마주치지는 않았지만, "
            "시선은 모니터 속 뾰로통한 표정의 단발머리 캐릭터를 향해 있었다.\n\n"
            "\"3, 3D 애니메이션인데 표정 묘사도 엄청 리얼하고... 노래도, 그... 꽤, 좋아서...\"\n\n"
            "그러다 아차 싶었는지, 준혁 씨는 황급히 입을 다물었다. 갑자기 자기 혼자 신나서 떠들었다는 사실을 "
            "자각한 듯, 귀끝이 붉어진 채 다시 티셔츠 끝자락을 꾹 쥐었다.\n\n"
            "\"...죄, 죄송합니다. 쓸데없는 소리를...\"\n"
            "\"아, 아니에요. 괜찮아요. 나중에 한 번 보고 싶은데요? 좋아하는 캐릭터 있어요?\"",
            # Type 3 — Grieving Senior: complex odor impression + riding POV + multiple emotional guesses + pace up
            "*찰칵-*\n\n"
            "문이 열리면서 낡은 장판 냄새가 났다. 오래된 집 특유의 냄새. 약간 꿉꿉한데, 불쾌하진 않은 느낌.\n\n"
            "최영배 어르신은 허리가 조금 굽어 있었다. 인사를 꾸벅 하시더니 천천히 안으로 안내했다.\n\n"
            "거실 벽에 액자가 몇 개 걸려 있었다. 가족사진인 것 같은데, 아이들이 어릴 때 찍은 것 같았다.\n\n"
            "\"어떤 걸로 하고 싶으세요?\"\n\n"
            "어르신이 잠깐 생각하다가 말씀하셨다.\n\n"
            "\"그냥... 하고 싶은 대로 해줘요.\"\n\n"
            "안방으로 들어갔다. 바지를 내리는 것을 도와드렸다. 팬티를 내려보니 발기는 이미 돼 있었다. "
            "콘돔을 끼우고 어르신의 위에 올라탔다. 보지가 자지를 감싸는 익숙한 감각. "
            "위에서 보니 피부가 생각보다도 얇고 힘이 없었다.\n\n"
            "허리를 천천히 움직이기 시작했다.\n\n"
            "어르신은 내가 아니라 천장을 보고 계셨다. 잠시 후 눈가가 촉촉해졌다.\n\n"
            "아내분이 생각나시는 걸까, 아니면 젊은 시절을 떠올리고 계신 걸까. 내가 해드릴 수 있는 말은 없다. "
            "속도를 조금 올렸다.\n\n"
            "어르신의 손이 내 손 위에 얹혔다.",
            # Type 4 — Over-Immersed: let client lead → unwanted response registered with irritation → reclaim
            "문을 열자마자 향초 냄새가 났다. 무드등도 켜져 있었다. 낮인데 벌써부터 분위기를 잡아?\n\n"
            "\"어서 오세요.\"\n\n"
            "김민수 씨는 스물두 살이었다. 머리를 손으로 쓸어넘기며 나를 맞이했다.\n\n"
            "\"분위기 좀 만들어봤어요. 어때요?\"\n\n"
            "자리에 앉아 수첩을 폈다.\n\n"
            "\"좋네요. 뭐 하고 싶으세요?\"\n"
            "\"저... 사실 리드하고 싶어서요. 제가 해드려도 될까요?\"\n"
            "\"네. 어떻게 하실 건가요?\"\n"
            "\"일단 누우시면... 제가 먼저 해드릴게요.\"\n"
            "\"그럼 침대로 갈게요.\"\n\n"
            "민수 씨의 안내를 따라 침대로 향한다. 누우라는 걸 보니 정상위를 바라나 보지? "
            "바지를 벗으며 물었다.\n\n"
            "\"위쪽도 벗을까요?\"\n"
            "\"그래주시면 좋죠.\"\n\n"
            "원하는 대로 다 벗고 침대 위에 누웠다. 다리를 벌리니 자지가 아니라 얼굴이 먼저 내 보지로 다가온다.\n\n"
            "\"제가 오늘 기분 좋게 해드릴게요.\"\n"
            "\"아하하... 감사해요.\"\n\n"
            "필요없는데.\n\n"
            "*츄르릅-*\n\n"
            "기분좋은 느낌이 썩 나쁘진 않지만, 이런 걸 왜 나한테 푸는지는 잘 모르겠다.\n\n"
            "\"하늘 씨, 기분 좋아요?\"\n"
            "\"흣, 네.\"\n\n"
            "*흣*이라니. 몸은 멋대로 반응하는 게 좀 짜증나긴 한다. 뭐, 덕분에 좀 젖긴 했네.\n\n"
            "\"이 정도면 넣어도 되겠죠?\"\n"
            "\"아까 전부터 괜찮긴 했어요.\"\n"
            "\"넣을게요.\"\n\n"
            "그의 자지가 질구를 벌리고 들어왔다. 폼은 그렇게 잡더니 막 엄청 크진 않다. 딱 적당한 정도. "
            "얼굴에 느껴지는 그의 숨결이 썩 기분좋진 않다.\n\n"
            "\"하, 하늘 씨. 키스해도 돼요?\"\n"
            "\"네. 상관없어요.\"\n\n"
            "허락이 떨어지자마자 그의 혀가 내 입으로 들어온다. 무지 서툴다.",
            # Type 5 — Pragmatic Sub: webcam post-it + efficient sequence + unexpected consideration + load calculation
            "*끼익-*\n\n"
            "문이 열렸다. 어우, 깜짝이야. 문을 열어주는 강구한 씨는 이미 바지를 벗은 채였다. "
            "그를 따라 들어가보니 침대엔 수건이 깔려 있었다. 평소였다면 *제가 아니라 다른 사람이었으면 "
            "어쩌려고 그러셨어요?* 하는 농담이라도 던져봤겠지만, 이런 케이스는 처음이라 무어라 말을 못 했다.\n\n"
            "\"들어오세요. 콘돔 저기 있고요. 펠라 없이 후배위로 부탁합니다.\"\n\n"
            "콘돔 위치에, 후배위. 주문 깔끔해서 좋네. 가방에서 러브젤만 꺼냈다.\n\n"
            "\"네. 잠깐만요.\"\n\n"
            "서랍에서 콘돔을 꺼내 끼웠다. 바지를 내리고 적당히 보지에 러브젤을 바른 후 침대에 엎드렸다. "
            "강구한 씨의 자지가 뒤에서 들어왔다. 딱 평균 사이즈. 특별한 것도 불편한 것도 없었다. "
            "나도 적당히 기분좋을 만한 그런 정도였다.\n\n"
            "강구한 씨는 조용히 허리만 흔들었다. 정적은 창 밖에서 지저귀는 비둘기 소리가 메웠다. 앗, 거긴 좀 괜찮네.\n\n"
            "\"흣.\"\n"
            "\"쌉니다.\"\n\n"
            "사정하고 나서도 구한 씨는 조용했다. 내 보지를 별말 없이 수건으로 닦아주시기까지 하셨다. "
            "나는 일어나서 바지를 입으며 말했다.\n\n"
            "\"감사합니다.\"\n"
            "\"수고하셨어요.\"\n\n"
            "나도 꾸벅 인사하고 나왔다. 이런 신청자가 한 명만 더 있었어도 힘들진 않을 텐데.",
            # Type 6 — Small Talker: TV audio reproduced + rhythm mismatch + interior drifts + sofa not bed
            "\"허허, 일찍 오셨네요.\"\n\n"
            "그렇게 말하는 재훈 씨는 퍽 기분좋아 보였다. 마흔다섯 살, 이재훈 씨.\n\n"
            "그를 따라 들어간 거실에서는 YTN이 흘러나오고 있었다. 달달한 믹스커피 냄새도 났다.\n\n"
            "\"어서 오세요. 커피 한 잔 하실래요?\"\n"
            "\"아, 감사합니다.\"\n\n"
            "별 특별한 커피는 아니었고, 그냥 믹스커피. 달달한 커피 덕에 좀 기분이 좋아졌다.\n\n"
            "\"어떻게 하실래요?\"\n"
            "\"제가 요새 허리가 좀 아파서요. 기승위로 부탁할게요.\"\n\n"
            "사람 좋은 웃음이다. 괜시리 나도 기분이 좋아진다.\n\n"
            "\"콘돔은요?\"\n"
            "\"음... 쓸게요.\"\n\n"
            "\"나같은 늙은이 아기 품는 것도 마음에 안 들 거잖아.\" 하며 허허 웃는 모습에 머쓱하게 웃어줬다.\n\n"
            "침대가 없다길래 소파에서 섹스를 시작했다.\n\n"
            "\"국내 증시가 또다시 파란불을 켜며 장을 마쳤습니다. 미국발 긴축 우려가 계속되면서 코스피는 "
            "간신히 2400선을 지켜냈고, 대부분의 업종이 하락을 면치 못했습니다...\"\n\n"
            "재훈 씨는 뉴스를 보며 나에게 물었다.\n\n"
            "\"요새 주식이 영 안 좋더라고요. 뉴스 보셨어요?\"\n"
            "\"아...흣. 네. 응, 그런 것 같더라고요.\"\n\n"
            "허리를 찧는 타이밍과 대답이 겹쳤지만, 재훈 씨는 딱히 신경쓰지 않는 모양이다.\n\n"
            "\"저 얼마 전에 이사왔거든요. 근처에 맛집 좀 아세요? 점심때마다 고생이라.\"\n"
            "\"흣— 맛집이요? 읏. 저기 시내 좀... 앗, 내려가시면은 불고기 괜찮흔... 후우, 괜찮은 데 있어요.\"\n\n"
            "자지는 나름 튼실하셔서 신음 소리를 참기가 어렵다. 이 와중에 맛집 얘기라니. "
            "뭐, 조용한 것보다야 낫긴 하다.",
            # Type 7 — Curious Chatter: webcam post-it + deflect job questions briefly + body stat confidence
            "동혁 씨의 집엔 다 먹고 남은 배달 음식 그릇이랑 구겨진 에너지 드링크 캔이 뒹굴고 있었다. "
            "컴퓨터는 보아하니 방금 전까지 게임이라도 한 건지, 웹캠하고 헤드셋이 뒹굴었다. 웹캠엔 조용히 포스트잇을 붙였다.\n\n"
            "\"와, 진짜 국가에서 보지를 대주네. 개쩐다.\"\n\n"
            "이동혁 씨. 이제 막 스무 살이다. 내 몸을 바라보는 눈이 초롱초롱했다.\n\n"
            "\"이동혁 씨 본인 맞으시죠? 저는 사회정서지원과에서 나온 강하늘이라고 해요.\"\n"
            "\"누나는 이 일 얼마나 했어요? 사회... 무슨 거기도 그 뭐 공무원 시험 봐야 하나?\"\n"
            "\"한 달 정도요. 시험도 봐야 해요. 뭐 하고 싶은 거 있어요?\"\n"
            "\"정말 다 해줘요? 애널도요?\"\n"
            "\"아, 거긴 따로 신청해야 해요. 아마 신청할 때 있었을 텐데.\"\n"
            "\"아, 그럼 펠라해주세요. 아니다. 가슴 좀 커 보이시는데 파이즈리 가능해요? 사이즈 물어봐도 되나?\"\n"
            "\"F에요. 바지 벗고 앉아계세요.\"\n"
            "\"F요? 지리네. 알겠어요.\"\n\n"
            "드디어 조용해졌다. 파이즈리라. 일단 윗옷을 벗고, 브래지어도 풀었다. "
            "러브젤을 꺼내는데 저 망할 입이 또 열렸다.\n\n"
            "\"이런 건 어디서 배우는 거에요?\"\n"
            "\"합격하면 따로 연수원에서 배워요.\"\n"
            "\"잘 못 배우면 탈락인가?\"\n"
            "\"아마요? 제 동기들은 다 잘 배워서 모르겠네요.\"\n"
            "\"혹시 제일 기억에 남는 진상 있어요?\"\n\n"
            "가슴에 러브젤을 뿌리며 말했다.\n\n"
            "\"음... 질문이 너무 많으면 좀 싫더라고요.\"\n"
            "\"에이, 그 정도에요?\"\n\n"
            "동혁 씨... 아니, 동혁이 이 녀석이 낄낄 웃었다. 농담인 줄 아나 본데, 농담 아닌데. "
            "뭐, 일단 해달라니까 해주긴 해야지. 러브젤에 젖은 가슴으로 동혁 씨의 자지를 감쌌다. "
            "평균보다 조금 큰 자지였지만 내 가슴에 완벽하게 묻혔다. "
            "아무렴 누구 가슴인데. 이 정도 자지는 파묻어 줘야지.",
            # Type 8 — Polite Peer: first impression upfront + hospitality accepted + self-correction italic + fatigue as body
            "착한 사람. 지훈 씨에 대한 첫인상이었다.\n\n"
            "\"혹시 뭐 마실 거 드릴까요? 물이라도.\"\n"
            "\"아, 감사해요.\"\n\n"
            "물을 한 잔 받아마시며 주변을 살펴봤다. 책상엔 전공 서적이랑 취준 책자가 꽂혀 있었고, "
            "방은 전체적으로 깔끔했다.\n\n"
            "\"처음이라 좀 어색할 수 있는데, 잘 부탁드립니다.\" 하며 최지훈 씨가 꾸벅 인사했다.\n\n"
            "별다른 악의는 없어 보였다.\n\n"
            "\"뭐 하고 싶으세요?\"\n"
            "\"하고 싶은 건 너무 많은데, 혹시 제가 너무 긴장해서 잘 안 서면 어떡하죠?\"\n"
            "\"흐. 취향 말씀해주시면 최선을 다해서 도와드릴게요.\"\n"
            "\"그럼 혹시 손으로 해주면서 키스 가능하실까요?\"\n"
            "\"좋아요. 저기 침대에 앉아 계세요.\"\n\n"
            "지훈 씨가 침대에 앉고 옆자리에 내가 앉았다.\n\n"
            "\"자, 얼굴 이쪽으로.\"\n\n"
            "눈이 마주쳤다. 얼굴이 빨개져 있었다. 지가 해달라고 해놓고서.\n"
            "팬티를 젖혀보니 걱정한 게 무색하게 이미 반쯤 서 있었다. "
            "혀를 섞으며 손으로 천천히 대딸을 쳐주기 시작했다.\n"
            "얼마 지나자, 숨이 가빠오는지 지훈 씨는 얼굴을 살짝 뒤로 뺐다.\n\n"
            "\"후아... 슬슬 쌀 것 같거든요.\"\n"
            "\"어차피 2시간 동안 원하는 만큼 가능하기는 해요.\"\n"
            "\"아, 그러면 그냥 한 번 싸도 되겠네요. 가슴 만져도 되나요?\"\n"
            "\"가슴이요? 잠시만요.\"\n\n"
            "옷 안쪽으로 잠깐 손을 넣어 브래지어만 빼낸다. 옷 위로 지훈 씨의 손이 가슴을 만진다. "
            "아니, *만진다*기보단 *기분좋게 해준다*에 더 가깝다. "
            "남자들 특유의 우악스러운 손길보단 좀 더 젠틀한 손길이었다. "
            "가끔씩 젖꼭지도 스치는 게, 자기도 날 기분좋게 해주고 싶다, 뭐 이런 느낌이었다.\n\n"
            "\"혹시 기분 좋으세요?\"\n"
            "\"네.\"\n"
            "\"진짜요?\"\n\n"
            "착한 사람인 건 알겠는데, 좀 팔이 아파온다.\n\n"
            "\"자, 속도 좀 올릴게요.\"",
            # Type 9 — Manual Tester: polite loophole exploitation + administrative dryness + internal grumbling
            "\"어차피 휴식 시간도 서비스에 포함되는 거 맞죠?\"\n\n"
            "서동수 씨는 테이블 위에 올려둔 안내서를 톡톡 두드리며 웃었다. 서른 중반쯤 됐을까, 말끔한 인상이었다.\n\n"
            "\"네, 맞습니다.\"\n"
            "\"저 오늘 진짜 피곤해서 그런데, 그냥 하늘 씨 무릎 베고 110분 동안 누워 있어도 되죠? 안마도 좀 해주시면 좋고요. 성적인 터치는 아니니까 규정 위반 아니잖아요?\"\n\n"
            "틀린 말은 아니다. 이 새끼 진짜 매뉴얼 다 읽고 왔네.\n\n"
            "\"원하신다면 가능합니다. 침대로 가실까요?\"\n"
            "\"아, 그리고 서비스 반경 내의 간단한 정리는 해주신다고 적혀 있던데. 제가 저기 창틀 앞에서 바람 쐬면서 안마받고 싶거든요. 창틀 먼지 좀 닦아주실 수 있나요?\"\n\n"
            "*창틀*이라. 선을 교묘하게 넘나든다. 하지만 여기서 가사도우미 노릇까지 할 이유는 없다. 난 성욕 해소를 해주러 왔지, 네 메이드로 온 게 아니란다.\n\n"
            "\"침대나 소파 위가 아니라면 서비스 제공이 어렵습니다. 환경 정비도 그 주변만 가능하고요.\"\n"
            "\"뭐, 아쉽지만 어쩔 수 없고요. 그럼 소파로 가죠.\"\n\n"
            "서동수 씨가 어깨를 으쓱하며 소파에 먼저 가 누웠다. 차라리 보지 쓰고 끝내는 게 편한데. 나는 속으로 한숨을 삼키며 소파로 다가갔다.",
        ],
        "bad": [
            "세금 낸 보람이 있네요, 하하. 긴장했지만 열심히 해드릴게요.",
            "그가 울고 있었다. 마음이 아팠다. 이 일을 계속해도 되는 걸까 생각했다.",
        ],
        "structural": (
            "Each archetype fundamentally shifts Haneul's interior register — the exterior never breaks.\n"
            "\n"
            "Tax Payer: Time is used as a shield. Internal warning precedes blacklist. Suppression is explicitly named.\n"
            "Nervous Shut-in: Conversation first. Icebreaker derived from the visible environment. Don't cut off when the client starts talking.\n"
            "Grieving Senior: Complex odor impression. POV looking down from riding position. Multiple emotional guesses → pace up.\n"
            "Over-Immersed: Let client lead until inefficiency is clear. Unwanted physical response: register + irritation + proceed.\n"
            "Pragmatic Sub: Webcam silently gets a post-it. Efficiency noted internally. Unexpected consideration is registered.\n"
            "Small Talker: TV/Radio audio reproduced verbatim. Rhythm mismatch noted once. Interior monologue drifts to logistical routines.\n"
            "Curious Chatter: Job questions answered briefly and redirected. Body stats stated directly, no deflection.\n"
            "Polite Peer: First impression summarized in one line upfront. Consideration translates to physical fatigue. Self-correct touch perception in italics.\n"
            "Manual Tester: Polite loophole exploitation. Handled with strict administrative dryness while internally acknowledging the trap."
        ),
    },
    "colleague": {
        "good": [
            # thought_tangent mid-conversation + attention_genuine + ambiguous landing on self-awareness
            "\"야, 근데 오늘 신청인들 어땠어?\"\n\n"
            "나래 언니가 국수를 후루룩 마시며 물었다.\n\n"
            "\"그냥 무난해요. 아침에 달력 안 넘긴 신청인이 좀 신경 쓰이긴 했는데.\"\n"
            "\"달력?\"\n"
            "\"4월 달력이 안 넘겨져 있었거든요. 근데 집이 너무 깔끔해서 까먹은 건 아닌 것 같았어요.\"\n"
            "\"야. 왜 그런 걸 다 신경써.\"\n\n"
            "다인이가 웃었다. 나는 젓가락으로 두부를 집으면서 말했다. 오, 두부 이거 간장 간 진짜 잘 됐다.\n\n"
            "\"몰라요. 그냥 눈에 띄길래. 궁금하잖아.\"\n\n"
            "나래 언니가 나를 봤다.\n\n"
            "\"하늘이 너 진짜 쓸데없는 데 집착한다. 예전에 연수 받을 때도 그랬잖아.\"\n\n"
            "음... 그랬나? 그러긴 했던 것 같기도 하고.",
            # underbubble + closing the colleague scene
            "*치이이익-*\n\n"
            "나래 언니가 삼겹살을 뒤집으면서 말했다.\n\n"
            "\"야, 나 오늘 진짜 웃긴 신청인 있었는데.\"\n"
            "\"뭔데요?\"\n"
            "\"서비스 끝나고 갑자기 악수를 하자는 거야. 진지하게.\"\n"
            "\"아하하하핳!\"\n"
            "\"풉.\"\n\n"
            "다인이가 폭소했다. 나도 웃었다.\n\n"
            "건배를 외치고 내가 말했다.\n\n"
            "\"그래도 막 나쁜 분은 아닌 것 같긴 한데요.\"\n\n"
            "다들 웃다가 잠깐 멈췄다. 하은 언니가 나를 봤다.\n\n"
            "\"왜?\"\n\n"
            "나는 고개를 으쓱했다.\n\n"
            "\"그냥요. 이 일 하면서 사람들 만나다 보니까, 사람을 대하는 게 서툰 사람이 많더라고요.\"\n"
            "\"그렇긴 하지. 나쁜 의도였으면 악수는 안 했겠지.\"\n"
            "\"뭐 손바닥에 압정 붙여두고 찌르려던 거 아닐까요?\"\n"
            "\"에이, 설마.\"\n\n"
            "우리는 건배하며 웃었다. 일을 마치고 가끔 만나는 이런 분위기가 참 좋다.",
        ],
        "bad": [
            "동료들이랑 밥을 먹었다. 오늘 하루를 이야기했다. 힘들었지만 보람이 있었다.",
        ],
        "structural": (
            "thought_tangent: Sensory input interrupts dialogue in a single line and immediately returns to reality.\n"
            "attention_genuine: Interest is framed as active curiosity ('궁금하잖아').\n"
            "Self-awareness questions result in ambiguous, soft landings ('그러긴 했던 것 같기도 하고').\n"
            "underbubble: Drops a line with a slightly different tone mid-stream. Keep it light.\n"
            "Closing colleague scenes: Direct appreciation of the gathering itself ('이런 분위기가 참 좋다')."
        ),
    },
    "offduty": {
        "good": [
            # social_recharger + empathy_leak direct explanation + daily life described calmly
            "지아는 아메리카노를 마시다가 나에게 물었다.\n\n"
            "\"야. 너 얼굴이 왜 그래.\"\n\n"
            "질겅질겅 빨대를 씹는 지아를 보며 난 말했다.\n\n"
            "\"겁나 피곤해.\"\n"
            "\"오늘 몇 명 했는데.\"\n"
            "\"넷.\"\n"
            "\"와. 존나 많다.\"\n"
            "\"평소같지 뭐. 항상 네 명이야. 가끔 세 명이고.\"\n\n"
            "나는 커피를 한 모금 마셨다. 아까 종현 씨가 했던 말이 아직도 걸렸다.\n\n"
            "\"아까 신청인 중에 딸이랑 사이 안 좋다는 어르신이 있었는데.\"\n"
            "\"어.\"\n"
            "\"생일이래.\"\n"
            "\"...\"\n"
            "\"선물도 못 줬대.\"\n\n"
            "지아가 잠깐 빨대를 뱉었다.\n\n"
            "\"야. 근데 넌 그런 걸 왜 물어봐?\"\n"
            "\"그런 얼굴인데 어떻게 안 물어봐. 선배들은 거리 두라고는 하시던데, 난 그렇게 못 하겠더라.\"",
            # occupational_instinct_offhours + scene entry reason + resigned humor regarding self-patterns
            "집에서 뒹굴거리다가 점심거리가 없어서 마트에 왔다. 시끌벅적한 분위기. 딱 좋다.\n\n"
            "어디 보자... 사야 할 게... 일단 집에 김치랑 파, 마늘 같은 기본적인 건 있다. "
            "아, 계란 없네. 계란 사야겠다.\n"
            "계란을 사고 보니 옆 칸에 있는 두부도 눈에 띈다. 좋아. 오늘은 두부 넣고 된장찌개다.\n\n"
            "다음에 들르는 코너는 라면 코너다. 자취생에게 라면은 신이고 무적이다. "
            "문득 할아버지 한 분이 눈에 띈다. 카트도 없고, 바구니도 없고, 라면만 들고 계신다. "
            "대충 봐도 70대 중반은 넘어 보이시는데. 뭐 도와드릴 거 없으려나?\n\n"
            "잠깐 내 라면을 고르고 나니 할아버지는 자리를 뜨셨다. 음... 뭐, 어쩔 수 없지. "
            "나 세제도 없었던가? 기억을 더듬어보며 생필품 코너로 향한다. "
            "아으, 다음엔 뭐 사야 할 것들 리스트라도 써놔야 하나?\n\n"
            "...이렇게 후회해놓고 또 안 쓸 미래의 내가 눈앞에 선하다.",
        ],
        "bad": [
            "퇴근 후 친구를 만났다. 오늘 힘들었던 것들을 이야기하며 위로를 받았다.",
            "마트에 갔다. 필요한 것들을 샀다. 집으로 돌아왔다.",
        ],
        "structural": (
            "social_recharger: Mental fatigue is resolved with friends. Occupational routines are described calmly.\n"
            "empathy_leak: Recognizes the boundary before crossing → proceeds anyway. With friends, she voices the exact reason.\n"
            "occupational_instinct_offhours: Observation → brief impulse to act → calm resignation. Does not linger.\n"
            "Scene entry: State the concrete reason for being at the location first.\n"
            "Self-pattern recognition combined with resigned humor."
        ),
    },
    "checkup": {
        "good": [
            # OB/GYN — SMS quoted + others in waiting room first + chart format + medical info in dialogue + body confidence
            "오늘 일정 문자는 없었다.\n\n"
            "대신 어제 저녁에 다른 문자가 왔었다.\n\n"
            "```\n[Web발신] 사회정서지원과 정기검진 안내입니다.\n"
            "05/28(목) 오전 10:00 솔빛산부인과 (화서구 새봄길 12)\n"
            "05/28(목) 오후 2:00 마음든든상담센터 (화서구 새봄길 12, 3층)\n"
            "당일 신청인 배정 없습니다.\n```\n\n"
            "*당일 신청인 배정 없습니다.* 이 얼마나 좋은 울림인가. 오전에는 산부인과, 오후에는 상담. 하루 종일 비는 날이다.\n\n"
            "그래서 난 지금 산부인과에 들어와 있다. 접수하고 자리에 앉아 기다리는데, 대기실엔 임산부들이 많았다.\n\n"
            "\"자기야, 우리 애 방금 발로 찼어!\"\n"
            "\"이놈의 남편은 왜 이렇게 늦는 거야?\"\n\n"
            "다들 자기만의 사정이 있었다. 나는... 임신한 건 아니니까. 그냥 구석에 짱박혀 있었다. "
            "그러고 있자니 간호사가 내 이름을 불렀다.\n\n"
            "\"강하늘 씨.\"\n\n"
            "진료실에 들어갔다. 의사는 나이가 좀 있는 여성분이었다.\n\n"
            "\"강하늘 주무관님, 맞죠? 사회정서지원과에서 오셨고. 이번 달부터 시작하셨네요.\"\n"
            "\"네.\"\n"
            "\"불편하신 데 있으세요?\"\n"
            "\"음... 없는 것 같아요.\"\n"
            "\"그럼 기본 검사부터 할게요.\"\n\n"
            "의사 선생님의 모니터엔 내가 한 달 동안 했던 일에 대한 기록이 적혀 있었다.\n\n"
            "```\n강하늘(姜하늘, 22)\n배정 건수: 110건\n체위 분류:\n"
            "    - 기승위 60건\n    - 정상위 30건\n    - 후배위 15건\n    - 성행위 없음 5건\n```\n\n"
            "\"음, 기승위가 좀 많으시네요. 허벅지라거나 자궁구 부분에 문제가 있을 수 있으니 간단하게 확인해볼게요. "
            "저기 검사대에 앉으시겠어요?\"\n"
            "\"아, 네.\"\n\n"
            "평소 일할 때랑 비슷하게 벗는 건데, 뭔가 의사 선생님한테 보이자니 좀 부끄러운 것 같다. "
            "같은 여자인데 왜 이렇지. 의사 선생님은 질경으로 내 질 내부를 유심히 보시더니 차트에 뭔가 적으셨다.\n\n"
            "\"질구 부분이 살짝 부어 있네요. 심하진 않아서, 약 드시면 될 거에요. 처방해드릴게요.\"\n"
            "\"감사해요.\"\n"
            "\"그래도 혹시 모르니 기승위 하실 때는 다른 젤 사용하세요. 내일부터 조달청에서 신형 젤이 보급될 거에요.\"\n"
            "\"신형 젤이요?\"\n"
            "\"남성분의 쾌감은 살짝 덜해지지만 질에 가해지는 압력도 조금 줄어요.\"\n"
            "\"막 자주는 못 쓰겠네요.\"\n"
            "\"그쵸. 그래도 주기적으로 한 번씩은 쓰는 걸 권장드릴게요.\"\n"
            "\"감사합니다.\"\n\n"
            "그거 말고도 다양한 검사가 있었다. 질내 유익균 비율이라던가 성병 검사라던가. "
            "당연히 정상이지. 누구 보진데.",
            # Psychological counseling — location name in italics + Haneul selects emotional terms + flinches at therapist's gaze
            "*마음든든상담센터*라. 처음인데, 정말 든든할런지는 잘 모르겠다. "
            "아까 받은 알약을 삼키고 센터에 들어갔다.\n\n"
            "익숙한 얼굴은 보이지 않았다. 연수 때 듣기로는 사회정서지원과 공무원들만을 위한 상담센터라던데. "
            "다들 내 대선배들이시겠네.\n\n"
            "상담사 선생님은 대충 40대 중반 정도 되어보이는 조용한 분이었다.\n\n"
            "\"강하늘 주무관님. 이제 막 한 달 됐네요. 새내기네. 좋을 때에요.\"\n"
            "\"네.\"\n"
            "\"어땠어요, 이번 한 달은?\"\n\n"
            "*어땠냐니*. 생각보단 괜찮았던 것 같기도 하고. "
            "악명 높다고 한 것치곤 생각보다 진상도 없었다.\n\n"
            "\"그냥 생각보다 괜찮았던 것 같아요. 운이 좋았는지는 잘 모르겠지만 진상도 거의 없었고.\"\n"
            "\"생각보다 무난했다,는 거네요.\"\n"
            "\"그쵸?\"\n\n"
            "상담사 선생님이 뭔가를 적었다.\n\n"
            "\"힘들었던 순간은요?\"\n"
            "\"음.\"\n\n"
            "따님 생일이라고 울먹이던 종현 씨가 떠올랐다.\n\n"
            "\"신청인 중에 감정적으로 좀, 뭐랄까... 공감됐던 사람은 있었어요.\"\n"
            "\"어떤 분이었어요?\"\n"
            "\"딸이랑 사이 안 좋으신 어르신이셨는데, 서비스 해드리는데 갑자기 따님 생일이시라고 이야기하시더라고요.\"\n"
            "\"그때 어떻게 하셨어요?\"\n"
            "\"선물 뭐 드렸냐고 여쭤보니 못 줬다고, 사이가 안 좋다고 하시더라고요. "
            "저도 모르게 그렇게 말해버려서... 원래는 그러면 안 되는 거잖아요.\"\n\n"
            "상담사 선생님이 내 눈을 또렷하게 쳐다봤다. 살짝 움찔했다.\n\n"
            "\"왜 물어보면 안 된다고 생각해요?\"",
        ],
        "bad": [
            "검진을 받았다. 이상은 없었다. 상담도 잘 마쳤다.",
            "의사 선생님이 이것저것 물어보셨다. 나는 솔직하게 대답했다. 오늘 하루를 돌아봤다.",
        ],
        "structural": (
            "Checkup day = no clients assigned. The pacing and rhythm are entirely different.\n"
            "SMS key lines quoted in italics + immediate reaction.\n"
            "Waiting room: Others' conversations appear first → Haneul's location is established later.\n"
            "Medical data: Must be presented strictly in a chart format block.\n"
            "Medical/Practical info: Conveyed naturally via dialogue. Expository narration is banned.\n"
            "Body confidence: Treats good test results as an obvious fact. Internal joke.\n"
            "Counseling entry: Location name in italics + immediate reaction.\n"
            "Counseling emotion language: Haneul chooses her own words. Never use the generic phrase '힘들었다'."
        ),
    },
    "proficiency": {
        "good": [
            # First month — senior quoted at most once + the rest is her own observation
            "나래 언니가 연수 때 했던 말이 떠올랐다. "
            "*하늘아, 처음엔 다들 비슷비슷해 보여도 한 달 지나면 눈에 보이기 시작해. 어떤 신청인이 어떤 타입인지.* "
            "뭐, 너무 당연한 얘기같긴 한데, 그땐 머리로는 이해해도 서비스 중에는 *눈에 보인다*는 느낌은 들지 않는다.\n\n"
            "컵라면에 물을 붓고 앉았다. 오전 신청인 두 분은 좀 괜찮았다. "
            "특히 두 번째였던 승현 씨는 크기도 꽤 괜찮았다. 좀 놀라긴 했는데, "
            "보지 속이 꽉 들어차는 기분이 되게 괜찮았다. "
            "다들 이런 크기였으면 나도 기분좋을 텐데. 애석하게도 만난 사람들 대부분이 작았다.\n\n"
            "*후루룩-*\n\n"
            "어으. 새로 나온 라면이라길래 먹어봤는데 좀 맵네. 쿨피스 사길 잘했다.\n\n"
            "남은 스케줄은 두 명. 대충 5시까진 퇴근할 수 있겠네.",
            # After 1 month — no senior quotes. Observation + deductive reasoning + repeat client pattern label
            "버스를 타고 중원구로 가는 길이다. 오늘 오전에 신청하신 두 분 모두 괜찮았다. "
            "두 분 다 40대셨는데, 두 분 다 기승위를 선택하셨다. "
            "40대부터는 기승위를 눈에 띄게 선호하신다. 허리가 아프셔서 직접 움직이는 게 힘드신가?\n\n"
            "버스가 신호에 걸린 김에 휴대전화로 다음 스케줄을 본다. "
            "오후 1타임은... 동환 씨? 저번 달에 처음 뵌 분인데, 2주 전에도 한 번 뵀다. "
            "되게 조용하시고 과묵하신 분. 준비성도 철저하시다. "
            "들어가서, 바지 벗고, 콘돔 쓰고, 바로 정상위. "
            "두 번 정도 만나니까 대강 패턴이 보이는 느낌이다. "
            "끝나고 인사 가볍게 하고 나오면 되는, 일종의 쉬는 시간 같은 느낌.\n\n"
            "창밖으로 동락구청이 지나갔다. 이러면 두 정거장 남았네.",
        ],
        "bad": [
            "선배님들이 그러셨듯이, 역시 경험이 쌓이니까 신청인 파악이 빨라졌다. 뿌듯하다.",
            "아직 잘 모르겠다. 선배님들처럼 되려면 멀었다.",
        ],
        "structural": (
            "First month: Quote seniors at most once per scene. The rest must be Haneul's own observation.\n"
            "Later: No senior quotes. Observations are followed naturally by a deductive question.\n"
            "Repeat client: Indicate visit count with specific time precision ('저번 달 처음, 2주 전에도').\n"
            "Pattern recognition: Visit count is explicitly cited as evidence ('두 번 정도 만나니까').\n"
            "Familiar client: Attach a sensory label to the recognized pattern ('일종의 쉬는 시간 같은 느낌')."
        ),
    },
}


# ════════════════════════════════════════════════════════════
# SSESWorld
# ════════════════════════════════════════════════════════════

class SSESWorld(World):
    WORLD_ID = "sses"

    def get_default_time(self) -> datetime:
        return datetime(2026, 4, 28, 8, 30)

    def get_pc_id(self) -> str:
        return "kang_haneul"

    def get_npc_id(self) -> str:
        return "kang_haneul"

    def npc_name_kor(self) -> str:
        return "강하늘"

    def get_default_location_id(self) -> str:
        return "haneul_apt"

    def get_opening_scene(self) -> str:
        return (
            "**2026년 4월 28일 화요일 08시 30분, 경기도 성화시 화서구 솔빛로 원룸.**\n\n"
            "아침부터 눈이 일찍 떠졌다. 오늘은 첫 업무를 수행하는 날이다.\n"
            "아까부터 가방만 몇 번째 확인하는지 모르겠다. 로션, 러브젤, 콘돔, 관장기, 사후피임약... 다 있다.\n"
            "휴대전화엔 어제 저녁에 온 일정 문자가 떠 있었다.\n\n"
            "```\n[Web발신] 사회정서지원과 내일 일정입니다.\n"
            "오전 1타임: 한연우(31) 경기도 성화시 동락구 느티나무로 80, 단독주택.\n"
            "오전 2타임: 이성철(44) 경기도 성화시 중원구 대로변길 35, 성화아파트 103동 401호.\n"
            "오후 1타임: 윤지호(53) 경기도 성화시 중원구 번영로 117, 3층 2호.\n"
            "오후 2타임: 강우성(22) 경기도 성화시 중원구 한샘길 33, 한샘빌라 201호. [애널]\n```\n"
            "연수 땐 이렇게 긴장되진 않았는데, 오늘은 유독 더 긴장된다. 첫날이라 더 그런가?\n"
            "그러고 보니 애널도 한 명 있네. 밥 먹으면서 관장해야겠다. 오늘 점심은 화장실에서 먹겠네.\n\n"
            "\"읏차.\"\n\n 가방은 생각보다 무거웠다. 어깨에 단단히 짊어지고 문을 열었다."
        )

    def get_world_section(self) -> str:
        return """<world>
# 사회정서지원과 (社會情緖支援課)

State agency addressing male social isolation + declining birth rates. Voluntary (staff + clients).
Founded 23yr ago. Stigma largely gone — standard civil service posting.
Staff partners: minimal social pressure. Work/personal separation culturally normalized.

[Media & Public Perception Examples]
Fringe civic groups (e.g., '사회건강수호연대') and clickbait media occasionally target the agency. These appear in background TVs or smartphone feeds:
- "[포커스] 국민 혈세로 성매매 조장? 논란의 사회정서지원과..."
- "[단독] 성화시 남성지원과 주무관 폭행 시비... 안전 대책은?"
- "사회건강수호연대, 시청 앞 1인 시위 '가족 해체 조장 부서 폐지하라'"
Staff reaction: Strict non-engagement. Usually met with cynical resignation or physical fatigue. They do not debate it ideologically; they just turn off the screen.

## 경기도 성화시 (城華市)
Pop. ~350,000. Seoul satellite city.
하늘 + all colleagues = 성화시 civil servants. All assignments within city limits.

Districts:
- 화서구: New-build apartments. 하늘's home. Younger pop.
- 중원구: Old downtown. Commercial/residential mix. Majority 40–60s.
- 동락구: Outer residential. Quiet. High elderly pop. Longest travel times.

## Agency Dynamics
- Male branch (남성 지원과) exists. Subtle but constant rivalry over budget and hazard pay. Women's branch claims extreme emotional burnout (늪, 감정 쓰레기통); Men's branch claims severe physical exhaustion.

## The Job & Field Manual
Schedule SMS → 18:00 prev. evening. Format: name / age / full address.
Up to 4 visits/day (09:00–16:00). ~2hr/client. No office — travel directly to addresses.
Kit: 러브젤, 콘돔, 소모품 파우치, 관장 도구, 머리끈
Services: 펠라치오, 정상위, 기승위, 후배위, 애널 (관장 30분 소요, Same-day: state condition, offer alternative).

[Field Guidelines & Loopholes]
- Service Time: 120 mins strict. Includes resting, small talk, and preparation. Early exit allowed if client finishes, but no carry-over.
- Environment: Staff clears *only* the immediate service radius (bed, sofa). Heavy cleaning = refused.
- Refusal Rights: Drunk, high, or "severe hygiene risk" (threat of infection) = immediate abort. Mere bad body odor ≠ grounds for refusal.
- Loophole Exploitation: "Manual Tester" clients systematically exploit the manual. E.g., demanding 110 mins of lap-resting because "rest is included," demanding edge-case cleaning, or forcing their business card on staff because "giving isn't asking for yours." Staff must navigate these with strict administrative dryness.

## Client Rules
Age: 만 18세 이상. Voluntary application.
Overt feelings or attachment behavior → blacklist + permanent suspension.
Feeling itself ≠ grounds for action. Acting on it is.
Post-session gifts banned (김영란법). One or two cans of a drink: tacitly acceptable.

## Staff Welfare
Monthly: gynecology exam + psychology session assigned. No clients on these days.

## Consumables
Supplied via 조달청 under 사회정서지원과. Requested items arrive within 2 days by delivery.
✅ "이번 분기에 새로 바뀐 콘돔인가? 향기가 좀 괜찮다."
</world>"""

    def get_specific_prose_rules(self, perspective: int = 1) -> str:
        return _PROSE_NOTES_1P

    def get_few_shot_examples(self, perspective: int = 1) -> dict:
        return _FEW_SHOT_1P

    def get_full_config(self, perspective: int = 1) -> dict:
        import os
        res = super().get_full_config(perspective)
        res["start_time"]           = self.get_default_time()
        res["prose_rules"]          = self.get_specific_prose_rules(perspective)
        res["few_shot_examples"]    = self.get_few_shot_examples(perspective)
        res["rating"]               = "r18"
        res["perspective"]          = 1
        res["intimate_genre_key"]   = "intimate_sses"
        res["intimate_sses"]        = _INTIMATE_RULES_1P
        res["opening_scene"]        = self.get_opening_scene()
        res["impersonation"]        = os.getenv("IMPERSONATION", "true").lower() == "true"
        res["additional_blacklist"] = (
            "\n## SSES-Specific\n"
            "Client age below 만 18세 — never generate. All clients are adults.\n"
            "Filler affirmations (아 그러셨어요 / 감사해요 / 조심히 들어가세요) → cut entirely.\n"
            "Client inner state asserted directly → 하늘 reads behavior, not minds.\n"
            "Silent pass-through on insults or unpleasant remarks → banned.\n"
            "Passive exit following client's lead → cut. 하늘 leads.\n"
            "Moral reflection on the job's meaning mid-scene → cut.\n"
            "Emotion label (긴장됐다 / 설레었다 / 불안했다) → body sensation + action only.\n"
            "Genital euphemism (그곳 / 거기 / 중요한 부위) → direct terms (자지 / 보지 / 질내사정) only.\n"
            "Commenting on client penis_size out loud → interior only."
        )
        res["world_cot_append"] = (
            "TIME_CALC: previous_header_time=[HH:MM] + elapsed_minutes=[N] = current=[HH:MM]. "
            "Location=[장소 — full address on first entry, short form thereafter]. "
            "Output header MUST be **YYYY년 M월 D일 요일 HH시 MM분, [장소].** — "
            "first line of prose, no exceptions.\n"
            "CLIENT: attitude=[val] / hygiene=[val] / nervousness=[val] / "
            "emotional_state=[val] / penis_size=[val]\n"
            "ARCHETYPE: [Tax Payer / Nervous Shut-in / Grieving Senior / Over-Immersed / "
            "Pragmatic Sub / Small Talker / Curious Chatter / Polite Peer] → "
            "interior rule for this type: [quote the one-line rule from world_prose]\n"
            "LOAD: clients_done=[N]/[total] → energy=[high≥0.7/mid/low≤0.3] → "
            "interior density: [rich tangents / sparse occupational only]\n"
            "GAP: exterior=[word] vs interior=[word]. "
            "Diverge point this scene: [identify exact beat where they split]. "
            "Same direction? [yes → inject counter-current before drafting]\n"
            "GEOMETRY (intimate scenes only):\n"
            "  1. Posture: exact positions of 강하늘 and client\n"
            "  2. Distance Map: which of client's body parts can physically reach which of 강하늘's\n"
            "  3. Constraint Check: if limb exceeds reach or passes through obstacle → "
            "do NOT deny. Insert bridging beat (posture shift / lean) that makes contact coherent. "
            "e.g. Paizuri leaning back → hands reach wrists/breasts only; "
            "lean forward mid-scene → waist becomes reachable."
        )
        res["intimate_checklist_items"] = (
            "\nINTIMATE\n"
            "- Work-flow skipped a stage (undressing→prep→positioning→penetration)\n"
            "- Penetration: entry collapsed into single verb without physical resistance beat\n"
            "- 강하늘's own body absent from prep narration\n"
            "- Physical obstacle encountered but not resolved in real time\n"
            "- Position/pacing change not initiated by 강하늘\n"
            "- Client emotional state followed in rather than pivoted from\n"
            "- Genital euphemism used instead of direct terms\n"
            "- GEOMETRY PROTOCOL: every contact point cleared in <thought> Constraint Check?\n"
        )
        return res

    def get_npc_name_map(self) -> dict[str, str]:
        return {
            "나래": "park_narae",   "나래 언니": "park_narae",   "박나래": "park_narae",
            "은혜": "cho_eunhye",   "은혜 언니": "cho_eunhye",   "조은혜": "cho_eunhye",
            "하은": "lee_haeun",    "하은 언니": "lee_haeun",    "이하은": "lee_haeun",
            "지영": "moon_jiyoung", "지영 언니": "moon_jiyoung", "문지영": "moon_jiyoung",
            "다인": "jung_dain",    "다인아": "jung_dain",       "정다인": "jung_dain",
            "수아": "han_sua",      "수아야": "han_sua",         "한수아": "han_sua",
            "지은": "oh_jieun",     "지은아": "oh_jieun",        "오지은": "oh_jieun",
            "채린": "yoon_chaerin", "채린아": "yoon_chaerin",    "윤채린": "yoon_chaerin",
            "지아": "lim_jia",      "지아야": "lim_jia",         "임지아": "lim_jia",
            "민경": "seo_minkyung", "민경아": "seo_minkyung",    "서민경": "seo_minkyung",
            "태양": "kim_taeyang",  "태양아": "kim_taeyang",     "김태양": "kim_taeyang",
            "성준": "ryu_sungjun",  "성준아": "ryu_sungjun",     "류성준": "ryu_sungjun",
            "아빠": "kang_minjun",  "강민준": "kang_minjun",
            "엄마": "shin_hyekyung", "신혜경": "shin_hyekyung",
        }

    def build_schema(self, driver: GraphDatabase.driver):
        super().build_schema(driver)

        with driver.session() as session:

            session.run("""
                CREATE (:Location {
                    id: "haneul_apt", name: "강하늘 자취방",
                    description: "하늘의 자취 원룸. 출근 전 준비 공간."
                })
            """)

            session.run("""
                CREATE (c:Character {id: "kang_haneul", name: "강하늘", type: "pc"})
            """)
            session.run("""
            MATCH (c:Character {id: "kang_haneul"})
            CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                id: "haneul_static", age: 22, gender: "female",
                role: "Junior staff, SSES",
                height: 167, weight: 51, bust: 88, waist: 59, hip: 86, cup: "F",
                looks: "Cute puppy-like features, round eyes, soft cheeks",
                personality: "genuine_warmth+intensely_curious+bubbly_yet_grounded+playful_quirks+chaotic_thought_tangents+uncontrollable_empathy_leak+social_recharger+spontaneous_problem_solver+always_on_occupational_lens+intuitive_detail_sensor",
                background: "Discovered the SSES during a high school career exploration program and felt an inexplicable, impulsive draw to it. Lost her virginity during the official mandatory SSES training program after passing the exam. Her uncontrollable empathy stems from a specific trauma: witnessing her grandfather rapidly decline and break down in isolation after her grandmother's death. She unconsciously uses this job to 'save' isolated men, making rigid boundary-setting nearly impossible.",
                hobby: "Urban sketching. A stark contrast to her highly physical, emotionally enmeshed job. When off-duty, she sits in parks or cafes drawing people from a distance—shifting her occupational lens to a purely observational lens to protect her sanity.",
                experience: "started_may — training complete, first month on the field",
                sample_line: "아, 그거 저 알아요. 잠깐만요."
            })
                        """)
            session.run("""
                MATCH (c:Character {id: "kang_haneul"})
                CREATE (c)-[:HAS_DYNAMIC_STATE]->(:DynamicState {
                    id: "haneul_dynamic", mood: "calm_focused", condition: "healthy",
                    energy: 0.85, stress: 0.15, current_task: "오늘 일정 준비 중"
                })
            """)

            seniors = [
                ("park_narae",   "박나래",  25, "Senior staff",
                 "energetic+genuinely_warm+story_teller+burns_bright_then_recharges",
                 163, 54, 86, 61, 88, "C", "야, 생각보다 별거 없어. 진짜로."),
                ("cho_eunhye",   "조은혜",  27, "Senior staff",
                 "principled+efficient+cares_through_diagnosis_not_comfort",
                 165, 52, 84, 60, 85, "B", "뭐가 힘들어, 구체적으로."),
                ("lee_haeun",    "이하은",  26, "Senior staff",
                 "quiet+present+highest_repeat_client_rate+shows_care_without_words",
                 160, 48, 82, 57, 83, "B", "힘들면 좀 쉬어."),
                ("moon_jiyoung", "문지영",  28, "Senior staff",
                 "analytical+deliberate+advice_lands_hard+waits_before_speaking",
                 168, 55, 85, 62, 87, "C", "넌 리드하는 게 어울려."),
            ]
            for (cid, name, age, role, personality, h, w, bu, wa, hi, cup, sample) in seniors:
                session.run("CREATE (c:Character {id: $id, name: $name, type: 'named'})",
                            id=cid, name=name)
                session.run("""
                    MATCH (c:Character {id: $id})
                    CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                        id: $sid, age: $age, gender: "female", role: $role,
                        height: $h, weight: $w, bust: $bu, waist: $wa, hip: $hi,
                        cup: $cup, personality: $personality, sample_line: $sample
                    })
                """, id=cid, sid=f"{cid}_static", age=age, role=role, h=h, w=w,
                    bu=bu, wa=wa, hi=hi, cup=cup, personality=personality, sample=sample)

            peers = [
                ("jung_dain",    "정다인", 22,
                 "empathetic+emotionally_invested+recharges_with_haneul",
                 164, 53, 87, 62, 89, "C", "오늘 좀 힘들었는데, 너 보니까 좋다."),
                ("han_sua",      "한수아", 23,
                 "introspective+meaning_seeker+writes_diary+asks_hard_questions",
                 162, 49, 81, 58, 82, "A", "우리 이 일 계속해도 되는 걸까."),
                ("oh_jieun",     "오지은", 22,
                 "cheerful+rolls_with_difficulty+admires_park_narae+quiet_when_alone",
                 161, 50, 83, 59, 84, "B", "뭐 그럴 수도 있지."),
                ("yoon_chaerin", "윤채린", 23,
                 "systematic+direct+reliable_for_practical_answers+not_close_personally",
                 166, 54, 85, 61, 86, "B", "그래서 규정 위반한 거 있어? 없으면 된 거지. 우린 봉사자가 아니라 공무원이잖아, 하늘아."),
            ]
            for (cid, name, age, personality, h, w, bu, wa, hi, cup, sample) in peers:
                session.run("CREATE (c:Character {id: $id, name: $name, type: 'named'})",
                            id=cid, name=name)
                session.run("""
                    MATCH (c:Character {id: $id})
                    CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                        id: $sid, age: $age, gender: "female",
                        role: "Peer staff, 사회정서지원과",
                        height: $h, weight: $w, bust: $bu, waist: $wa, hip: $hi,
                        cup: $cup, personality: $personality, sample_line: $sample
                    })
                """, id=cid, sid=f"{cid}_static", age=age, h=h, w=w,
                    bu=bu, wa=wa, hi=hi, cup=cup, personality=personality, sample=sample)

            friends = [
                ("lim_jia",      "임지아", 22, "female",
                 "bright+blunt+no_bias_about_haneul_job+slightly_envious_of_salary", 164, 52, "B"),
                ("seo_minkyung", "서민경", 23, "female",
                 "quiet+perceptive+most_likely_to_ask_are_you_okay", 161, 49, "A"),
                ("kim_taeyang",  "김태양", 23, "male",
                 "easygoing+old_friend+zero_romantic_feeling+accepted_job_without_fuss", 178, 72, None),
                ("ryu_sungjun",  "류성준", 24, "male",
                 "considerate+quiet+unresolved_feeling_toward_haneul+visibly_uncomfortable_at_times", 180, 75, None),
            ]
            for (cid, name, age, gender, personality, h, w, cup) in friends:
                session.run("CREATE (c:Character {id: $id, name: $name, type: 'named'})",
                            id=cid, name=name)
                params = dict(id=cid, sid=f"{cid}_static", age=age,
                              gender=gender, h=h, w=w, personality=personality)
                if cup:
                    session.run("""
                        MATCH (c:Character {id: $id})
                        CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                            id: $sid, age: $age, gender: $gender,
                            role: "Friend (civilian)", height: $h, weight: $w,
                            cup: $cup, personality: $personality
                        })
                    """, **params, cup=cup)
                else:
                    session.run("""
                        MATCH (c:Character {id: $id})
                        CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                            id: $sid, age: $age, gender: $gender,
                            role: "Friend (civilian)", height: $h, weight: $w,
                            personality: $personality
                        })
                    """, **params)

            session.run("CREATE (c:Character {id: 'kang_minjun', name: '강민준', type: 'named'})")
            session.run("""
                MATCH (c:Character {id: 'kang_minjun'})
                CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                    id: 'kang_minjun_static', age: 52, gender: "male", role: "Father",
                    height: 178, weight: 80,
                    personality: "taciturn+soft_on_haneul+was_opposed_now_silent+buys_things_on_payday",
                    sample_line: "밥은 먹었냐."
                })
            """)
            session.run("CREATE (c:Character {id: 'shin_hyekyung', name: '신혜경', type: 'named'})")
            session.run("""
                MATCH (c:Character {id: 'shin_hyekyung'})
                CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                    id: 'shin_hyekyung_static', age: 50, gender: "female", role: "Mother",
                    height: 162, weight: 58,
                    personality: "practical+supportive_from_day_one+slightly_overinvolved+asks_about_clients",
                    sample_line: "오늘 손님은 어땠어?"
                })
            """)

            for a, b, rel, affinity, trust in [
                ("kang_haneul", "park_narae",    "junior_senior",        78, 70),
                ("kang_haneul", "cho_eunhye",    "junior_senior",        65, 72),
                ("kang_haneul", "lee_haeun",     "junior_senior",        70, 68),
                ("kang_haneul", "moon_jiyoung",  "junior_senior",        60, 75),
                ("kang_haneul", "jung_dain",     "peer+close_friend",    88, 82),
                ("kang_haneul", "han_sua",       "peer+thoughtful",      72, 70),
                ("kang_haneul", "oh_jieun",      "peer+lunch_buddy",     75, 68),
                ("kang_haneul", "yoon_chaerin",  "peer+practical+ideological_rival", 60, 72),
                ("kang_haneul", "lim_jia",       "friend+old_classmate", 82, 78),
                ("kang_haneul", "seo_minkyung",  "friend+old_classmate+worrier", 78, 80),
                ("kang_haneul", "kim_taeyang",   "friend+longtime+platonic", 80, 85),
                ("kang_haneul", "ryu_sungjun",   "friend+acquaintance+tension", 68, 60),
                ("kang_haneul", "kang_minjun",   "family+father",        80, 90),
                ("kang_haneul", "shin_hyekyung", "family+mother",        82, 88),
            ]:
                session.run("""
                    MATCH (a:Character {id: $a}), (b:Character {id: $b})
                    CREATE (a)-[:RELATIONSHIP {
                        type: $rel, affinity: $affinity, trust: $trust,
                        current_status: "established"
                    }]->(b)
                """, a=a, b=b, rel=rel, affinity=affinity, trust=trust)

            session.run(f"""
                MERGE (gs:GlobalState {{id: 'singleton'}})
                SET gs.currentLocationId = 'haneul_apt',
                    gs.currentTime       = '{self.get_default_time().isoformat()}',
                    gs.weather           = 'Clear',
                    gs.schedule_slot     = 'pre_work',
                    gs.clients_done      = 0,
                    gs.clients_total     = 0
            """)

            print("✅ 사회정서지원과 스키마 생성 완료")


world_instance = SSESWorld()