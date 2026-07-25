---
aliases:
  - Architecture TODO
  - Development Board
tags:
  - todo
  - architecture
---

# Wiki-first parity TODO

이 문서는 [[GraphRAG]]와 [[WikiRAG]]의 실행 순서를 함께 관리하는 Obsidian 작업 보드다. 상세한 WikiRAG 장기 백로그는 `docs/wiki_v2_todo.md`, 소스-단위 parity 구현 로드맵(어디를 어떻게 고치는가)은 `docs/wiki_parity_roadmap.md`, 실제 LLM 테스트 케이스는 `docs/wiki_v2_manual_llm_test_plan.md`에 있다.

## 개발 모드

WikiRAG가 GraphRAG의 사용자 기능과 상태 의미를 따라잡을 때까지 신규 제품 개발은
Wiki를 우선한다. Graph는 치명적 버그, 공용 Actor/Prompt/UI 회귀, parity 판정에
필요한 검증만 수정한다. Graph의 Kuzu 구현을 그대로 복사하지 않고 같은 사용자
동작과 복구 보장을 Markdown, 결정적 로컬 규칙, 선택적 후처리 또는 명시적 사용자
제어로 구현한다.

우선순위는 다음과 같다.

1. 사용자 안전과 편의
2. Actor/Updater 프롬프트 품질과 관측성
3. 핵심 상태 parity
4. 장기 시뮬레이션 parity
5. 독립 실행·저장소 승격

Wiki 구현으로 아래 표나 상세 TODO의 완료 상태가 바뀌면 이 문서와
`docs/wiki_v2_todo.md`를 같은 변경에서 즉시 갱신한다. prompt, commit, conflict,
vault 계약이 바뀌면 연결된 `WikiRAG/` 문서와 `docs/wiki_v2_format.md`도 함께
갱신한다.

## Graph → Wiki parity 보드

상태는 `완료`, `부분`, `미구현`, `검증 필요`로 기록한다. Graph에 존재하는
위험한 동작은 그대로 복제하지 않고 Wiki 목표 동작에 안전한 정책을 적는다.

