# Wiki V2 실제 LLM 수동 테스트 계획

## 0. 테스트 실행 표면

- 모든 실제 대화 테스트는 Sites 프로젝트 `GraphRAG Chat`에서 진행한다.
- 프로젝트 ID: `appgprj_6a5f380e559081918f102b1ec882f5f5`
- 배포 주소: `https://graphrag-fiction-room.cyane123.chatgpt.site`
- Sites는 원격 채팅 화면이고 Wiki Markdown과 LLM 실행은 로컬 FastAPI 엔진이 담당한다. 테스트 전 로컬 엔진이 최신 `graphRAG/wiki` 코드를 사용해 실행 중인지 확인한다.
- 새 Wiki 대화를 생성한 뒤 테스트한다. 구현 변경 전에 생성된 thread는 이전에 물질화된 `scene/current.md`를 계속 사용하므로 시작 설정 검증 표본으로 재사용하지 않는다.

이 계획은 자동 mock 테스트와 실제 Gemini 호출을 분리한다. 실제 LLM 품질과
비용을 판단하는 테스트는 사용자가 수행하며, 각 케이스에서 원문 응답,
`commit.md`, 적용 전·후 Markdown을 함께 보존한다.

## 1. 현재 구현된 사항

### Markdown 저장소

- UTF-8 Markdown과 엄격한 YAML frontmatter 로딩
- H2 이하의 고유한 제목 경로 파싱
- 특정 섹션 전체 교체와 제목·경계 검증
- 문서 전체 및 대상 섹션 SHA-256 revision 검증
- 대상 외 섹션의 수동 편집을 보존하는 section-level rebase
- 같은 대상 섹션의 수동 편집과 Updater 변경 충돌 감지
- 임시 파일 교체 방식의 안전한 저장

### Vault와 문서 규격

- `worlds/<world_id>`와 `threads/<thread_id>`의 분리된 디렉터리 구조
- 15종 world/thread 문서 템플릿
- 기존 문서를 덮어쓰지 않는 스캐폴드
- 중단된 스캐폴드의 동일 hash 기반 재개
- thread 생성 시 참조 world 검증
- symlink/junction을 통한 vault 경로·world/thread alias 방지

### Unified Wiki Updater

- 관련 문서 전문, 사용자 입력, 확정된 Actor 응답을 한 번의 모델 요청으로 전달
- 변경된 섹션만 `SectionPatch`로 받는 구조화 출력
- 제공하지 않은 문서, 없는 섹션, 오래된 revision, 중복 대상 거부
- 근거 없는 patch와 confidence 0.55 미만 patch 거부
- 기본 최대 3회 재시도
- 성공한 변경안을 대상 문서에 즉시 쓰지 않고 `commit.md`에 보류
- 다음 사용자 입력 시 적용 후 `commits/<commit_id>.md`에 보관
- 동일 커밋 재적용 방지와 부분 적용 후 재시작 복구

### 현재 앱에 연결된 범위

- Wiki/Graph 모드와 world/usernote 저장 범위 분리
- `wiki_v2/worlds/*/world.md` 기반 Wiki 월드 목록 탐색
- Wiki 월드를 `runtime_ready: true`로 노출하고 Wiki conversation/thread 생성
- Graph 전용 도구가 Wiki thread에 실행되지 않도록 차단
- `start_state.md`를 thread의 `scene/current.md`와 character 문서로 물질화
- 최신 Markdown을 읽어 기존 `PromptBuilder`의 Fixed/Genre/Dynamic 구간 조립
- Kuzu와 Graph Manager 없이 기존 Actor 스트리밍 경로 사용
- Actor 응답 뒤 Gemini Pro Updater를 최대 3회 재시도하고 `commit.md` 생성
- 다음 사용자 입력 직전에 이전 `commit.md` 적용 및 이력 보관
- Wiki commit 상태 조회, 다음 입력 전 즉시 반영, 마지막 확정 턴 재시도, 건너뛰기 API
- failed commit 재시도 전 기존 실패본과 건너뛴 commit을 `skipped` 이력으로 보존
- 대화 JSON에 updater 성공·실패·pending commit ID를 저장하고 실제 `commit.md`를 우선해 상태 조회
- 실제 Actor·Updater 호출을 제외한 전체 lifecycle 스모크 테스트

## 2. 아직 구현되지 않은 사항

### 검색과 변경 알림

- Obsidian/World Editor 파일 변경 이벤트를 UI에 전달하는 감지와 debounce
- 변경 문서 증분 재파싱
- 문서 ID·type·제목·tag 인덱스
- `[[wikilink]]`, 역링크, 깨진 링크 진단
- 전문 검색과 임베딩 검색
- 동일한 `actor` visibility 안에서 현재 Actor별로 비밀·기억을 구분하는 필터링

