# Wiki V2 단계별 TODO

> Branch: `graphRAG/wiki`  
> 방향: Kuzu를 사용하지 않고 Markdown vault를 세계 상태의 유일한 원본으로 삼는 별도 V2 엔진  
> 현재 우선순위와 parity 원칙은 `.ai/active.md` 및 `.ai/initiatives/wikirag-migration.md`를 참조한다. 이 문서와 `architecture_wiki/TODO.md`가 구현 상태의 정본이다.

## 확정된 원칙

- [x] 기존 GraphRAG와 분리된 `graphRAG/wiki` 브랜치에서 개발한다.
- [x] 장기적으로 별도 저장소로 승격할 수 있게 기존 그래프 엔진과의 결합을 최소화한다.
- [x] Markdown 문서가 유일한 원본이다.
- [x] 링크·전문 검색·임베딩 데이터는 Markdown에서 다시 만들 수 있는 캐시로 취급한다.
- [x] Obsidian과 향후 전용 World Editor가 같은 vault를 직접 편집한다는 제품 원칙을 확정한다. World Editor 연결 자체는 Phase 8 범위다.
- [x] 외부에서 수정된 Markdown을 매 턴 다시 읽어 재시작 없이 반영한다. watcher와 변경 알림은 Phase 3 범위다.
- [x] Updater에는 관련 문서 전문을 주고, 변경된 섹션만 돌려받는다.
- [x] 기본 Updater 모델은 Gemini 3.1 Pro로 시작하고, 추후 가격과 품질에 따라 교체 가능하게 한다.
- [x] 기존 기능의 완전한 복제보다 Wiki V2에 맞는 단순한 구조와 품질을 우선한다.
- [x] 기존 Kuzu 데이터 변환기는 MVP 필수 범위에 넣지 않는다.

## 현재 구현 상태