| 영역 | Graph 기준 동작 | Wiki 현재 상태 | Wiki parity 목표 | 우선순위 |
| --- | --- | --- | --- | --- |
| 월드·시나리오·대화 생성/열기 | 지원 | 완료 | Graph/Wiki namespace를 분리한 선택·생성·열기 | P0 |
| Actor 모델 선택·스트리밍·중단 | 지원 | 완료 | 공용 provider 스트리밍과 실패 표시 유지 | P0 |
| output guard/repair | 지원 | 완료 | Graph와 동일한 사용자 출력 안전 경계 | P0 |
| Fixed/Genre/Dynamic prompt | 지원 | 완료 — compiled contract, 공용 장면 분류·8종 prompt, 결정적 recall과 문서 수/token 이중 예산 | metadata/wikilink 차단과 prompt snapshot을 유지하는 안정적 3구간 조립 | P0 |
| 최신 미반영 reroll/edit/delete/variant | 지원 | 완료 | 기존 commit 보존 후 성공 시 안전하게 교체 | P0 |
| Wiki commit 상태와 사용자 제어 | 해당 없음 | 완료 | 변경 section·실패 사유·즉시 반영·재시도·건너뛰기 UI | P0 |
| 대화 이름 변경·보관·삭제·내보내기 | 미구현 | 완료 | 이름 변경, 보관/복원, canonical Markdown ZIP, 확인 후 thread 영구 삭제 | P1 |
| 적용된 과거 턴 변경 | 텍스트만 바뀌고 graph는 유지되는 위험한 한계 | 완료 — 최신 applied 턴은 audited inverse, 중간 과거 턴은 이후 commit을 복사본에서 역순 inverse하는 원본 보존 분기 | in-place 과거 편집을 금지하고 원본 보존 분기를 기본 정책으로 유지 | P1 |
| 현재 장면·시각·장소 | GlobalState/DynamicState와 accepted header | 완료 | `scene/current.md` 전체 H2와 accepted header 기반 결정적 시각·장소 guard | P1 |
| 캐릭터 현재 상태·관계 | 다단계 extractor/updater | 완료·장기 LLM 검증 필요 — 현재 5개 시나리오 실제 1턴 deferred/apply 통과 | 현재 상태 source 권한 + Actor-owner 자연어 관계 원장, append-only 보존과 과잉 변화 억제 | P1 |
| Event·Memory 생성 | 새 graph node 생성 | 완료 | durable Event와 owner-private Memory `CreateDocument`, source turn/commit, inverse | P1 |
| Goal·Item·Secret 생성·갱신 | 전용 시스템 | 코드 완료·실제 LLM 검증 필요 — 단일 Updater 생성·갱신·권한, Actor-visible knower-scoping, hidden/suspected 은닉 계약 구현 | 신규 문서 생성, 갱신, visibility와 공개 범위 검증 | P2 |
| usernote·OOC | 지원 | 완료·실제 LLM 검증 필요 | 다음 prompt 반영과 상태 변경 결과 표시 | P2 |
| 변경 이력·감사 | pending/state audit | 완료 — 자동 commit diff/hash, 진단, schema 계약, 상태 migration, baseline 기반 외부 편집 manual archive/inverse | 실시간 watcher는 P3 편의 기능으로 별도 | P2 |
| Memory recall·감쇠·왜곡 | 지원 | 부분 — 결정적 recall과 관계 변화 게이트형 왜곡 구현; 삭제 없는 recall 감쇠·실제 LLM 검증 필요 | Markdown 정본을 보존하는 선택·감쇠·왜곡 정책 | P3 |
| needs·schedule·자율 행동 | 지원 | 코드 완료·실제 LLM 검증 필요 — 결정적 needs vector, 문서 일정, Active pressure/현재 시각 기반 Actor 자기 행동 계약 | 장기 플레이에서 임계·과잉 이탈 검증 | P3 |
| social/Kakao·소문·reputation | 지원 | 부분 — 새 Event의 명시 목격자를 owner-private Memory로 전파하는 기본-off gossip 구현 | Kakao/reputation과 다단 전파는 별도 실측 | P3 |
| personality drift·pregnancy 등 | 지원 | 코드 완료·실제 LLM 검증 필요 — 동적 성격 변화 원장, opt-in 주기/임신 상태, 공용 OOC 결과 경로 구현 | 장기 플레이에서 임계·표현 품질 검증 | P3 |
| World Editor·상태 탐색 | Graph 도구 지원 | 부분 — Explorer 문서 목록 읽기 API 구현(`GET .../wiki/documents`); 편집·diff·시점 복원 UI는 site 후속 | Markdown 편집, diff, 문서 tree와 상태 탐색 | P4 |
| 장기 플레이·비용·지연 검증 | 부분 | 검증 필요 | 5개 현재 시나리오 20턴 이상 기록 | P4 |

## 공동 개발 전환 조건

- [ ] 필수 사용자 기능 parity 행이 모두 `완료`다.
- [ ] 현재 장면, 캐릭터 상태, 관계, Event, Memory, Goal, Item, Secret이 Markdown에 안정적으로 유지된다.
- [ ] 최신·과거 턴 변경과 Obsidian 수동 편집이 상태를 조용히 손상하지 않는다.
- [ ] 다섯 현재 시나리오가 각각 20턴 이상 실제 LLM 검증을 통과한다.
- [ ] Graph/Wiki 공용 Actor, output guard, PromptBuilder 회귀 검증이 통과한다.
- [ ] Wiki 전용 실행과 저장소 분리 여부를 결정할 근거가 확보된다.

## 현재 기준선

