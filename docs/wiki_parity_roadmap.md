# WikiRAG parity 구현 로드맵

> Branch: `graphRAG/wiki`
> 목적: GraphRAG의 **구현된** 기능 전체를 WikiRAG에 이식하기 위한 소스-단위 실행 로드맵.
> 성격: `architecture_wiki/TODO.md`(상태 보드)와 `docs/wiki_v2_todo.md`(장기 백로그)의
> 완료 체크리스트를 **소스 위치·통합 지점·구현 단계·검증**으로 구체화한 보조 문서다.
> 상태 체크박스의 정본은 두 보드가 유지하고, 이 문서는 "어디를 어떻게 고치는가"를 담는다.
> site(hosted-ui) 측 UI 변경 스펙은 `docs/wiki_site_ui_changes.md`로 분리한다.

---

## 1. 최상위 방침

1. **단일 Updater 유지.** Graph/Wiki는 이미
   `src/simulation/state/updater.py::update_accepted_turn(request)` 하나로 통합돼 있고,
   `request.mode`로 Graph(`graph_apply.py`)와 Wiki(`src/wiki/commit_planner.py`)에 지연
   분기한다. **새 parity 기능은 모드별 병렬 Updater를 만들지 않고, 이 단일 진입점의
   mode-aware 계약을 확장한다.** Wiki 쪽은 `plan_pending_commit` 한 번의 LLM 호출이
   내는 `WikiUpdaterResult`(patches + creations)를 넓히는 방식으로만 늘린다.

2. **Wiki 반영 = 4개 확장 지점의 확장.** 거의 모든 parity 항목은 아래 네 곳만 건드린다.

   | # | 확장 지점 | 파일 | 역할 |
   | --- | --- | --- | --- |
   | E1 | 출력 스키마 | `src/wiki/models.py` (`WikiUpdaterResult`, `CreateDocument` union, `SectionPatch`) | Updater가 반환할 수 있는 변경/생성의 형태 |
   | E2 | 검증·권한 | `src/wiki/commit_planner.py` (`_validate_result`, `_validate_patch_policy`, `_validate_document_creations`) | 출처·소유·범위·중복 검증 |
   | E3 | 프롬프트 계약 | `src/wiki/prompts/updater.md` | 모델에게 허용/금지 규칙 전달 |
   | E4 | 컨텍스트 선택 | `src/wiki/context.py::read_wiki_thread_documents`, `src/wiki/runtime.py` | Updater 입력 문서 집합과 Actor 가시 문서 필터 |

   신규 문서 종류는 여기에 **문서 렌더러**(`src/wiki/document_creation.py`)와
   **템플릿**(`src/wiki/templates/*.md`) 확장이 더해진다. Goal/Item/Secret 템플릿과
   `WikiDocumentType`·`WikiMetadata` 검증은 **이미 존재**한다.

3. **Markdown이 유일 정본.** 파생 인덱스(검색·임베딩·backlink)는 Markdown에서 재생성
   가능한 캐시로만 둔다. 상태를 DB가 아니라 문서 섹션에 적는다.

4. **턴-구동 vs 시간-구동 구분.** GraphRAG 후처리기는 두 부류다.
   - **턴-구동**(응답 내용에서 사실을 추출): Event, Memory, 관계, 상태, Goal/Item/Secret
     변화 → **E1~E3 확장**으로 단일 Updater가 흡수한다.
   - **시간-구동**(경과 시간·확률로 상태가 변함): needs 감쇠, schedule tick,
     memory 감쇠, 성격 drift, gossip 확산 → LLM 출력이 아니라 **결정적 post-commit
     로컬 규칙 또는 선택적 postprocessor**로 반영한다(정책상 P3, 개별 실험 후 편입).

5. **위험한 Graph 동작은 복제하지 않는다.** 예: Graph가 커밋 후 텍스트를 편집해 상태와
   어긋나는 문제는 Wiki에서 inverse/3-way 분기로 이미 안전하게 재설계돼 있다. 같은
   원칙을 이어간다.

---

## 2. parity 대상과 제외 대상

### 2.1 이식 대상 (GraphRAG에 구현되어 있음)