- [x] 표준 world/thread vault 스캐폴드와 기존 문서 덮어쓰기 방지
- [x] 중단된 스캐폴드의 동일 hash 기반 재개와 vault 경로 탈출 방지
- [x] YAML frontmatter 로딩과 content hash revision 통합
- [x] 15종 Markdown 문서 템플릿과 구체적인 H2/H3 제목 규격
- [x] Markdown H2/H3/H4 제목 경로 파싱과 섹션 단위 교체
- [x] content hash 기반 문서·대상 섹션 revision 충돌 감지
- [x] 다른 섹션의 수동 편집을 보존하는 section-level rebase
- [x] Gemini Updater 구조화 출력 검증과 최대 횟수 재시도
- [x] 검증된 변경안을 `commit.md`에 보류
- [x] 다음 사용자 입력 시 patch 적용 후 `commits/<commit_id>.md`로 보관하는 공개 API
- [x] turn debug에서 start state 물질화·Dynamic 포함 여부와 Updater 입력 문서 revision 확인
- [x] pending/failed commit 상태 조회·즉시 반영·재시도·건너뛰기 백엔드 API
- [x] 건너뛴 commit을 `skipped` archive로 보존
- [x] 기존 thread의 누락된 욕구·성격 변화 원장·생식 상태 H3를 쓰기 없이 미리 보고 audited `manual` commit으로 명시 적용
- [x] 외부 Markdown 편집을 baseline과 비교해 턴 시작 전에 별도 applied `manual` archive로 기록하고 section·문서·생성·삭제 inverse를 지원
- [x] `prose.md`를 PromptBuilder 전용 prose 슬롯으로 분리하고 `babe_university`의 world/location/organization/scenario 정본 책임을 정리
- [x] 시나리오 전용 간병·비밀 규정을 해당 `scenario.md`로 이동하고 prompt 격리 스모크를 추가
- [x] Updater evidence 출처와 exact quote를 검증하고 Actor 유래 플레이어 상태 patch를 차단하며, complete scene 혼합 사실은 scene 전용 `player_evidence` exact quote로 검증
- [x] 현재 장면 전체를 단일 H2 patch로 갱신하고 활성 인물 위치·활동 중복을 차단
- [x] accepted Actor 헤더의 시각을 역행 없이 동기화하고 날짜 점프·장소 이동의 사용자 근거를 검사
- [x] durable Event를 구조화된 `CreateDocument`로 생성하고 적용·inverse·보상 복구
- [x] owner-private Memory를 같은 `CreateDocument`·commit·inverse 경로로 생성
- [x] gameplay Updater의 정적 캐릭터 섹션과 thread 관리 문서 수정을 차단
- [x] Actor-visible 문서의 wikilink·Markdown 파일명·frontmatter 필드 누출과 Fixed/Genre/Dynamic 경계 위반을 컴파일 시 거부
- [x] 현재 5개 시나리오의 compiled prompt hash snapshot과 Fixed cache 안정성 회귀 검증
- [x] Graph/Wiki accepted turn을 `mode=graph|wiki` 단일 Updater API로 통합하고 Wiki mode에서 Graph/Kuzu 반영기를 로드하지 않는다.
- [x] Goal·Item·Secret을 단일 Updater의 `CreateDocument`/`SectionPatch`로 생성·갱신하고 owner/knower 권한, 정체성 read-only, Actor-visible secret knower-scoping과 hidden/suspected 은닉 계약을 검증
- [x] hidden/suspected Secret의 실제 내용이 Actor 출력에 직접 누설되면 Wiki 출력 guard가 repair하거나 generic 오류로 차단하고, 공개 단서는 허용한다.
- [x] accepted header 경과 시간으로 Actor-owner `Needs` 벡터를 결정적으로 갱신하고 안전 수치는 명시적 사건 없이 자동 증가시키지 않는다.
- [x] 결정적 Needs 갱신이 작성자 `Condition` 서술을 보존하고 수치·pressure만 교체하도록 검증한다.
- [x] 기본-off 장기 시스템 게이트로 관계 변화 기반 Memory 왜곡, Event 목격자 gossip Memory, 성격 변화 원장, opt-in 생식 상태와 공용 OOC 반환을 단일 Updater 뒤에 연결한다.
- [x] 실제 LLM 격리 하네스로 `lover`·`best_friends` 1턴의 deferred canonical 불변, pending commit, apply 성공과 무의미한 Event/Memory 미생성을 검증한다.
- [x] `amputee_fwb`·`ntr_lite`·`altered`까지 현재 5개 시나리오 실제 1턴 deferred/apply와 시나리오별 위험 경계를 검증한다.
- [x] Updater correction prompt에 모든 이전 검증 거부 사유를 누적해 위치 중복·exact quote·section 범위 수정이 재시도 사이에서 진동하지 않게 한다.
- [x] 임시 vault smoke 검증

---

## Phase 0 — V2 경계와 성공 조건 고정

- [ ] V2에서 유지할 최소 기능을 확정한다.
  - 현재 장면, 시간, 위치
  - 캐릭터 정적 설정과 현재 상태
  - 관계
  - 이벤트와 캐릭터별 기억
  - 목표, 아이템, 비밀의 기본형
- [ ] 초기 제외 기능을 명시한다.
  - 욕구 자동 감소
  - 성격 수치 drift
  - 기억 감쇠·왜곡
  - 소문 자동 전파
  - 복잡한 스케줄 tick
  - 자율 행동
  - 별도의 유기적 시뮬레이션
- [x] Actor 응답 뒤에는 대상 문서를 수정하지 않고 `commit.md`만 만들며, 다음 사용자 입력 직전에 적용한다.
- [x] Updater 실패 시 최대 시도 횟수까지 재시도하고, 모두 실패하면 적용 가능한 `commit.md`를 만들지 않는다.
- [x] 최신 미반영 응답의 reroll, 메시지 수정, 버전 선택, 삭제 시 Wiki 커밋 교체 정책을 확정하고 구현한다.
- [x] 수동 편집 이후 applied commit은 inverse/3-way 판정하며, 후속 턴이 있는 중간 과거 응답은 원본을 유지한 새 thread 분기로 이어간다.
- [ ] MVP 품질 지표를 정한다.
  - 턴당 모델 호출 수
  - Actor 종료부터 Wiki 갱신 완료까지의 지연
  - 잘못된 변경 및 누락 비율
  - 수동 수정이 다음 턴에 반영되는 시간
  - reroll/rollback 복구 성공률

**완료 조건:** 구현 도중 제품 정책을 추측하지 않아도 되는 짧은 V2 범위 문서가 존재한다.

