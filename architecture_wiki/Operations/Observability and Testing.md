---
aliases:
  - WikiRAG Observability
  - Test Surface
tags:
  - architecture
  - operations
  - testing
---

# 관측성과 테스트

## 실제 대화 표면

- Sites 프로젝트: `GraphRAG Chat`
- 프로젝트 ID: `appgprj_6a5f380e559081918f102b1ec882f5f5`
- 주소: `https://graphrag-fiction-room.cyane123.chatgpt.site`
- 실행 조건: 최신 `graphRAG/wiki` 로컬 FastAPI 엔진

Sites 배포와 로컬 엔진은 별도 상태다. 실제 테스트 기록에는 Sites version과 로컬 branch/commit을 함께 남기는 편이 좋다.

## Turn debug 산출물

| 파일 | 확인할 내용 |
| --- | --- |
| `fixed_prompt.txt` | 다른 scenario 누출, 동적 상태 혼입 |
| `genre_prompt.txt` | 선택된 장면 규칙 |
| `dynamic_prompt.txt` | 시작 설정, 최신 수동 수정, 사용자 입력 |
| `history.json` | 실제 대화 history와 usernote 반복 여부 |
| `metadata.json` | 모델, scene type, prompt 길이, Wiki context 진단 |
| `summary.md` | 사람이 빠르게 보는 핵심 진단 |
| `raw_full.txt` | Actor 원문 전체 |
| `raw_output.txt` | 사용자에게 보인 prose |

Wiki 턴의 `summary.md`와 `metadata.json`에는 다음 진단 필드가 기록된다.

- `scene_document`, `scene_revision`
- `start_state_materialized`
- `start_state_in_dynamic_prompt`
- Updater에 전달된 각 문서의 path, type, revision, visibility

## 시작 설정 점검

1. 반드시 새 Wiki thread를 만든다.
2. 첫 메시지를 보내 materialization과 prompt 조립을 실행한다.
3. Dynamic prompt에 `## 시작 기준`이 존재하는지 확인한다.
4. scenario의 시각, 장소, 관계, 인물 상태가 scene 문서와 prompt에 모두 있는지 비교한다.
5. opening prose가 Fixed에 들어가지 않고 recent story에만 있는지 확인한다.

## Deferred commit 점검

1. 첫 Actor 응답 직후 `commit.md`가 존재하는지 확인한다.
2. canonical 문서가 아직 바뀌지 않았는지 확인한다.
3. 두 번째 입력 직전 patch가 적용되는지 확인한다.
4. `commits/<commit_id>.md` archive와 status를 확인한다.
5. 같은 section 수동 편집 시 failed 상태로 남고 사용자 내용을 보존하는지 확인한다.

## 자동 smoke와 실제 LLM의 역할

### 자동 smoke

- parser, revision, patch, rollback
- PromptBuilder 조립과 scenario 격리
- start state의 Dynamic 포함
- deferred commit 순서
- updater 형식 오류 재시도

### 실제 LLM

- 사실 변경 정확도와 누락
- 비유/물리 상태 구분
- 무변화 턴의 빈 patch
- 장기 연속성
- 입력·출력 token, 비용, 지연

실제 LLM 품질 판정은 사용자가 수행하고 결과를 [[TODO#즉시 검증 — 실제 LLM]]에 반영한다.