| 영역 | Graph 소스 | Wiki 현재 | 목표 마일스톤 |
| --- | --- | --- | --- |
| Goal 생성·갱신 | `src/simulation/systems/goals/` (`apply_goal_updates`) | 단일 Wiki Updater 생성·갱신·권한 구현 | M1 |
| Item 생성·갱신 | `src/simulation/systems/items/` (`apply_item_updates`) | 단일 Wiki Updater 생성·갱신·권한 구현 | M1 |
| Secret 생성·갱신·공개 | `src/simulation/systems/secrets/` (`apply_secret_updates`) | knower-scoping·은닉 prompt·출력 guard 포함 구현 | M1 |
| 변경 이력·감사 완성 | `src/simulation/state/apply/audit.py` 등 | baseline 외부 편집 manual archive/inverse까지 완료 | M2 |
| Memory recall 선택 | `src/simulation/systems/memory/` (recall) | Option A 결정적 예산 선택 구현 | M3 |
| 결정적 시간·스케줄 | `src/simulation/state/apply/time_plan.py`, `systems/schedules/` | accepted-header 시각·문서 일정 계약 구현 | M4 |
| needs 감쇠·자율 행동 | `src/simulation/systems/needs/`, `src/agents/resolver.py` | 결정적 needs와 Actor 자기 행동 prompt 계약 구현 | M4 |
| Memory 감쇠·왜곡 | `src/simulation/systems/memory/decay.py`, `distort_on_affinity_change` | recall 감쇠 + 기본-off 관계 게이트형 왜곡 구현 | M5 |
| social/gossip/reputation | `src/simulation/systems/world_dynamics/reputation.py`, `systems/social/` | 명시 목격자 gossip 구현, reputation/Kakao 후속 | M6 |
| personality drift | `src/simulation/systems/world_dynamics/personality.py` | 기본-off 동적 성격 변화 원장 구현 | M7 |
| pregnancy/organic | `src/simulation/systems/world_dynamics/organic.py` | opt-in 상태·결정적 roll·공용 OOC 구현 | M7 |
| 상태 탐색·편집 도구 | `src/apps/world_editor/`, `src/apps/graph_viewer/` | Explorer 목록 API 완료, 편집/tree/diff UI 후속 | M8 |
| 장기 플레이 검증 | 수동 | 검증 필요 | M9 |

### 2.2 제외 대상 (Graph에서도 미구현/실험 중이므로 이식하지 않음)

- `MANAGER_PLANNER_MODE=integrated`, `TURN_EXTRACTOR_MODE=unified` — Pro 전용·미채택.
- kakao 시스템 중 미완 경로.
- `docs/wiki_v2_todo.md` Phase 0 미결정 지표들(추후 확정).

### 2.3 구현 진행 현황 (2026-07-24)

"M9까지 멀티턴 LLM 테스트 필요 부분 제외 전부 시작" goal에 따른 실제 코드 진행:

| M | 상태 | 비고 |
| --- | --- | --- |
| M1 Goal·Item·Secret | ✅ 코드 완료 | E1~E4 + Actor-visible knower-scoping + hidden/suspected 은닉 계약, smoke 통과 |
| M2 감사 | ✅ 코드 완료 | 진단·schema 계약·상태 migration + baseline 기반 외부 section/구조/생성/삭제 manual archive와 inverse smoke 완료 |
| M3 Memory recall | ✅ 코드 완료 | Option A 확정·구현(`recall.py`), Actor/Updater 예산 분리, smoke 통과. 임베딩(B)은 실측 후 seam 교체 |
| M4 시간·needs·schedule | ✅ 코드 경로 완료 | 결정적 needs vector가 작성자 Condition을 보존하고 수치·pressure만 갱신, schedule 서술, Active pressure/현재 시각 기반 Actor 자기 행동 계약과 smoke 완료; `lover` 실제 1턴 통과, 장기 LLM 검증 필요 |
| M5 Memory 감쇠·왜곡 | ◑ 코드 경로 완료 | 큰 durable 관계 patch에서만 기본-off 왜곡 실행, exact source quote와 실패 격리 smoke 통과 |
| M6 gossip | ◑ 코드 경로 완료 | 새 Event의 명시 목격자를 제3자 owner-private Memory로 전파하는 기본-off 경로 구현; 장기 실측 후 범위 조정 |
| M7 personality·pregnancy | ◑ 코드 경로 완료 | 동적 성격 변화 원장, opt-in 생식 상태, 결정적 roll, 공용 OOC 반환과 기본-off 게이트 구현 |
| M8 Explorer·편집 | ◑ 백엔드 시작 | 문서 목록 읽기 API(`explorer.py`, `GET .../wiki/documents`) 완료. 편집/tree/diff UI는 site 후속 |
| M9 장기 검증 | ◑ 1턴 기준선 완료 | 현재 5개 시나리오 실제 Actor/Updater/deferred/apply 통과; `player_evidence`, Condition 보존, 누적 retry 결함 수정. 각 20턴 검증은 남음 |