### 편집과 이력

- 새 문서 생성 patch와 LLM 기반 문서 생성
- rename/archive와 중복 ID 진단
- 적용 전·후 hash 및 diff를 포함한 완전한 commit 기록
- 수동 편집을 별도 commit으로 기록
- reroll/edit/delete와 Wiki commit의 연결 및 inverse patch
- 수동 수정 이후 reroll하는 3-way merge UI
- 외부 편집기 저장과 `os.replace` 사이의 OS-level CAS 경합 해결

### 제품 UI

- Wiki V2 전용 채팅 런처
- Markdown 기반 World Editor
- Wiki Explorer의 문서 트리, 검색, 링크, 변경 이력, 시점 복원
- Markdown 본문을 직접 고치는 World Editor
- 적용 전·후 section의 행 단위 diff와 시점 복원 탐색기

Sites의 `Wiki 상태 검토`에는 상태 조회, 명시적 갱신, 즉시 반영, 재시도,
건너뛰기, inverse 충돌 선택과 변경 문서·section·근거 표시가 연결되어 있다.
정본 Markdown 직접 편집과 전체 변경 이력 탐색은 아직 전용 UI가 없다.

## 3. 실제 LLM 테스트 전 준비

1. 테스트용 thread vault를 복사해 원본 world를 보존한다.
2. Updater 모델 ID를 Gemini 3.1 Pro로 고정한다.
3. temperature는 `0.0`, 최대 시도 횟수는 `3`으로 유지한다.
4. 케이스마다 다음 자료를 한 폴더에 보존한다.
   - 사용자 입력
   - 확정된 Actor 응답
   - Updater에 제공한 문서 전문과 revision
   - 모델의 각 시도 원문
   - 생성된 `commit.md`
   - 적용 전·후 문서와 diff
   - 지연 시간, 입력·출력 token, 예상 비용
5. 성공 여부와 별개로 사실 누락, 과잉 변경, 비밀 누출을 기록한다.
6. 첫 턴은 `summary.md`의 `start_state_materialized`와 `start_state_in_dynamic_prompt`가 모두 `true`인지 먼저 확인한다.

## 4. 구현된 기능의 실제 LLM 테스트

### T1 — 단일 섹션의 명시적 사실 변경

- 입력 예: 캐릭터가 새 직업을 확정하는 장면
- 기대: 해당 캐릭터의 `직업과 소속` 섹션 하나만 patch
- 실패: 다른 성격·외형·관계 섹션까지 재작성

### T2 — 여러 문서의 한 턴 변경

- 입력 예: 두 캐릭터가 다투고 현재 장면의 장소도 바뀌는 장면
- 기대: 두 캐릭터/관계/현재 장면의 필요한 섹션만 patch
- 확인: 전체 턴에 Updater 모델 호출은 정상 경로에서 1회

### T3 — 변화가 없는 일상 대화

- 입력 예: 기존 설정을 반복하는 짧은 대화
- 기대: `patches: []`; 의미 없는 이벤트나 기억을 만들지 않음

### T4 — 비유와 실제 신체 변화 구분

- 입력 예: “심장이 찢어지는 것 같았다” 같은 비유
- 기대: 감정은 필요 시 갱신하지만 신체 손상 사실은 추가하지 않음

### T5 — 수동 편집 후 unrelated rebase

- 절차: `commit.md` 생성 후 다른 섹션의 나이를 직접 수정하고 다음 입력
- 기대: 수동 수정은 유지되고 Updater의 원래 대상 섹션만 적용

### T6 — 수동 편집과 동일 섹션 충돌

- 절차: `commit.md` 생성 후 patch 대상 섹션을 직접 수정
- 기대: 사용자 내용을 덮지 않고 failed commit으로 남김

### T7 — 잘못된 JSON과 재시도

- 절차: 응답 형식이 흔들리기 쉬운 복합 변경을 요청
- 기대: 형식/검증 실패 시 최대 3회 재시도하고 유효한 결과만 queue
- 기록: 각 실패 이유와 최종 시도 횟수

### T8 — 허용되지 않은 문서·섹션 변경

- 절차: 관련 문서 일부만 제공하되 응답에서 다른 인물을 언급
- 기대: 제공하지 않은 문서 경로를 수정하지 않음. 반환하면 전체 결과 거부

### T9 — 비밀 누출 억제

- 절차: Actor에게 비공개 secret 문서를 주지 않고 공개 단서만 제공
- 기대: 비밀의 실제 내용을 추측해 공개 상태로 확정하지 않음
- 주의: visibility context filter가 아직 없으므로 현재는 입력 문서를 수동 선별

### T10 — 장기 연속성

