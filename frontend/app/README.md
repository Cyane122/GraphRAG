# GraphRAG Chatbot UI

HTML, CSS, JavaScript만으로 만든 GraphRAG 챗봇 프론트엔드입니다.

## 실행

```bash
python -m http.server 8000
```

브라우저에서 `http://localhost:8000` 접속.

또는 `index.html`을 직접 열어도 됩니다.

## 폰트

Pretendard를 사용합니다.

```html
<link rel="stylesheet" as="style" crossorigin href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css" />
```

CSS에서도 기본 폰트가 Pretendard로 지정되어 있습니다.

## Assistant 응답 포맷

```text
<analyze>
CoT 내용
</analyze>
**YYYY년 MM월 DD일 W요일, HH시 MM분. 장소명**
채팅 내용
```

## OOC 포함 응답 포맷

OOC는 `*이런 식*`의 유저 입력을 LLM으로 해석한 뒤, assistant 응답의 마지막에 상태 기록으로 붙는 구조입니다.

```text
<analyze>
CoT 내용
</analyze>
**YYYY년 MM월 DD일 W요일, HH시 MM분. 장소명**
본문 메시지
<ooc>
OOC 요약
---
state_changes 상세
</ooc>
```

렌더링 순서는 다음과 같습니다.

```text
CoT
본문
OOC 기록
```

OOC 기록은 전체가 접기/펼치기 가능한 패널로 표시되며, 기본 상태에서는 제목만 보입니다. 내부 기록은 로그처럼 줄바꿈을 보존하고, 과한 볼드 서식 없이 작은 글씨로 표시됩니다.

## 리롤 / 수정 / 삭제

이번 버전에는 프론트엔드 상태 배열 기반 액션이 들어가 있습니다. 액션 버튼은 hover 여부와 상관없이 항상 보입니다.

### 리롤

- assistant 메시지에만 표시됩니다.
- 연결된 user 메시지의 내용을 기준으로 assistant 응답을 다시 생성합니다.
- 확인용 딜레이 없이 바로 처리됩니다.

### 수정

- user 메시지와 assistant 메시지 모두 수정할 수 있습니다.
- user 메시지를 수정하면 연결된 assistant 응답도 다시 생성합니다.
- assistant 메시지는 원문 포맷을 직접 수정합니다.
  - `<analyze>`
  - `**시간·장소**`
  - `<ooc>`
  - 본문

### 삭제

- assistant 메시지는 해당 메시지만 삭제합니다.
- user 메시지를 삭제하면 연결된 assistant 메시지도 함께 삭제합니다.

## 로딩 표시

확인용 딜레이는 제거되어 있습니다.

```js
const DEMO_DELAY_MS = 0;
```

실제 백엔드 연결 시에는 서버 스트리밍 이벤트 기준으로 로딩 상태를 제어하면 됩니다.


## Markdown

본문과 유저 메시지에는 간단한 마크다운 렌더링을 적용합니다. OOC 기록은 로그 가독성을 위해 줄바꿈 보존 중심으로 표시합니다.

지원 범위:

- `#`, `##`, `###` 제목
- `**굵게**`, `*기울임*`, `~~취소선~~`
- 인라인 코드와 코드블록
- 순서/비순서 리스트
- 인용문
- 링크
- 구분선


## 세계관 / 시나리오 선택

왼쪽 사이드바에서 세계관과 시나리오를 분리해서 선택합니다.

예시:

```text
world_id: sunghwa_high_school
scenario_id: volleyball_team
```

기존의 `sunghwa_high_school/volleyball_team` 한 줄 선택 대신, 첫 번째 드롭다운에서 `sunghwa_high_school`을 고르고 두 번째 드롭다운에서 `volleyball_team`을 고르는 방식입니다.

## 채팅방 이름 / 미리보기

활성 채팅방 항목은 다음 형식으로 표시됩니다.

```text
세계관 / 시나리오
최근 assistant 본문 일부...
```

assistant 메시지가 생성, 수정, 리롤, 삭제될 때 미리보기도 함께 갱신됩니다.


## 상단 프로필 드롭다운

세계관/시나리오 선택은 채팅창 상단 제목 위치에서 합니다.

표시 형식:

```text
sunghwa_high_school/volleyball_team
```

드롭다운은 별도 화살표 없이 텍스트 자체를 클릭하면 열립니다.


## 분리형 상단 드롭다운

상단 프로필 선택은 `world/scenario`처럼 보이지만 실제로는 두 영역이 따로 동작합니다.

```text
sunghwa_high_school / volleyball_team
^^^^ world 클릭 영역      ^^^^^ scenario 클릭 영역
```

- 세계관 이름을 클릭하면 세계관 목록이 열립니다.
- 시나리오 이름을 클릭하면 현재 세계관에 속한 시나리오 목록만 열립니다.
- 화살표 아이콘은 사용하지 않습니다.
- 상단 세계관 설정 텍스트는 `Multicolore` 폰트를 우선 사용하고, 폰트가 없으면 Pretendard로 대체됩니다.