요약: M1~M7의 1차 코드 경로와 LLM 없는 회귀는 구현됐다. 남은 핵심은 M4 자율 행동,
M5~M7 실제 장기 플레이 품질·임계 조정, M8 편집/tree/diff UI, M9 다섯 시나리오 20턴 장기
검증이다. 사용자 실행 검증이 필요하다는 이유로 코드 구현을 미완료 상태에 두지 않는다.

---

## 3. 마일스톤별 실행 계획

각 마일스톤은 (a) Graph 기준 동작, (b) Wiki 매핑, (c) 구현 단계, (d) 검증으로 기술한다.
정책상 P2 → P3 → P4 순서를 따른다.

---

### 3.1 단일 Updater 통합 검증 체크리스트

Goal/Item/Secret과 이후 시스템을 단일 Updater로 흡수할 때 공통으로 확인할 항목이다.
`[기존]`은 이미 검증됨, `[신규]`는 M1에서 추가, `[열림]`은 설계/실측이 남은 부분.

**A. 출력·파싱 견고성 (단일콜 핵심 리스크)**
- `[기존→강화]` 절단: `max_output_tokens=65536`으로 사실상 해소. 단 구조적으로
  all-or-nothing(잘리면 그 턴 전체 소실)은 남음. 회귀 없는지 확인.
- `[열림]` `finish_reason == MAX_TOKENS` 구분: 절단과 스키마 위반을 나눠 로그·재시도
  전략을 다르게 할지.
- `[기존]` 재시도 시 직전 거부 사유 전달이 커진 스키마에서도 유효한지.

**B. 스키마 검증 — `models.py` (E1)**
- `[신규]` `CreateGoal/Item/SecretDocument` 3종이 discriminator(5종)로 정상 분기.
- `[신규]` 신규 필드 전부 single-line 주입 방지 validator.
- `[신규]` ID namespace·owner 요구 검증.

**C. 권한·범위 검증 — `commit_planner.py` (E2)**
- `[기존]` evidence exact-quote + evidence_source 권한(player vs actor)을 신규 3종에 적용.
- `[신규]` owner=활성 thread profile, 중복 ID/경로 거부, confidence 하한.
- `[신규]` 정적 정체성 read-only(goal `목표 정체성`/item `물품 정체성`/secret `비밀 정체성`).
- `[신규]` 플레이어 소유 goal/secret 갱신은 `player_input` 근거만.

**D. Actor 가시성·프롬프트 계약 — `runtime.py` (E4)**
- **D1** `[신규]` **knower-scoping 정확성**: 활성 NPC가 아는 비밀·목표만 Actor에 포함,
  NPC가 모르는 건 제외(Memory owner 필터 재사용).
- **D2** `[신규]` **은닉 계약**: Fixed/Genre에 "알지만 감춘다 + subtlety" 지시가 들어가고
  프롬프트 snapshot에 반영되는지.
- **D3** `[열림, 진짜 남는 리스크]` **실묘사 누출**: 능력 있는 모델도 가끔 조기 발설/
  텔레그래프 가능. 은닉 계약이 실제로 지켜지는가는 장기 플레이 품질(M9)에서 측정.
- `[기존]` Fixed/Genre/Dynamic 경계 snapshot + `updater.md` hash 갱신.

**E. Secret reveal (Option A) 전용**
- `[신규]` 산문 발각 조건 판단 → status hidden→revealed patch의 보수성(일상 대화 남발 금지).
- `[신규]` reveal patch가 evidence exact-quote로 뒷받침되는지.

**F. 생명주기 — inverse / reroll / rollback**
- `[신규]` goal/item/secret 생성이 event/memory처럼 inverse 삭제 지원, `applied_creations`에
  신규 타입 포함.
- `[신규]` 신규 문서를 만든 최신 턴의 reroll/edit/delete가 상태를 깔끔히 되돌리는지.
- `[신규]` `progress`/`status` patch의 3-way 수동편집 보존이 신규 섹션에서도 동작.

**G. 회귀·격리**
- `[기존]` Graph mode 무영향(단일 Updater mode-aware).
- `[기존]` `tests/smoke_wiki_v2.py`(LLM 없이 생성→patch→inverse) 신규 3종 추가.
- `[기존]` `tests/smoke_wiki_runtime.py` 프롬프트 계약 snapshot 갱신.

**H. 장기 품질 (M9)**
- `[열림]` 추론 희석: 한 호출이 scene+관계+event+memory+goal+item+secret을 동시에
  juggling — 각 항목 추출 품질 실측.
- `[열림]` 입력 토큰 증가: 모든 문서+규칙이 매 턴 입력(상시 과금) → M3 recall로 완화.

---

### M1 — Goal·Item·Secret 단일 Updater 편입 (P2)