---

## Phase 1 — Markdown 문서 규격 설계

- [x] vault의 표준 디렉터리 구조를 확정한다.

```text
wiki_v2/
├─ worlds/<world_id>/
│  ├─ world.md
│  ├─ prose.md
│  ├─ scenarios/<scenario_id>/
│  │  ├─ scenario.md
│  │  ├─ start_state.md
│  │  └─ opening_scene.md
│  ├─ characters/
│  ├─ locations/
│  └─ organizations/
└─ threads/<thread_id>/
   ├─ scene/
   ├─ characters/
   ├─ relationships/
   ├─ events/
   ├─ memories/
   ├─ goals/
   ├─ items/
   ├─ secrets/
   └─ commits/
```

- [x] 문서 종류별 frontmatter 공통 필드를 정의한다.
  - `id`
  - `type`
  - `schema_version`
  - `visibility`
  - 필요 시 `world_id`, `thread_id`, `profile_id`, `owner`, `participants`, `created_at`
- [x] Updater가 바꾸는 동적 상태는 frontmatter와 중복하지 않고 본문 섹션에 둔다.
- [x] revision은 frontmatter에 중복 저장하지 않고 문서 전체 content hash로 계산한다.
- [x] Markdown 제목 계층을 문서 스키마로 정의한다.
  - `#`: 문서의 고유 대상
  - `##`: 큰 정보 영역
  - `###`: 독립적으로 교체 가능한 기본 수정 단위
  - `####`: 필요한 경우에만 사용하는 세부 수정 단위
- [x] 같은 문서 안에서 동일한 `section_path`가 생기지 않도록 제목 규칙을 정한다.
- [x] `기타`, `정보`, `설정`처럼 단독으로 의미가 불분명한 leaf 제목을 금지한다.
- [x] 제목에는 현재 값이 아니라 의미를 적도록 한다.
  - 허용: `### 나이와 생년월일`
  - 금지: `### 나이: 24세`
- [x] 섹션 범위를 “해당 제목부터 다음 동급 또는 상위 제목 직전까지”로 정의한다.
- [x] 정적·동적 사실의 canonical home 표를 고정하고 Updater가 같은 사실을 여러 문서에 복제하지 않게 한다.
- [x] 캐릭터, 관계, 이벤트, 기억, 목표, 아이템, 비밀 문서 템플릿을 작성한다.
- [x] Actor prompt 조립은 `[[wikilink]]`를 탐색하지 않고 Actor-visible 본문에 남아 있으면 거부한다.
- [ ] 저작·Wiki Explorer용 `[[wikilink]]` ID 해석과 상대 경로 규칙을 정의한다.
- [x] 비밀 및 캐릭터별 주관적 기억의 `visibility`를 owner/knower profile 기준으로 정의하고 Actor prompt·출력 guard에 적용한다.
- [x] `schema_version: 1` 이후 문서의 type/version 단계 registry와 실패 계약을 정의한다. 실제 version 2 변환은 해당 스키마 변경 시 등록한다.

**완료 조건:** 예제 월드 하나를 코드 없이 Markdown만으로 완전하게 표현할 수 있다.

---

## Phase 2 — Wiki 저장소 핵심 구현

- [x] 그래프 코드에 의존하지 않는 `src/wiki/` 독립 패키지 경계를 정한다.
- [x] `WikiDocument` 경계 모델을 정의한다.
- [x] UTF-8 Markdown 및 frontmatter 로더를 구현한다.
- [x] 제목 트리와 `section_path` 파서를 구현한다.
- [x] 특정 섹션 조회 및 교체 기능을 구현한다.
- [ ] 문서 생성, 이름 변경, 보관 기능을 구현한다.
- [x] content hash revision과 `expected_revision` 비교로 오래된 쓰기를 거부한다.
- [x] 임시 파일 후 교체 방식의 안전한 단일 문서 쓰기를 구현한다.
- [x] 여러 문서 변경을 메모리에서 검증한 뒤 하나의 논리 커밋으로 적용한다.
- [x] 적용 전·후 section 원문과 hash를 `commits/`에 기록한다.
- [x] canonical baseline 밖의 외부 편집을 자동 commit과 구분된 `operation: manual` archive로 기록한다.
- [x] 다중 문서 적용 중 실패하면 이 호출에서 이미 쓴 문서를 복구한다.
- [x] 잘못된 frontmatter와 중복 섹션 경로 진단을 구현한다.
- [x] 중복 문서 ID 진단을 구현한다.