- [x] `graphRAG/wiki` 브랜치 생성
- [x] 개발 아키텍처용 `architecture_wiki/`와 플레이 상태용 `wiki_v2/` 분리
- [x] Graph와 Wiki의 대화·usernote namespace 분리
- [x] Wiki world/scenario 발견과 대화 생성
- [x] `start_state.md`를 새 thread의 `scene/current.md`로 물질화
- [x] `opening_scene.md`를 최초 메시지와 첫 턴 문맥으로 전달
- [x] 기존 `PromptBuilder`로 Wiki Fixed/Genre/Dynamic 조립
- [x] Kuzu 없는 Wiki Actor streaming
- [x] Updater 최대 3회 재시도
- [x] 응답 뒤 `commit.md` 보류, 다음 입력 직전 적용
- [x] 다른 섹션의 Obsidian 수동 편집을 보존하는 rebase
- [x] 실제 대화 테스트 표면을 `GraphRAG Chat`으로 확정
- [x] turn debug에 start state 물질화·Dynamic 포함 여부와 Updater 문서 revision 진단 추가
- [x] Wiki commit 상태 조회·즉시 반영·재시도·건너뛰기 백엔드 제어 경로
- [x] 건너뛴 commit을 `skipped` 상태로 `commits/`에 보존
- [x] Actor prompt에서 파일 경로, 관리 문서와 world/scenario/thread 내부 ID 제거
- [x] Actor-visible 문서의 wikilink·Markdown 파일명·frontmatter 필드 누출을 컴파일 시 거부
- [x] Fixed/Genre/Dynamic 필수 태그·배치 계약과 현재 5개 시나리오 prompt hash snapshot 검증
- [x] Graph/Wiki accepted turn을 하나의 mode-aware Updater 공개 진입점으로 통합하고 저장소별 반영기를 지연 분기
- [x] 인물 프로필의 `common`/`default`/활성 분기를 Actor용 평탄 Markdown으로 컴파일
- [x] `babe_university` 주요 인물 설정을 상세화하고 진은서의 일반·절단 상태를 한 원본의 분기로 통합
- [x] 주요 인물의 미정형 문장을 제거하고 출생·가족·거주·학업·일정·취향·관계 이력·목표를 구체적인 정본으로 확장
- [x] 절단 상태의 현재 신체 수치와 납치 조직·일시·장소·구조 경위를 명시
- [x] Wiki world의 `rules.md` 문서 종류를 `prose.md`/`type: prose`로 교체하고 scaffold·loader·schema·tests를 동기화
- [x] `babe_university/world.md`를 가온시·캠퍼스·학과·상권·주거·교통·기술·경제·계절·제도 중심의 세계 사실 문서로 전면 재작성
- [x] `babe_university/prose.md`를 기존 월드 이상의 장면 스케일·카메라·대화·정서 비율·물리 동선·관계·친밀감·간병·시간·종결 규정으로 전면 재작성
- [x] `prose.md`를 PromptBuilder 전용 prose 슬롯으로 분리하고 `world_lore` 중복 삽입을 방지
- [x] 공용 CORE·POV·EMOTION·STYLE·NPC 규정과 겹치던 prose를 작품 전용 차이만 남도록 축약
- [x] 간병 규정은 `amputee_fwb`, 비밀·의심 규정은 `ntr_lite`로 이동해 시나리오 간 전파 차단
- [x] `world.md`·장소·조직의 정본 책임을 분리하고 바베대학교·바베 피트니스·주거 공간의 중복 사실 제거
- [x] 친밀 장면의 현재 동의 규정과 거부 뒤 강제 진행 규정의 충돌 해소
- [x] Wiki Updater patch에 `evidence_source`와 원문 exact-quote 검증 추가
- [x] Updater 시도별 모델 원문·검증 오류 진단과 비정본 `.txt` 저장 경로 추가
- [x] 플레이어 주체 행동과 NPC 행동의 대상 언급, 공동 이동과 이동 제안을 구분
- [x] Actor가 생성한 플레이어 행동의 영속화와 gameplay 정적 프로필 수정을 차단
- [x] `scene/current.md`를 현재 장면 H2 전체 단일 patch로 갱신해 위치·행동 모순 방지
- [x] Actor/NPC 결과와 사용자 입력의 플레이어 사실이 한 장면 H2에 공존할 때 scene 전용 `player_evidence` exact quote로 양쪽 권한을 검증
- [x] 활성 인물의 공유 위치·활동은 scene을 정본으로 삼아 character 중복 patch 차단
- [x] 실제 LLM 격리 하네스로 `lover`와 `best_friends` 1턴의 deferred canonical 불변·pending·apply·과잉 생성 없음 검증
- [x] 나머지 `amputee_fwb`·`ntr_lite`·`altered`도 실제 1턴 deferred/apply를 통과하고 비유의 신체 손상 오인·숨은 사실 공개 생성·시나리오 혼입이 없음을 확인
- [x] 실제 `ntr_lite`에서 확인된 Updater 재시도 진동을 막기 위해 모든 이전 검증 거부 사유를 correction prompt에 누적

## 즉시 검증 — 실제 LLM