**(a) Graph 기준 동작.**
`graph_apply.py`가 `should_run_life_depth_system("goals"/"items"/"secrets", ...)` 게이트
뒤에 각 시스템의 `apply_*_updates(actor_response, owner_id, pc_id, current_time, event_id)`를
best-effort 호출한다. 각 시스템은 별도 LLM 호출로 진행도/상태/공개 단계를 갱신한다.

**(b) Wiki 매핑 (핵심: 별도 호출 없이 단일 Updater 확장).**

- 문서 종류·템플릿·frontmatter 검증은 이미 존재
  (`WikiDocumentType`에 `goal|item|secret`, 템플릿 3종, `WikiMetadata`의 owner 요구).
- Updater 입력에는 이미 모든 thread 문서가 들어간다
  (`read_wiki_thread_documents`가 `commit.md`/`commits/` 외 전부 로드).
  따라서 **기존 goal/item/secret 문서의 갱신은 `SectionPatch`로 즉시 가능**하고,
  검증 정책(E2)과 프롬프트 규칙(E3)만 추가하면 된다.
- **신규 생성**은 `CreateDocument` union(E1)을 `event|memory` →
  `+ goal|item|secret`로 확장하고, `document_creation.py`에 렌더러 분기를 추가한다.
- **Secret reveal**은 결정적 조건이 아니라 단일 Updater의 산문 판단으로 처리한다((c) 5단계).
- **가시성**은 knower-scoped: 활성 NPC가 아는 비밀·목표만 Actor에 노출하고 은닉은
  프롬프트 계약으로 강제한다((c) 6단계). Graph의 hint 은닉 기계는 이식하지 않는다.

**(c) 구현 단계.**

1. **E1 — 출력 모델.** `src/wiki/models.py`:
   - `CreateGoalDocument` / `CreateItemDocument` / `CreateSecretDocument` 추가
     (owner, title, evidence, evidence_source, confidence 공통 + 종류별 필드).
     - Goal: `desired_outcome`, `motivation`, `status`(active 기본), `current_step`,
       `next_action`, `obstacles`, `completion_conditions`.
     - Item: `kind`, `appearance`, `function`, `constraint`, `storage_location`,
       `access_state`, `status`(available 기본), `recent_change`.
     - Secret: `actual_content`, `who_knows`, `concealment`, `status`(hidden 기본),
       `public_clue`, `misunderstanding`, `exposure_condition`, `exposure_result`.
   - discriminator를 `document_type`로 확장. 각 필드에 기존 memory와 동일한
     single-line 주입 방지 validator 적용.
2. **E4 — 렌더러.** `src/wiki/document_creation.py`에
   `_prepare_goal_document` / `_prepare_item_document` / `_prepare_secret_document`
   추가, `prepare_created_document` 분기 확장. 템플릿 marker 교체 후
   frontmatter/type/thread_id/owner 재검증(기존 event/memory 패턴 그대로).
3. **E2 — 검증·권한.** `commit_planner.py`:
   - `_validate_document_creations`에 goal/item/secret 분기:
     - `owner`는 활성 thread character profile이어야 함(`available_profile_ids`).
     - ID namespace `goal:` / `item:` / `secret:`, 경로 중복·ID 중복 거부.
     - **Secret 소유·근거:** Actor 근거로 생성된 secret은 소유자=Actor owner만 허용.
       (가시성은 아래 (e) knower-scoping 참조. secret도 Actor가 아는 비밀이면
       Actor-visible이며, 은닉은 프롬프트 계약으로 강제한다.)
     - confidence 하한(생성 0.75 유지).
   - `_validate_patch_policy`에 갱신 범위:
     - goal 문서: `## 진행 상태` 하위만 patch 허용, `## 목표 정체성`은 read-only.
     - item 문서: `## 현재 상태` 하위만 patch 허용, `## 물품 정체성` read-only.
     - secret 문서: `## 공개 상태` 하위만 patch 허용, `## 비밀 정체성` read-only.
     - 플레이어 소유 goal/secret 갱신은 `player_input` 근거만.
4. **E3 — 프롬프트.** `updater.md`에 규칙 블록 추가:
   - "durable goal/item/secret 신규 생성·상태 변경만 기록. 일상 대화·소지품 언급은 무시."
   - Graph의 보수성 규칙 이식: 진행도는 명시적 진전이 있을 때만, secret 공개는
     실제 발각/자백 근거가 있을 때만.
   - `creations` 예시에 goal/item/secret 1개씩 추가.
