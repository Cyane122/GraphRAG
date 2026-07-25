---
aliases:
  - WikiRAG Turn Pipeline
tags:
  - architecture
  - wikirag
  - turn
---

# WikiRAG 턴 파이프라인

## 대화 생성

1. `world.md`에서 PC/NPC profile, POV, rating을 읽는다.
2. 선택한 scenario의 필수 3문서를 검증한다.
3. thread scaffold를 만든다.
4. `start_state.md`를 `scene/current.md`에 물질화한다.
5. world와 scenario character profile을 thread character로 복사한다.
6. 활성 Actor가 플레이어를 보는 자연어 관계 변화 원장을 빠진 경우 생성한다.
7. `opening_scene.md`를 최초 assistant 메시지로 저장한다.

## 일반 턴

```mermaid
sequenceDiagram
    participant UI
    participant Service as Wiki Service
    participant Queue as Commit Queue
    participant Vault as Markdown Vault
    participant Prompt as PromptBuilder
    participant Actor
    participant Updater

    UI->>Service: 사용자 입력
    Service->>Queue: 이전 commit.md 적용
    Queue->>Vault: revision 검증 후 section write
    Service->>Vault: 최신 문서 전체 재조회
    Service->>Prompt: Wiki context adapter
    Prompt->>Actor: Fixed + Genre + Dynamic
    Actor-->>UI: token streaming
    Service->>Updater: 사용자 입력 + Actor 응답 + 문서 전문
    Updater-->>Service: SectionPatch + Event/Memory CreateDocument
    Service->>Service: accepted 헤더 시각·장소 hard guard
    Service->>Queue: 새 commit.md 보류
    Service-->>UI: complete + updater status
```

## 순서 불변식

- 이전 commit 적용은 새 prompt를 만들기 전에 끝난다.
- Actor가 보는 문서는 적용 이후 다시 읽은 최신 Markdown이다.
- Actor streaming 중 canonical 문서를 바꾸지 않는다.
- Updater 실패는 Actor 응답 자체를 잃게 하지 않는다.
- accepted 헤더는 시간 역행을 만들지 않으며, 날짜 점프와 장소 이동에는 사용자 입력 근거가 필요하다.
- 안전한 헤더 변경은 모델의 scene patch 유무와 관계없이 현재 장면 H2에 결정적으로 병합된다.
- 새 Event와 owner-private Memory는 source turn/commit이 연결된 canonical 문서로 렌더링되고 section patch와 함께 지연 적용된다.
- 관계 변화는 현재 Actor-owner 원장의 complete H2에 append-only로 보류되며 기존 durable 기록과 플레이어 권한을 보존한다.
- 새 commit은 다음 사용자 입력 전까지 canonical 문서에 적용하지 않는다.

## 외부 편집

파일 watcher가 아직 없어도 일반 턴마다 파일을 다시 읽는다. 따라서 Obsidian에서 저장한 변경은 다음 메시지의 prompt에 반영된다. 향후 watcher는 UI 알림과 index 증분 갱신을 위한 기능이지 최신 Markdown을 읽기 위한 필수 조건은 아니다.

## 현재 제한

- 최신 미반영 턴과 후속 턴 없는 최신 applied 턴은 reroll/edit/delete/variant를
  지원한다. 중간 과거 턴은 원본을 직접 고치지 않고 안전한 새 thread로 분기한다.
- Failed commit은 상태 카드에서 재시도하거나 건너뛸 수 있다. 같은 section 수동
  편집과 inverse가 충돌하면 자동으로 쓰지 않고 현재 상태를 유지한다.
- 동시에 두 메시지를 보내는 thread-level 경쟁 정책은 명시적으로 제공하지 않는다.