- [ ] 최신 `graphRAG/wiki` 로컬 엔진을 실행한다.
- [ ] `GraphRAG Chat`에서 Wiki / `babe_university` / `lover` 새 대화를 만든다.
- [ ] 첫 Actor prompt가 `start_state.md`의 시각, 장소, 관계, 인물 상태를 모두 받는지 turn debug로 확인한다.
- [ ] 첫 응답 뒤 canonical Markdown은 그대로이고 `commit.md`만 생기는지 확인한다.
- [ ] 두 번째 입력 직전에 첫 `commit.md`가 적용되고 `commits/`에 보관되는지 확인한다.
- [ ] Obsidian에서 나이를 수정한 뒤 다음 응답이 새 값을 사용하는지 확인한다.
- [ ] Updater가 `patches: []`을 반환해야 하는 일상 대화를 검증한다.
- [ ] 잘못된 JSON을 유도해 재시도 횟수와 최종 실패 payload를 확인한다.
- [ ] 같은 section을 수동 수정해 충돌 시 사용자 내용을 덮지 않는지 확인한다.
- [ ] 충돌로 failed가 된 commit을 수동 수정 후 즉시 반영하거나 재시도할 수 있는지 확인한다.
- [ ] 건너뛰기 후 원문은 바뀌지 않고 `skipped` 이력만 남는지 확인한다.
- [ ] 20턴 이상 진행하며 사실 누락, 과잉 patch, 입력 token, 비용, 지연을 기록한다.

## WikiRAG 다음 마일스톤

### P0 — 테스트 가능성과 관측성

- [x] Sites UI에서 updater 성공·실패와 변경된 문서/section을 표시한다.
- [x] turn debug에서 start state, 선택 문서, 세 prompt 구간을 쉽게 대조할 수 있게 한다.
- [x] compiled prompt 계약과 현재 5개 시나리오의 Fixed/Genre/Dynamic snapshot 회귀 검증을 고정한다.
- [x] pending 또는 failed `commit.md`의 상태 조회·즉시 반영·재시도·건너뛰기 API를 제공한다.
- [x] Sites UI에 Wiki commit 제어 버튼과 실패 사유·변경 section 표시를 연결한다.
- [x] Sites UI에서 Obsidian 수동 수정 후 상태를 다시 읽는 흐름을 안내한다.
- [x] 새 thread와 이전 구현에서 만들어진 thread를 구분하는 진단을 추가한다.

### P1 — 대화 변경 안전성

- [x] Wiki용 최신 미반영 응답 reroll 정책을 확정한다.
- [x] reroll 성공 후 아직 적용되지 않은 `commit.md`를 skipped 이력으로 보관하고 새 변경안으로 교체한다.
- [x] 이미 적용된 commit의 inverse patch와 수동 수정 보존 규칙을 설계한다.
- [x] 최신 메시지 edit/delete와 응답 버전 선택을 관련 Wiki commit의 폐기·재생성 규칙에 연결한다.
- [x] 3-way 충돌 시 자동 덮어쓰기 대신 사용자 선택을 요구한다.
- [x] 중간 과거 턴은 원본 thread를 유지하고 선택 입력 직전 정본으로 새 thread를 분기한다.
- [x] accepted Actor 헤더의 시간을 역행 없이 반영하고, 날짜 점프와 장소 이동은 사용자 입력의 명시적 근거가 있을 때만 `scene/current.md` 전체 H2에 병합한다.
- [x] durable Event를 검증된 `CreateDocument`로 생성하고 source turn/commit, pending 적용, inverse 삭제·보상 복구를 연결한다.
- [x] Memory의 owner별 Actor visibility와 주관성 계약을 확정하고 `CreateDocument`를 확장한다.
- [x] 활성 Actor→player 관계 문서를 자동 물질화하고 숫자 점수 대신 자연어 durable-change 원장으로 유지한다.
- [x] 관계 갱신을 Actor-owner의 complete H2 patch로 제한하고 기존 기록 삭제와 플레이어 내면 확정을 거부한다.

### P2 — 문서 정합성과 이력

- [x] 정적·동적 사실별 canonical home을 확정하고 Updater에 중복 저장 금지 계약을 적용한다.
- [x] 적용 전·후 hash와 section diff를 commit archive에 기록한다.
- [x] baseline revision과 canonical 전문을 비교해 외부 section·문서 구조·생성·삭제를 별도 applied `manual` commit으로 기록하고 inverse한다.
- [x] 문서 ID 중복과 잘못된 frontmatter를 vault 전체에서 진단한다. (`src/wiki/diagnostics.py`, `GET .../wiki/diagnostics`)
- [x] `schema_version` 마이그레이션 계약을 정한다. (`src/wiki/migrations.py`)
- [x] 기존 thread의 누락된 runtime-owned 캐릭터 상태 H3를 미리 보고 명시적으로 적용하는 audited `manual` migration을 제공한다. (`GET/POST .../wiki/migration`)