5. **Secret reveal (Option A — 단일 Updater LLM 판단).** Graph의 reveal은 구조화된
   `reveal_conditions`를 `evaluate_conditions`(time/stat/flag 술어)로 결정적 평가한다.
   그러나 `stat`(affinity/trust)·`flag`(GlobalState.flags)가 조회하는 수치 상태는
   **Wiki가 의도적으로 버린 것**이라 재현 불가능하고, `time`만 살아남는다. 따라서
   Wiki는 별도 조건 언어를 만들지 않고 **산문 "발각 조건"을 단일 Updater가 판단**한다:
   서사가 조건을 충족하면 `## 공개 상태` status를 hidden→revealed로 바꾸는 `SectionPatch`
   하나를 emit한다. reveal은 evidence exact-quote로 뒷받침되어야 하며, 일상 대화로
   조기 공개하지 않는다(보수성). **의미: reveal은 Actor가 비밀을 알게 되는 것이 아니라
   (이미 앎) 세계·플레이어가 알게 되는 상태 전이다.** 장기 테스트에서 LLM 공개 타이밍이
   불안정하면 그때 `time`/`event_exists`만 결정적 술어로 좁게 추가한다(후속 옵션).
6. **Actor 가시성 — knower-scoped (Memory owner 필터 재사용).** Graph의 힌트 은닉
   (`fetch_goal_hints`/`fetch_secret_hints`)은 약한 모델의 누출 방어책이었지, 설계
   요구가 아니다. 일관된 묘사를 하려면 **NPC는 자기가 아는 비밀·추구하는 목표를 온전히
   알아야** 한다(은닉·회피·미세 반응은 진실을 알아야 나오고, 목표는 NPC 행동을 몰아가라고
   존재한다). 따라서:
   - secret/goal 문서를 **활성 NPC가 knower/owner일 때만** Actor 프롬프트에 포함한다
     (Wiki가 Memory에 이미 쓰는 owner-scoping과 동일 규칙, `runtime.py`).
     NPC가 진짜 모르는 비밀(다른 인물의 비밀 등)은 Actor에서도 제외해 무지를 유지한다.
   - secret 템플릿 `visibility`는 knower인 활성 NPC 컴파일 시 `actor`를 포함하도록 한다.
   - **은닉·subtlety는 정보 차단이 아니라 프롬프트 계약(Fixed/Genre)으로 강제**한다:
     "너는 이 비밀을 안다 — 감추는 인물을 연기하라. 서사적 공개가 성립하기 전엔 직접
     말하지 마라. 목표는 subtlety만큼 은근하게 추구하라."
   - Actor가 진실을 아므로 `public_clue` 별도 노출 장치는 불필요(저작 메모로만 남김).
     힌트 생성 기계(`fetch_*_hints`)는 이식하지 않는다.

**(d) 검증.** 아래 §3.1 "단일 Updater 통합 검증 체크리스트"를 M1 완료 기준으로 삼는다.
최소한: goal/item/secret 생성→patch→inverse round-trip(`tests/smoke_wiki_v2.py`),
정적 정체성 patch 거부, knower-scoping 정확성, `updater.md` prompt hash snapshot 갱신.

**구현 상태 (2026-07-24):** 코드 스캐폴딩 완료.
- E1~E4 + knower-scoping 구현, `tests/smoke_wiki_v2.py`에 goal/item/secret 생성·갱신
  권한·knower·inverse round-trip 검증 추가, 두 smoke 모두 통과.
- goal/item은 owner-scoped Actor-visible이고 Secret은 활성 Actor가 owner/knower일 때만
  Actor-visible이다. hidden/suspected private truth는 은닉 prompt 계약과 별도 출력
  guard/repair로 보호하며, `revealed` 전환과 공개 단서는 기존 commit 경계를 따른다.
- **후속:** 실제 장기 플레이에서 은근한 묘사 품질과 false-positive repair를 측정한다.

**보드 반영:** `architecture_wiki/TODO.md`의 "Goal·Item·Secret" 행 부분→완료,
`docs/wiki_v2_todo.md` Phase 5/§P2 해당 항목 체크. (같은 커밋에서 갱신.)

---

### M2 — 변경 이력·감사 완성 (P2)

**(a) Graph 기준 동작.** pending/state audit 스냅샷.

**(b) Wiki 현재.** 적용 전·후 hash와 section diff는 이미 `commit archive`에 기록됨
(`AppliedSectionChange`, `PendingWikiCommit.applied_changes`). 남은 것은 세 가지.

**(c) 구현 단계.**

1. **수동 편집 `manual` commit 기록.** `PendingWikiCommit.operation`에 이미 `manual`
   값이 있음. Obsidian 등 외부 편집을 감지해 별도 `manual` commit archive로 남기는
   경로를 `src/apps/app/wiki_controls.py`에 추가(턴 시작 시 canonical revision 변화
   비교 → manual commit 생성).