**완료 조건:** LLM 없이도 테스트 코드가 여러 문서의 섹션을 안전하게 변경하고 되돌릴 수 있다.

---

## Phase 3 — 실시간 변경 감지와 인덱스

- [ ] vault 파일 감지기를 구현한다.
- [ ] 연속 저장 이벤트를 합치는 debounce 정책을 적용한다.
- [ ] 변경된 문서만 다시 파싱하는 증분 갱신을 구현한다.
- [ ] ID, 문서 종류, 제목, 태그 인덱스를 구현한다.
- [ ] `[[wikilink]]`와 역링크 인덱스를 구현한다.
- [ ] 전문 검색 인덱스를 구현한다.
- [ ] 임베딩 인덱스를 선택하고 Markdown에서 재생성 가능하게 구현한다.
- [ ] 문서 변경 시 관련 검색·임베딩 캐시만 무효화한다.
- [ ] Obsidian과 World Editor의 동시 수정 충돌을 감지한다.
- [ ] 외부 편집기의 저장과 Wiki commit 파일 교체가 동시에 일어나는 OS-level CAS 경합을 충돌본 보존 또는 quiet-window 정책으로 해결한다.
- [ ] 문법 오류가 있는 사용자 문서를 자동 덮어쓰지 않고 진단으로 표시한다.
- [ ] 변경 이벤트를 채팅 UI와 Wiki Explorer에 전달할 스트림을 구현한다.

**완료 조건:** Obsidian에서 나이를 수정하면 실행 중인 앱이 재시작 없이 이를 감지하고 다음 컨텍스트 조회에서 새 값을 반환한다.

---

## Phase 4 — Wiki 컨텍스트 선택과 Actor 연결

- [x] Actor가 항상 읽는 고정 문서를 정의한다.
  - 세계 규칙
  - 활성 시나리오
  - 출력 및 역할 연기 규칙
- [x] 매 턴 읽는 동적 문서를 정의한다.
  - 현재 장면
  - 활성 캐릭터
  - 활성 캐릭터 사이의 관계
  - 최근 이벤트
- [x] 검색으로 선택하는 문서를 정의한다. (누적 문서 recall: 결정적 최근성·구조 관련성 + 문서 수/token 이중 예산 트리거, `src/wiki/recall.py`)
  - 관련 기억
  - 목표, 아이템, 비밀
  - 연결된 장소와 조직 (장소·조직 링크 탐색은 후속)
- [x] Actor runtime 링크 탐색 깊이를 0으로 고정하고 누적 문서의 토큰 예산을 정한다. (`WIKI_ACTOR_RECALL_TOKEN_BUDGET=12000`, `WIKI_UPDATER_RECALL_TOKEN_BUDGET=32000`)
- [x] `visibility`에 `actor`가 없는 thread/static 문서를 Actor 컨텍스트에서 제외한다.
- [x] `thread.md`, 파일 경로, revision과 world/scenario/thread 내부 ID를 Actor prompt에서 제외한다.
- [x] 인물 프로필의 `common`/`default`/활성 분기를 선택하고 선택기 제목을 제거한 Markdown만 Actor에게 전달한다.
- [x] scenario 작성 문서의 포장 제목을 제거하고 현재 적용되는 사실과 묘사 규정만 의미 기반 XML로 전달한다.
- [x] Fixed / Genre / Dynamic 구분을 Wiki 구조에 맞게 단순화한다.
- [x] Graph와 같은 공용 rule/LLM 장면 분류를 Wiki 턴에 연결하고, 분류 라벨을 실제 8종 scene prompt asset으로 정규화한다.
- [x] 비어 있던 daily/bonding/formal/tense/conflict/action/ambient 공용 장면 prompt를 연속성·과잉 전개 방지 계약으로 채운다.
- [x] Fixed prose 1회 포함, Dynamic current scene/user input 1회 포함과 가변 상태의 Fixed 유입 금지를 실행 시 검증한다.
- [x] 선택된 updater 문서와 세 prompt 구간을 기존 turn debug에 기록한다.
- [x] start state의 thread 물질화와 Dynamic prompt 포함 여부를 turn debug summary에서 진단한다.
- [x] Kuzu 없이 Actor 응답을 생성하는 최소 채팅 경로를 연결한다.