### P3 — Obsidian 검색과 변경 감지

- [ ] 파일 watcher와 debounce를 구현한다.
- [ ] 변경된 문서만 다시 파싱한다.
- [ ] ID, type, title, tag index를 만든다.
- [ ] `[[wikilink]]` 해석과 backlink index를 만든다.
- [ ] 전문 검색을 구현한다.
- [ ] 필요성이 확인되면 Markdown 재생성 가능한 embedding index를 추가한다.
- [ ] 외부 저장과 Wiki commit 교체의 동시 경합을 감지한다.

### P4 — 권한과 컨텍스트 선택

- [x] Actor별 private Memory를 owner profile로 격리한다.
- [x] Secret의 Actor별 visibility와 공개 전환 규칙을 확정한다.
- [x] Actor runtime 링크 탐색 깊이는 0으로 고정하고 누적 문서의 문서 수/token 이중 예산을 정한다. (Actor 24/12000, Updater 48/32000; env 조정 가능)
- [x] 관련 기억, 목표, 아이템, 비밀을 선택하는 검색 정책을 구현한다. (`recall.py`, Option A 결정적)
- [x] 허용되지 않은 비밀 공개와 정적 설정 덮어쓰기를 검사한다.

### P5 — 편집·탐색 제품

- [ ] Markdown World Editor에서 원문과 section form 편집을 지원한다.
- [ ] Wiki Explorer에 문서 tree, link, backlink, 검색을 구현한다. (문서 목록 읽기 API `GET .../wiki/documents` 완료; tree/link/검색 UI는 후속)
- [ ] 문서별 diff와 turn commit 연결을 표시한다.
- [ ] 특정 시점 미리보기와 복원을 구현한다.
- [ ] Obsidian vault 설정과 공동 편집 방법을 문서화한다.

### P6 — 독립 엔진·저장소

- [ ] WikiRAG 전용 실행 진입점을 만든다.
- [ ] Kuzu와 Graph Manager에 대한 의존성 감사를 한다.
- [ ] 별도 저장소로 옮길 코드, 문서, 자산 목록을 작성한다.
- [ ] 실제 플레이 결과로 유지할 시뮬레이션 기능만 선별한다.
- [ ] 모델별 품질, 비용, 지연을 비교한다.
- [ ] 독립 저장소 승격 여부를 결정한다.

## GraphRAG 유지보수

- [ ] WikiRAG 변경이 Graph 대화 생성과 deferred Kuzu commit을 깨지 않는 회귀 검증을 유지한다.
- [ ] Graph 전용 도구가 Wiki thread에서 실행되지 않도록 mode guard를 유지한다.
- [ ] Graph와 Wiki의 동일 `world_id`가 usernote나 상태를 공유하지 않는지 검증한다.
- [ ] 공용 Actor streaming 또는 output guard 변경 시 두 엔진을 모두 smoke test한다.
- [ ] 공용 `PromptBuilder` 변경 시 Fixed cache 안정성과 Dynamic 상태 노출을 두 엔진에서 확인한다.

## 결정이 필요한 항목

- [x] WikiRAG 관계는 Graph의 affinity/trust 수치를 복제하지 않고 Actor-owner 자연어 변화 원장으로 유지한다.
- [x] 시간은 accepted header, needs는 결정적 로컬 규칙, 일정은 문서 서술로 경계를 확정한다.
- [ ] 기억 왜곡, 욕구, 소문, 자율 행동을 실제 장기 플레이에서 각각 독립 실험한다. (각 코드 경로 완료)
- [x] 임신 등 특수 시스템은 author opt-in 동적 상태 섹션과 기본-off 게이트로 편입한다.
- [ ] Updater 모델을 Gemini 3.5 Pro로 교체할 가격·품질 기준을 정한다.

## 완료 정의

- [ ] 새 Wiki 대화가 시작 설정을 놓치지 않는다.
- [ ] 사용자 수동 수정이 다음 턴에 반영되고 자동 변경에 조용히 덮이지 않는다.
- [ ] reroll/edit/delete가 상태와 텍스트를 예측 가능하게 되돌린다.
- [ ] 모든 파생 index를 Markdown에서 재생성할 수 있다.
- [ ] GraphRAG와 WikiRAG가 같은 앱에서 서로의 저장소를 침범하지 않는다.
- [ ] WikiRAG를 Kuzu 없이 독립 실행하고 별도 저장소로 옮길 수 있다.