2. **중복 문서 ID 진단.** `src/wiki/store.py` 또는 신규 `src/wiki/diagnostics.py`에
   vault 전체 `id` 유일성·잘못된 frontmatter 스캔 함수 추가, `wiki_controls` 상태 조회에
   노출.
3. **`schema_version` 마이그레이션 계약.** `schema_version: 1` → N 문서 변환 규칙과
   ledger를 정의(`docs/wiki_v2_format.md`에 계약 명시 + `src/wiki/migrations.py`).
4. **기존 thread 상태 계약 보강.** 자동 재작성하지 않고 누락된 runtime-owned 캐릭터
   H3를 미리 본 뒤 사용자 승인으로 audited `operation: manual` commit을 적용한다.
   기존 pending이나 완전한 `현재 상태` H2가 없는 문서는 충돌로 중단한다.

**구현 상태:** `.wikirag-audit-baseline.json`을 턴 시작 전 비교해 H2 변경은 section
snapshot, 문서 구조 변경은 full replacement, 생성·삭제는 full-content snapshot으로
deterministic manual archive에 기록한다. 정상 내부 commit 뒤 baseline을 갱신하므로
자동 변경을 외부 변경으로 오인하지 않는다. 명시 preview/record API와 inverse smoke도
완료했다. 실시간 watcher는 감사 정확성이 아니라 즉시 알림을 위한 별도 P3 작업이다.

**(d) 검증.** 중복 ID/깨진 frontmatter를 심은 임시 vault로 진단이 잡히는지,
manual commit이 자동 commit과 구분되는지 smoke 검증.

---

### M3 — Memory recall 선택 정책 (P3)

**(a) Graph 기준 동작.** 관련 기억을 검색해 Actor 컨텍스트에 주입.

**(b) Wiki 현재.** Memory 생성·owner 격리는 완료. 그러나 `read_wiki_thread_documents`가
**모든** 문서를 로드하므로, 턴·문서가 늘면 Actor 프롬프트/Updater 입력이 무한정 커진다.
recall = "이번 턴에 관련된 memory/event/goal/secret만 선택"하는 정책이 필요.

**(c) 구현 단계.**

1. **Actor 컨텍스트 선택.** `runtime.py::build_wiki_prompt_bundle`에서 Actor-visible
   동적 문서를 (현재 owner NPC의) 최근성·장면 관련성 기준으로 상한 선택. 초기에는
   결정적 규칙(최근 N개 + 현재 장면 참여자 관련)으로 시작, 필요 시 임베딩 검색(캐시)
   추가 — 정책상 Markdown 재생성 가능해야 함.
2. **Updater 입력 예산.** `read_wiki_thread_documents`에 선택 파라미터를 추가하되,
   **정합성이 필요한 문서(scene, 활성 character, 활성 relationship)는 항상 포함**.
   event/memory/goal/item/secret은 관련성 상한 적용.
3. 링크 탐색 깊이·문서별 token budget 확정(`docs/wiki_v2_todo.md` Phase 4).

**(d) 검증.** 문서 수를 늘린 vault에서 프롬프트 토큰이 상한 내로 유지되고, 관련 memory가
누락되지 않는지 측정.

---

### M4 — 결정적 시간·needs·schedule (P3)

**(a) Graph 기준 동작.** needs가 시간에 따라 감쇠하고 임계 초과 시 `resolver.py`가
자율 행동을 만든다. schedule은 tick으로 진행. 시간은 accepted 헤더로 전진.

**(b) Wiki 매핑.** 시각 전진은 이미 `_synchronize_accepted_header`가 결정적으로 처리.
needs/schedule은 **시간-구동**이므로 LLM Updater 출력이 아니라 **post-commit 결정적
로컬 규칙**으로 구현한다(정책 §4).

**(c) 구현 단계.**

1. needs를 character `## 현재 상태` 하위 섹션(또는 전용 needs 문서)으로 모델링.
   경과 in-world 시간(scene 시각 diff)에 비례한 결정적 감쇠 규칙을 `src/wiki/`의
   post-commit 훅에 추가. `NeedLevels`/`NEED_DEFAULTS`(`systems/needs/models.py`)를
   상수로 재사용(하향 import 규칙 준수).
2. schedule은 초기엔 결정적 규칙 없이 scenario/character 문서의 일정 서술을 Actor가
   읽는 수준으로 두고, tick 필요성을 실측 후 결정(정책상 "결정 필요").
3. 자율 행동(needs 임계)은 선택적 postprocessor로 분리, 기본 off.

