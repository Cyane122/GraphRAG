---
aliases:
  - Shared Runtime
tags:
  - architecture
  - shared
  - runtime
---

# 공용 런타임

[[GraphRAG]]와 [[WikiRAG]]는 상태 저장소가 다르지만 브라우저 API, 대화 metadata, Actor 호출 기반의 일부를 공유한다.

## 구성 요소

| 계층 | 위치 | 책임 |
| --- | --- | --- |
| Sites UI | `hosted-ui/` | 원격 편집형 채팅 화면, 로컬 엔진 API 호출 |
| Local UI | `frontend/app/` | FastAPI가 직접 제공하는 정적 채팅 화면 |
| API routes | `src/apps/app/app.py` | JSON/NDJSON 계약과 mode guard |
| Conversation service | `src/apps/app/service.py` | Graph/Wiki 진입 분기와 상태 저장 |
| Conversation store | `src/apps/app/storage.py` | thread JSON과 mode별 usernote hydration |
| Actor bridge | `src/apps/app/actor.py` | provider 차이를 숨기는 streaming event |
| Prompt factory | `src/agents/prompt_factory/` | Fixed/Genre/Dynamic 공통 출력 계약 |
| LLM client | `src/core/llm/` | 모델 선택, 동시성, timeout, 429 재시도 |
| Accepted-turn Updater | `src/simulation/state/updater.py` | `mode=graph|wiki` 요청을 받아 저장소별 반영기로 위임 |

## 요청 흐름

```mermaid
sequenceDiagram
    participant UI as Browser UI
    participant API as FastAPI
    participant Store as ConversationStore
    participant Engine as Graph 또는 Wiki service
    participant Actor as Actor LLM
    participant Updater as Mode-aware Updater

    UI->>API: POST /messages/stream
    API->>Store: thread와 world_mode 로드
    API->>Engine: mode별 turn 실행
    Engine->>Actor: Fixed + Genre + Dynamic
    Actor-->>UI: NDJSON token events
    Engine->>Updater: accepted turn + mode
    Engine->>Store: 메시지와 대화 metadata 저장
    Engine-->>UI: complete event
```

## API의 mode 책임

- `world_mode=graph`와 `world_mode=wiki`는 대화 생성부터 목록 조회까지 유지한다.
- Schema, 위치 이동과 임신은 Graph 전용이다. reroll/edit/delete와 응답 버전 선택은 mode별 상태 정책으로 분기한다.
- 동일한 `world_id`라도 mode가 다르면 대화 목록과 usernote namespace가 다르다.
- UI는 엔진 규칙을 결정하지 않고 서버가 제공한 mode와 capability를 표현한다.

## Sites의 위치

실제 대화 테스트 표면은 `GraphRAG Chat`이다. Sites는 정적·서버 렌더링 UI이며 로컬 파일이나 Kuzu를 직접 읽지 않는다. 브라우저가 로컬 FastAPI 엔진에 JSON/NDJSON 요청을 보내므로 다음 두 상태를 구분해야 한다.

1. Sites 배포 상태
2. 사용자의 로컬 엔진 실행 및 코드 버전

Sites가 정상이어도 로컬 엔진이 꺼져 있거나 이전 브랜치를 실행하면 대화 생성과 Wiki materialization은 동작하지 않는다.

## 공용 변경의 검증 규칙

- Actor event 형식을 바꾸면 Graph와 Wiki streaming을 모두 검사한다.
- PromptBuilder를 바꾸면 두 엔진에서 Fixed cache와 Dynamic 상태 포함 여부를 확인한다.
- ConversationState 필드를 바꾸면 저장된 두 mode thread의 하위 호환성을 확인한다.
- usernote/OOC 변경은 mode/world/thread 소유 범위를 다시 확인한다.
- accepted-turn 상태 반영을 추가할 때 mode-aware Updater 요청을 확장하고 Graph/Wiki
  전용 공개 Updater 파일을 따로 만들지 않는다.

관련 경계는 [[Shared/Boundaries|엔진 경계]]에서 다룬다.