**완료 조건:** 예제 vault만으로 한 턴을 생성하고, 사용자가 수정한 Markdown 내용이 Actor 응답에 반영된다.

---

## Phase 5 — Unified Wiki Updater

- [x] 모델 ID를 중앙 설정의 `MODEL_PRO_UPDATER`로 분리한다.
- [x] Gemini 3.1 Pro를 지정할 수 있는 updater 호출 경로를 구현한다.
- [x] updater 입력을 정의한다.
  - 사용자 입력
  - 확정된 Actor 응답
  - 관련 문서 전문
  - 각 문서의 ID와 revision
  - 허용된 수정 및 생성 범위
- [x] `SectionPatch` 출력 모델을 정의한다.
  - `document_id`
  - `base_revision`
  - `section_path`
  - `replacement_markdown`
  - `evidence`
  - `evidence_source`
  - `confidence`
- [x] durable Event 신규 문서를 위한 `CreateDocument` 출력 모델과 canonical renderer를 정의한다.
- [x] Event ID·경로 중복, exact quote, confidence와 Actor 유래 플레이어 행동을 검증한다.
- [x] Event frontmatter에 source commit과 user/assistant message ID를 기록한다.
- [x] pending commit에서 새 문서를 배타적으로 생성하고 inverse 시 미수정 문서만 삭제하며 inverse의 inverse로 복구한다.
- [x] Memory 생성의 owner·private visibility·주관적 서술 계약을 확정하고 `CreateDocument`를 확장한다.
- [x] Actor 응답은 현재 Actor owner, player 입력은 player owner의 Memory만 생성하도록 근거 권한을 강제한다.
- [x] Actor prompt에는 현재 NPC profile owner의 Memory만 포함하고 Updater 입력에는 모든 owner 문서를 유지한다.
- [x] Memory의 관련 Event ID는 검증에만 쓰고 Actor-visible 본문에는 사람이 읽는 Event 제목을 렌더링한다.
- [x] 새 thread와 관계 문서가 없는 기존 thread에 활성 Actor→player 관계 변화 원장을 물질화한다.
- [x] 관계 상태는 affinity/trust 수치 없이 Actor owner 관점의 자연어 durable-change bullet로 유지한다.
- [x] 관계 patch를 complete `Relationship Development` H2와 `actor_response` 근거로 제한한다.
- [x] 기존 durable 관계 bullet 삭제·의역과 Actor 근거의 플레이어 행동·내면 확정을 거부한다.
- [x] Actor prompt에는 현재 NPC owner의 관계 문서만 포함하고 Updater에는 전체 관계 문서를 유지한다.
- [x] 모델이 제공되지 않은 문서나 임의 경로를 수정하지 못하게 한다.
- [x] 변경된 섹션만 출력하도록 구조화 출력과 프롬프트를 구현한다.
- [x] 각 변경에 비어 있지 않은 근거를 요구한다.
- [x] evidence가 지정한 사용자 입력 또는 Actor 응답의 exact quote인지 검사한다.
- [x] Actor 응답에서 플레이어 행동·상태를 추출하지 못하게 한다.
- [x] gameplay Updater의 정적 캐릭터 설정 덮어쓰기를 차단한다.
- [x] 현재 장면은 H2 전체를 한 patch로 교체해 내부 정합성을 유지한다.
- [x] validated Updater 결과 뒤 accepted Actor 헤더를 결정적으로 병합하고, 모델이 scene patch를 생략해도 안전한 시각·장소 변경만 별도 patch로 만든다.
- [x] 같은 날짜의 시간 전진만 기본 허용하고, 다음 날 이후 이동은 사용자 입력의 명시적 시간 점프가 있을 때만 허용한다.
- [x] 장소는 현재 장소 유지 또는 사용자 입력이 헤더 장소를 구체적으로 언급한 경우에만 변경한다.
- [x] 검증 실패 재시도에 직전 거부 사유를 전달해 동일한 잘못된 JSON 반복을 줄인다.
- [x] 시도별 요청·모델 원문·검증 오류를 thread 진단 폴더에 비정본 파일로 보존한다.
- [x] NPC 행동의 대상인 플레이어 언급과 플레이어 주체 행동을 구분한다.
- [x] 공동 이동과 `함께 가자` 같은 제안을 구분하고 scene H2 legacy 별칭을 안전하게 정규화한다.
- [ ] 허용되지 않은 비밀 공개와 존재하지 않는 링크를 검사한다.
- [x] 검증을 통과한 patch만 `commit.md`에 보관할 수 있게 한다.
- [x] 정상 처리 시 전체 턴에 하나의 updater 호출을 사용하는 기본 함수를 만든다.
- [x] Graph와 Wiki 앱 호출자가 동일한 mode-aware accepted-turn Updater 요청 모델을 사용한다.
- [x] updater 실패, JSON 손상, timeout 재시도 이후의 사용자 선택 UI를 만든다.
- [x] updater 실패 이후 재시도·건너뛰기와 commit 즉시 반영 백엔드 경로를 만든다.
- [x] 모델 ID를 바꿔도 저장소나 patch 계약이 달라지지 않도록 분리한다.