**(d) 검증.** 시간 점프 시 needs 값이 결정적으로 변하고 역행하지 않는지, 재시작 후에도
Markdown만으로 재현되는지 확인. 2026-07-25 `lover` 실제 1턴에서 deferred/apply를
통과했고, 그 과정에서 발견한 `Condition` 덮어쓰기를 수정해 작성자 서술 보존을
회귀 테스트로 고정했다. 장기 임계와 자율 행동 품질은 M9에서 계속 측정한다.

---

### M5 — Memory 감쇠·왜곡 (P3, 선택적 postprocessor)

**(a) Graph 기준 동작.** `decay.py` 감쇠 루프, `distort_on_affinity_change`가 큰 관계
변화 시 공유 기억을 재해석.

**(b) Wiki 매핑.** memory 템플릿에 이미 `certainty`·`distortion_risk` 필드 존재.
왜곡 = 이 필드 방향으로 `remembered_content`/`interpretation`을 재서술하는 **선택적
postprocessor**. 감쇠 = 삭제가 아니라 recall 가중치 하향(M3와 연계). Markdown 정본
보존.

**(c) 구현 단계.** 큰 durable 관계 변화가 커밋될 때 관련 memory에 대해 postprocessor를
게이트 실행(기본 off). 결과는 별도 공개 Updater를 만들지 않고 공용
`update_accepted_turn(mode="wiki")`가 정상 결과와 같은 pending commit에 병합한다.
정상 Updater를 실패시키지 않으며 applied section snapshot으로 감사한다.

**(d) 검증.** 왜곡 전/후 Markdown diff가 감사에 남고, 사실 자체가 조용히 추가되지 않는지
(evidence 없는 신규 사실 금지) 확인.

---

### M6 — social/gossip/reputation (P3)

**(a) Graph 기준 동작.** 중요 event + 유의미 관계 변화 시 `propagate_gossip`로 제3자
평판·지식 전파. transient NPC 정체성은 `systems/social/graph.py`.

**(b) Wiki 매핑.** 사용자 가치가 확인된 것부터. gossip = 목격자 외 인물의 memory/관계
문서에 전파 bullet 추가하는 Updater creation/patch로 표현 가능하나, 다자 문서 동시 변경은
비용·정합성 위험 → **실측 후 편입**.

**(c) 구현 단계.** 새 Event의 명시된 제3자 `witnesses`만 owner로 하는 주관적 Memory를
기본-off postprocessor로 생성한다. participants 전체나 추정 목격자에게 확장하지 않는다.
Kakao/reputation과 다단 전파는 실제 필요성이 확인된 뒤 별도로 판단한다.

---

### M7 — personality drift, pregnancy/organic (P3)

**(a) Graph 기준 동작.** `check_personality_drift`(중요도/관계 기반 성격 수치 drift),
`process_ejaculation`(임신 시스템, OOC 메시지 반환).

**(b) Wiki 매핑.** 정적 성격은 read-only로 유지하고 `Personality Change Ledger`에만
durable 변화를 누적한다. 생식 상태는 author가 명시적으로 켠 캐릭터의
`Reproductive State`에서만 진행하며 공용 `TurnUpdateResult.ooc_message`를 사용한다.

**(c) 구현 단계.** 두 경로 모두 기본-off gate이며 공용 Wiki accepted-turn 분기에서 같은
pending에 병합한다. 임신 확률은 commit ID와 누적 횟수로 재현 가능하고, 추가 처리 실패는
정상 Updater 결과를 막지 않는다.

---

### M8 — 상태 탐색·편집 (P4)

Markdown World Editor(섹션 폼 + 원문 편집), Wiki Explorer(문서 tree·link·backlink·diff·
시점 복원). `docs/wiki_v2_todo.md` Phase 8·9와 동일. M3의 인덱스(id/type/title/tag/
backlink)를 기반으로 함.

---

### M9 — 장기 플레이·비용·지연 검증 (P4)

5개 현재 시나리오 각 20턴 이상 실제 LLM 실행. 사실 누락·과잉 patch·입력 token·비용·지연
기록. `architecture_wiki/TODO.md`의 "즉시 검증" 체크리스트와 "공동 개발 전환 조건"을 충족.

---

## 4. 공통 검증 전략

- **LLM 없는 회귀:** `tests/smoke_wiki_v2.py`에 문서 생성/patch/inverse/rollback을
  parity 항목마다 추가.
- **프롬프트 계약 회귀:** `tests/smoke_wiki_runtime.py`의 Fixed/Genre/Dynamic +
  `updater.md` snapshot을 각 마일스톤에서 갱신. Fixed cache 안정성 유지.