- 절차: 같은 thread에서 20턴 이상 진행
- 기대: 과거 확정 사실을 유지하고 같은 사실을 여러 canonical home에 복제하지 않음
- 측정: 턴별 patch 수, 잘못된 변경, 누락, 누적 token과 비용

### T11 — 최신 Markdown의 다음 턴 반영

- Obsidian에서 나이를 수정한 뒤 앱을 재시작하지 않고 다음 메시지 전송
- 기대: 다음 Actor prompt와 응답이 새 나이를 사용

### T12 — 완전한 지연 커밋 lifecycle

- Actor 응답 수신 직후 대상 문서는 그대로이고 `commit.md`만 존재
- 다음 사용자 입력 직전에 이전 변경이 적용된 뒤 새 Actor context를 구성

### T13 — commit 상태 조회

- 첫 응답 직후 `GET /api/conversations/{thread_id}/wiki/commit` 실행
- 기대: `update_status: queued`, 실제 commit ID, updater 시도 횟수, 변경 대상 문서와 `section_path` 표시
- Updater가 재시도를 모두 소진한 경우 기대: `update_status: failed`, 오류 표시, 적용 가능한 `commit.md` 없음

### T14 — 다음 채팅 전 즉시 반영

- pending 상태에서 `POST /api/conversations/{thread_id}/wiki/commit/apply` 실행
- 기대: 대상 Markdown이 즉시 변경되고 `commit.md`는 사라지며 applied archive가 생성됨
- 그 뒤 채팅을 보내도 같은 commit이 중복 적용되지 않아야 함

### T15 — 변경안 건너뛰기

- pending 상태에서 변경 대상 원문을 기록한 뒤 `POST .../wiki/commit/skip` 실행
- 기대: 원문은 그대로이고 `commit.md`는 사라지며 `commits/<id>.md`가 `status: skipped`로 남음
- 다음 채팅은 건너뛴 변경을 Actor 문맥에 포함하지 않아야 함

### T16 — 충돌 실패와 Updater 재시도

- pending patch의 대상 section을 Obsidian에서 수정한 뒤 `POST .../wiki/commit/apply` 실행
- 기대: HTTP 409, 사용자 수정 보존, 상태 조회에서 `failed`와 충돌 사유 표시
- 이어서 `POST .../wiki/commit/retry` 실행
- 기대: 기존 failed commit은 skipped 이력으로 보존되고, 최신 수동 수정 문서를 기준으로 새 pending commit 생성
- 정상 pending에 곧바로 retry하면 덮어쓰지 않고 먼저 반영/건너뛰기를 요구해야 함

## 5. 미구현 기능의 향후 인수 테스트

### U1 — reroll/edit/delete 안전성

- 생성 → Wiki 적용 → 수동 수정 → 과거 응답 reroll 순으로 진행
- 기대: 수동 수정이 사라지지 않고 충돌 시 사용자 선택을 요구

### U2 — 검색과 링크

- 관련 기억이 여러 문서에 흩어진 장면에서 필요한 문서만 선택
- 기대: 선택 근거, 링크 이동, 역링크, 깨진 링크 진단을 재현 가능하게 표시

### U3 — 권한 경계

- 서로 다른 캐릭터의 private memory와 secret을 준비
- 기대: 현재 Actor가 알 수 없는 문서는 prompt에 포함되지 않음

### U4 — 종료와 복구

- Updater 실행, 문서 적용, archive 생성의 각 단계에서 프로세스를 강제 종료
- 기대: 재시작 후 중복 적용이나 사용자 문서 손실 없이 pending 상태를 복구

## 6. 판정 기록 형식

각 케이스는 `PASS`, `FAIL`, `BLOCKED`로 기록한다.

- `PASS`: 기대한 canonical section만 정확히 변경
- `FAIL`: 사실 오류, 과잉 변경, 누락, 비밀 누출, 사용자 수정 손실 중 하나 발생
- `BLOCKED`: 해당 기능이나 필요한 UI가 아직 구현되지 않아 실행 불가

MVP 후보 품질 기준은 다음 실험 후 수치로 확정한다.

- 명시적 사실 변경 정확도
- 불필요한 patch 비율
- 필요한 변경 누락률
- 동일 섹션 충돌 시 사용자 수정 보존률
- 정상 턴당 모델 호출 수
- Updater 평균·p95 지연
- 턴당 token과 비용

## 7. babe_university 첫 실제 LLM 테스트 순서