**완료 조건:** Actor 응답 하나가 한 번의 모델 호출과 검증된 SectionPatch를 통해 관련 문서만 갱신한다.

---

## Phase 6 — 턴 커밋, reroll, 수동 수정 충돌

- [x] Actor 응답 직후 WikiPatch를 생성해 thread의 `commit.md`로 연결한다.
- [x] Actor 스트리밍 뒤 Wiki 갱신 진행 상태를 NDJSON status event로 전달한다.
- [x] Updater가 성공하거나 재시도를 소진할 때까지 complete event를 보류한다.
- [x] 최신 미반영 응답 reroll 시 기존 `commit.md`를 보존한 채 Actor 재생성을 먼저 완료하고, 성공 후 이전 변경안을 skipped 이력으로 교체한다.
- [x] 최신 사용자/응답 수정, 응답 버전 선택과 삭제가 연관된 Wiki commit을 폐기·재생성하도록 구현한다.
- [x] 수동 수정은 baseline 비교를 통해 별도의 deterministic `manual` commit으로 기록한다.
- [x] 모델 변경 뒤 수동 수정이 있을 때 inverse patch의 3-way 충돌을 탐지한다.
- [x] 충돌이 없으면 수동 수정을 보존한 채 모델 변경만 되돌린다.
- [x] 충돌이 있으면 어떤 Markdown도 쓰지 않고 Sites 비교 화면에서 사용자가 현재 상태 유지를 선택하게 한다.
- [x] 중간 과거 턴을 선택하면 이후 applied commit을 복사본에서 역순 inverse하고, 원본을 보존한 턴 직전 thread와 사용자 입력 초안을 만든다.
- [x] 동일 turn commit 재적용을 막는 idempotency를 구현한다.
- [x] 앱 재시작 후 pending/failed commit을 다시 읽을 수 있게 한다.
- [x] pending commit의 덮어쓰기를 막고 failed commit 재시도 전 기존 실패본을 보관한다.

**완료 조건:** 생성 → Wiki 갱신 → 수동 수정 → reroll의 전체 흐름에서 사용자의 수정이 조용히 사라지지 않는다.

---

## Phase 7 — V2 채팅 런처