- **모드 격리:** Graph 대화 생성·deferred Kuzu commit이 깨지지 않는지, Wiki 전용 도구가
  Graph thread에서 실행되지 않는지 확인(`architecture_wiki/TODO.md` GraphRAG 유지보수).
- **각 Python 편집 후:** `python -m py_compile` + `python-file-header` 스킬로 헤더 동기화.
- **비-사소 변경 후:** `/codex:review --background`(AGENTS.md 워크플로).

---

## 5. 결정이 필요한 항목

> **해소됨 (M1 설계 확정):**
> - ~~Secret 공개 단서의 Actor 노출 위치~~ → **소멸.** Actor는 자기가 아는 비밀을
>   온전히 알고(knower-scoped) 은닉은 프롬프트 계약으로 강제하므로 `public_clue` 별도
>   노출 장치가 필요 없다. (M1 (c) 6단계)
> - ~~Secret reveal을 결정적 조건으로 둘지~~ → **Option A 확정.** 별도 조건 언어 없이
>   산문 발각 조건을 단일 Updater가 판단. `stat`/`flag` 결정적 술어는 Wiki가 버린 수치
>   상태에 의존해 재현 불가. `time`/`event_exists` 결정적 술어는 불안정 실측 시 후속. (M1 (c) 5단계)

### 확정된 결정 (2026-07-24)

1. **goal/secret knower 판정 소스** (M1) — **확정: owner/knower id 일치 단순 규칙**.
   secret의 `who_knows`·goal의 `owner` frontmatter id가 활성 NPC profile과 일치하는지로만
   판정한다(Memory owner-scoping과 동일). 본문 서술 파싱은 하지 않는다.
2. **시간·스케줄의 결정성 범위** (M4) — **확정: 시각·needs는 결정적, 일정은 문서 계약**.
   accepted 헤더로 시각을 역행 없이 전진시키고 같은 경과 분으로 Actor-owner의 canonical
   need vector를 갱신한다. 일정은 character/scenario 서술을 Actor가 현재 시각과 함께
   판단하되 플레이어 이동을 만들 수 없고, Active pressure도 NPC 자신의 행동 동기로만 쓴다.
3. **needs/감쇠/왜곡/소문/자율행동의 편입 임계** (M4~M6) — **확정: 근거대로**. 각각
   실측 근거가 확인된 것만 정식 편입한다(정책상 개별 실험, 기본 off로 시작).
5. **Updater 모델** (전체) — **확정: `MODEL_PRO_UPDATER = Gemini 3.1 Pro`**. 3.5 Pro는
   출시 소문만 수 주째라 불확실 → 3.1 Pro 기준으로 진행하고, 출시·검증되면 그때 교체.

4. **Memory recall 검색 방식** (M3) — **확정: Option A(결정적 최근성·구조 관련성 +
   예산 트리거 fallback)**. `src/wiki/recall.py::select_recall_documents`가 누적 문서
   (event/memory/goal/item/secret)의 문서 수 또는 보수적 추정 token 합이 예산을 넘을
   때만 (구조 관련성 → 최근성)으로 축소한다. 짧은 thread는 전량 포함으로 무변경.
   Actor는 24개/12000 token으로 좁게, Updater는 48개/32000 token으로 넓게 잡고
   환경 변수로 조정한다. Actor runtime은 wikilink를 따라가지 않으므로 링크 탐색
   깊이는 0이다. 랭킹 함수만 교체하면 임베딩 의미 검색(Option B)으로 승격 가능한 seam이다.
   B는 품질이 멀티턴 LLM 검증에 걸려 있어, 실플레이에서 "옛 기억 소환 실패"가 실측되면
   그때 seam에 꽂는다.

---

## 6. 구현 순서 요약

```text
M1 Goal·Item·Secret 단일 Updater 편입        (P2, 즉시 착수)
M2 변경 이력·감사 완성                        (P2)
M3 Memory recall 선택 정책                     (P3, 문서 증가 대응 필수)
M4 결정적 시간·needs·schedule                  (P3)
M5 Memory 감쇠·왜곡 (선택적 postprocessor)     (P3)
M6 social/gossip/reputation                    (P3, 실측 후)
M7 personality drift, pregnancy/organic        (P3, 구조 안정 후)
M8 상태 탐색·편집 도구                          (P4)
M9 장기 플레이·비용·지연 검증                   (P4, 전환 조건)
```

각 마일스톤 완료 시 이 문서가 아니라 `architecture_wiki/TODO.md`와
`docs/wiki_v2_todo.md`의 상태를 같은 커밋에서 갱신한다.