1. 다섯 시나리오를 각각 새 thread로 시작하고 선택한 `scenario.md`의 `시나리오 특징`과 `시나리오 한정 묘사 규정`만 Fixed prompt에 포함되는지 확인한다.
2. `start_state.md`가 초기 thread 상태를 만들고 `opening_scene.md`의 원문만 첫 장면으로 사용되는지 확인한다.
3. 첫 장면이 시안의 대사·행동·감정·의도를 대신 확정하지 않고 플레이어 입력을 기다리는지 확인한다.
4. `best_friends`에서 변화 없는 일상 대화로 T3을 먼저 실행한다.
5. `lover`에서 은서의 직장 출발과 현재 장면 변경으로 T1·T2를 실행한다.
6. `ntr_lite`에서 한도준에게 공개되지 않은 관계가 다른 문서로 누출되지 않는지 T9를 실행한다.
7. `amputee_fwb`에서 비유/신체 사실 구분과 장애·트라우마의 과잉 갱신 여부를 T4로 확인한다.
8. `altered`에서 진은서 1인칭 시점과 상식 재배치 설정이 다른 시나리오로 누출되지 않는지 확인한다.
9. 마지막으로 각 시나리오의 thread vault를 분리해 20턴 연속성 T10을 실행한다.

첫 장면 판정 시에는 시각·장소·은서의 상태가 해당 시나리오 전제와 일치하고,
다른 평행 시나리오의 연애·사고·비밀 관계가 섞이지 않는지도 함께 확인한다.

다섯 시나리오는 평행 세계이므로 같은 thread나 character runtime 문서를 공유하지
않는다. 공통 world profile은 읽기 원본이며 플레이 중 변화는 scenario별 thread
문서에만 기록한다.

## 8. 2026-07-25 자동 실제 LLM 검증 기록

`scripts/run_wiki_llm_validation.py`로 운영 vault와 대화 저장소를 임시 경로에
격리하고, 실제 scene classifier·Actor·공용 mode-aware Updater·deferred commit
적용 경로를 실행했다.

| 시나리오 | 결과 | 확인 사항 | 보존 자료 |
| --- | --- | --- | --- |
| `lover` | PASS(수정 후 회귀) | 생성 직후 canonical 불변, pending 생성, apply 성공, `characters/eun_seo.md`와 `scene/current.md`만 변경, Event/Memory 생성 없음 | `docs/wiki_llm_runs/20260725T132437_984249Z_lover/` |
| `best_friends` 최초 | FAIL(결함 재현) | complete scene H2의 Actor/NPC 결과와 사용자 입력 플레이어 사실을 단일 evidence로 표현하지 못해 3회 검증 실패 | `docs/wiki_llm_runs/20260725T132743_742492Z_best_friends/` |
| `best_friends` 수정 후 | PASS | scene 전용 `player_evidence` exact quote로 한 번에 성공, canonical deferred 불변, apply 성공, `scene/current.md`만 변경, Event/Memory 생성 없음 | `docs/wiki_llm_runs/20260725T133303_001783Z_best_friends/` |
| `amputee_fwb` | PASS | 비유적 “가슴이 찢어지는 것 같다”를 플레이어 신체 손상으로 상태화하지 않음, `scene/current.md`만 변경, 생성 없음 | `docs/wiki_llm_runs/20260725T134321_062896Z_amputee_fwb/` |
| `ntr_lite` 최초 | FAIL(결함 재현) | 위치 중복→축약 evidence→잘못된 H2 patch로 재시도 오류가 진동; 숨은 사실 누출이나 canonical 적용은 없음 | `docs/wiki_llm_runs/20260725T134444_289862Z_ntr_lite/` |
| `ntr_lite` 수정 후 | PASS | 모든 이전 거부 사유를 correction prompt에 누적; valid pending/apply, 현재 장면과 은서 상태만 변경, 생성 없음 | `docs/wiki_llm_runs/20260725T134847_065761Z_ntr_lite/` |
| `altered` | PASS | 진은서 1인칭 시점 유지, 현재 장면·은서 상태·결정적 needs만 변경, 다른 평행 시나리오 사실 및 생성 없음 | `docs/wiki_llm_runs/20260725T135016_922443Z_altered/` |

`lover` 실행에서는 1분의 결정적 needs tick이 작성자의 `Condition: stable`을
일반 문구로 덮는 문제도 발견했다. 수치와 pressure만 다시 계산하고 기존
`Condition`을 보존하도록 수정했으며 smoke 회귀를 추가했다. 이 표는 실제 1턴
검증 근거일 뿐이며, 다섯 시나리오 각 20턴의 M9 장기 검증을 완료로 판정하지 않는다.
`ntr_lite` 최초 실패에서는 재시도마다 직전 오류만 전달할 때 서로 다른 정책 오류가
되살아나는 현상을 확인했다. correction prompt가 서로 다른 이전 거부 사유를 모두
누적하도록 바꾸고 해당 동작을 smoke로 고정했다.