- [ ] 기존 앱과 분리된 V2 실행 진입점을 만든다.
- [ ] 월드와 vault 선택 화면을 만든다.
- [ ] 스레드 생성, 열기, 보관 기능을 만든다.
- [x] 기존 FastAPI 앱에서 Wiki Actor 응답 스트리밍을 구현한다.
- [x] Wiki 갱신 중 상태와 완료 payload의 성공·실패 값을 전달한다.
- [x] 갱신 결과에서 어떤 문서와 섹션이 바뀌었는지 표시한다.
- [x] 구현된 commit 재시도·건너뛰기·즉시 반영 API를 Sites UI에 연결한다.
- [x] 기존 reroll, 메시지 수정, 응답 버전 선택, 삭제 UI를 Wiki 최신 미반영 턴 정책에 연결한다.
- [x] Obsidian 정본 수정은 다음 메시지에서 자동 재로딩된다는 안내와 commit 상태 새로고침을 Sites UI에 표시한다.
- [x] 새 thread에 Actor 비가시 런타임 표식을 남기고 이전 구현 thread를 API와 turn debug에서 진단한다.
- [x] 최신 applied Wiki 응답을 inverse한 뒤 edit/delete/reroll/variant 교체로 이어가고, 리롤 실패 시 inverse를 보상 적용한다.
- [x] 메시지와 commit을 ID로 연결하고 applied commit의 inverse 계획·적용·충돌 비교를 Sites UI에 제공한다.
- [x] 메시지의 `여기서 분기`에서 과거 턴 직전 Wiki 정본을 새 대화로 복원하고 선택 입력을 초안에 되살린다.
- [x] Wiki 대화 이름 변경, 보관/복원, 대화 JSON+canonical Markdown ZIP 내보내기와 확인 후 영구 삭제를 제공한다.
- [ ] 로컬 파일 접근이 필요한 MVP는 로컬 런처로 완성한다.
- [ ] 향후 Site 형태로 옮길 수 있도록 UI와 로컬 저장소 API 경계를 분리한다.

**완료 조건:** 기존 GraphRAG 런처를 실행하지 않고 Wiki V2만으로 채팅을 진행할 수 있다.

---

## Phase 8 — World Editor와 Obsidian 동시 지원

- [ ] World Editor가 Markdown 문서를 직접 열고 저장하게 한다.
- [ ] frontmatter와 제목 계층을 보존하는 편집 방식을 구현한다.
- [ ] 문서 템플릿으로 캐릭터·관계·장소 등을 생성한다.
- [ ] 섹션별 폼 편집과 원문 Markdown 편집을 모두 제공한다.
- [ ] 외부 편집 감지 및 새로고침 안내를 구현한다.
- [ ] 저장 전 revision 충돌을 검사한다.
- [ ] 깨진 링크, 중복 ID, 모호한 섹션 제목을 편집기에서 표시한다.
- [ ] Obsidian에서 편집할 때 필요한 vault 설정과 사용법을 문서화한다.

**완료 조건:** 같은 문서를 Obsidian과 World Editor 어느 쪽에서 수정해도 데이터 손실 없이 앱에 반영된다.

---

## Phase 9 — Wiki Explorer

- [ ] 문서 트리와 타입별 목록을 구현한다.
- [ ] Markdown 렌더링과 원문 보기를 구현한다.
- [ ] 링크와 역링크 탐색을 구현한다.
- [ ] 캐릭터, 관계, 장소 연결을 시각화한다.
- [ ] 변경 이력과 문서별 diff를 표시한다.
- [ ] turn commit과 변경된 Wiki 섹션을 연결해서 보여준다.
- [ ] 검색 결과와 기억 검색 근거를 표시한다.
- [ ] 깨진 링크, 중복 ID, 권한 문제를 진단 화면에 표시한다.
- [ ] 특정 시점의 문서 상태를 미리 보고 복원하는 기능을 만든다.

**완료 조건:** 그래프 DB 없이도 세계의 연결 관계, 현재 상태, 변경 이유를 탐색할 수 있다.

---

## Phase 10 — 기존 시뮬레이션 기능 재평가

- [ ] 실제 플레이 로그를 바탕으로 빠진 기능을 목록화한다.
- [ ] 각 기능을 다음 중 하나로 분류한다.
  - Markdown 상태와 Unified Updater로 흡수
  - 결정적 로컬 규칙으로 재구현
  - 선택적 후처리 모델로 유지
  - V2에서 제거
- [x] 시간·needs는 결정적 로컬 규칙, schedule은 문서 서술로 유지하는 1차 경계를 확정한다.
- [x] 관계 수치는 유지하지 않고 Actor-owner 자연어 durable-change 원장으로 표현한다.
- [ ] 관계 변화 게이트형 기억 왜곡이 플레이 품질에 기여하는지 실제 LLM 장기 플레이로 평가한다. (코드·스모크 완료)
- [ ] 결정적 욕구, 성격 변화 원장, 목격자 소문, Actor 자기 행동을 하나씩 독립 실험한다. (코드·스모크 완료)
- [x] 임신 등 특수 시스템은 author opt-in `Reproductive State`, 기본-off 실행 게이트, 공용 OOC 결과로 격리한다.
- [ ] 호출 수와 품질 이득이 확인된 기능만 정식 편입한다.

**완료 조건:** 기존 기능을 습관적으로 이식하지 않고, V2에 필요한 기능만 근거를 가지고 선택한다.

---

## Phase 11 — 품질 검증과 독립 저장소 준비

- [x] 문서 파서, 섹션 교체, revision, rollback을 `tests/smoke_wiki_v2.py`에서 검증한다.
- [x] 현재 5개 시나리오의 Fixed/Genre/Dynamic SHA-256 snapshot과 Fixed cache 안정성을 `tests/smoke_wiki_runtime.py`에서 검증한다.
- [ ] 파일 감지와 동시 수정 시나리오를 검증한다.
- [x] Actor와 updater를 포함한 장기 플레이 smoke 시나리오를 만든다. (`scripts/run_wiki_long_play.py` — 사람이 작성한 Markdown 턴 스크립트를 격리 임시 저장소에서 무인 연속 실행하고, 턴마다 생성 중 canonical 무변경(deferred) 불변식을 검증한다. LLM 없는 회귀는 `tests/smoke_wiki_long_play.py`. 다만 2026-07-26 결정으로 기본 검증 경로는 실제 플레이이며, 이 하네스는 좁은 회귀 재현용 보조 도구다.)
- [ ] 잘못된 사실 추가, 기존 사실 누락, 비밀 누출을 평가한다. (실제 플레이 중 `commits/` archive의 section diff로 관찰하고 판정은 사람이 한다.)
- [ ] 문서 수와 턴 수가 늘어날 때 검색 품질과 지연을 측정한다.
- [x] 모델별 비용, 입력·출력 토큰, 지연을 기록한다. (`logs/llm_latency.jsonl`이 플레이 중 호출별 지연과 prompt/output/thought/total 토큰을 자동으로 남긴다. 실측 집계는 미수행.)
- [ ] Gemini 모델 교체 smoke check를 만든다.
- [ ] 기존 Kuzu 모듈을 참조하지 않는지 의존성 감사를 한다.
- [ ] 독립 저장소로 옮길 코드, 문서, 정적 자산 목록을 만든다.
- [ ] 기존 GraphRAG 전용 파일을 새 저장소에 끌고 가지 않도록 정리한다.
- [ ] 별도 저장소 승격 여부를 최종 결정한다.

**완료 조건:** Wiki V2가 Kuzu 없이 독립 실행되고, 별도 저장소로 옮길 수 있는 명확한 경계를 가진다.

---

## 권장 구현 순서 요약

1. Phase 0의 정책 결정
2. Markdown 문서 규격과 예제 vault
3. LLM 없는 Wiki 저장소와 rollback
4. 실시간 파일 감지와 인덱스
5. Wiki 기반 Actor 한 턴
6. Unified Wiki Updater
7. reroll 및 수동 수정 충돌 처리
8. 독립 V2 채팅 런처
9. World Editor와 Wiki Explorer
10. 실제 플레이 후 시뮬레이션 기능 선별 복원
11. 품질 검증 및 독립 저장소 승격

## MVP 완료 정의

- [x] Kuzu를 시작하지 않고 Wiki V2 채팅을 실행할 수 있다.
- [x] 예제 월드를 Markdown 문서만으로 작성할 수 있다.
- [x] Actor가 매 턴 최신 Markdown 내용을 다시 읽는다.
- [x] 정상 경로에서 한 번의 Gemini updater 호출이 변경된 섹션만 반환한다.
- [x] 검증된 변경이 `commit.md`에 보류되고 다음 사용자 입력 직전에 Markdown에 반영된다.
- [x] Obsidian에서 한 수동 수정이 재시작 없이 다음 턴 prompt에 반영된다.
- [ ] 최신 미반영 응답 reroll의 기존 commit 보존은 구현됐으며, Obsidian 수동 수정과 함께하는 실제 LLM E2E에서 최종 확인한다.
- [ ] Wiki Explorer에서 링크, 역링크, 변경 이력을 확인할 수 있다.
- [ ] 모든 상태 인덱스는 Markdown에서 재생성할 수 있다.
