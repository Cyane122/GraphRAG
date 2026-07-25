# Site(hosted-ui) 변경 스펙 — WikiRAG parity

> 대상: `hosted-ui/`(Sites 호스팅 채팅 클라이언트)를 수정하는 site 팀.
> 목적: 백엔드 WikiRAG parity 구현(`docs/wiki_parity_roadmap.md`)에 맞춰 site가
> **추가·보완해야 할 UI/소비 로직**을 마일스톤별로 정리한다. 구현이 진행되며 갱신된다.
> 백엔드는 로컬 FastAPI 엔진, 통신은 JSON + NDJSON 스트리밍.

---

## 1. 현재 site 구현 상태 (기준선)

1. **graph / wiki 월드 구분 실행** — 대화 생성 시 `world_mode`로 분기.
2. **graph 채팅 대부분 기능** — OOC, 유저노트 등. (제외: 임신·질내사정·현재 월드 상황 표시)
3. **이번 턴 Wiki 업데이트 commit 표기** — 확정 응답 뒤 변경 문서/섹션 표시.

---

## 2. 백엔드가 이미 제공하는 계약 (site 소비 대상)

site 팀이 참조할 실제 엔드포인트·payload. **아래는 이미 백엔드에 존재**하므로 site는
소비/렌더/버튼 연결만 하면 된다.

### 2.1 Wiki commit 제어 엔드포인트 (`src/apps/app/app.py`)

| Method | Path | 역할 |
| --- | --- | --- |
| GET | `/api/conversations/{tid}/wiki/commit` | 현재 updater 상태 + commit payload 조회 |
| POST | `/api/conversations/{tid}/wiki/commit/apply` | 대기 중 commit 즉시 반영 |
| POST | `/api/conversations/{tid}/wiki/commit/retry` | 최신 확정 턴으로 updater 재실행 |
| POST | `/api/conversations/{tid}/wiki/commit/regenerate` | 현재 변경안을 보존하고 최신 확정 턴으로 새 commit 생성 |
| POST | `/api/conversations/{tid}/wiki/commit/skip` | 현재 변경안 미적용 보관(`{reason}`) |
| GET | `/api/conversations/{tid}/wiki/commits/{cid}/inverse` | applied commit inverse 계획(쓰기 없음) |
| POST | `/api/conversations/{tid}/wiki/commits/{cid}/inverse/apply` | 충돌 없는 inverse 적용 |
| GET | `/api/conversations/{tid}/wiki/migration` | 기존 thread 상태 계약 migration 미리보기(쓰기 없음) |
| POST | `/api/conversations/{tid}/wiki/migration/apply` | 승인된 상태 계약 보강을 audited manual commit으로 적용 |
| GET | `/api/conversations/{tid}/wiki/manual-audit` | 외부 Markdown 변경 미리보기(쓰기 없음) |
| POST | `/api/conversations/{tid}/wiki/manual-audit/record` | 외부 변경을 applied manual archive로 즉시 기록 |
| POST | `/api/conversations/{tid}/wiki/branch/{mid}` | 과거 턴 직전으로 안전 분기 |
| PATCH/POST | `.../wiki/rename`, `.../wiki/archive`, GET `.../wiki/export`, DELETE | 이름변경·보관·ZIP·삭제 |

> **확인 요청:** 현재 site가 "commit 표기"만 있다면, 위 제어(재시도·건너뛰기·즉시반영·
> inverse·분기) 버튼이 아직 안 붙었을 수 있다. 백엔드는 준비돼 있으니 **버튼 연결 여부를
> 먼저 점검**해 달라.

### 2.2 commit payload 형태 (`WikiCommitStatusResponse.commit`)

`GET .../wiki/commit`의 `commit` 필드 = `PendingWikiCommit` 직렬화. 핵심 필드:

```jsonc
{
  "commit_id": "…",
  "status": "pending|failed|applied|skipped",
  "operation": "update|inverse|manual",
  "summary": "한국어 변경 요약",
  "patches": [                       // 섹션 교체
    { "document": "scene/current.md",
      "section_path": ["현재 장면"],
      "replacement_markdown": "…",
      "evidence": "…", "evidence_source": "actor_response|player_input",
      "confidence": 0.95 }
  ],
  "creations": [                     // 신규 문서 (full markdown)
    { "document": "events/xxx.md", "content": "---\n…", "confidence": 0.9 }
  ],
  "deletions": [ … ],
  "failure_reason": "…"              // status=failed 시 사유
}
```

- **문서 종류는 `document` 경로 접두사로 판별**한다: `scene/`, `characters/`,
  `relationships/`, `events/`, `memories/`, 그리고 **M1 이후** `goals/`, `items/`,
  `secrets/`. (또는 `creations[].content`의 frontmatter `type:` 파싱.)
- payload **형태는 마일스톤이 늘어도 바뀌지 않는다.** 새 문서 종류가 `patches`/
  `creations`에 추가로 등장할 뿐이다. → site의 commit 렌더러는 **문서 종류에 관대하게**
  설계하고, 모르는 종류는 "기타 문서 변경"으로 fallback 표시하면 미래 마일스톤에 안전하다.

### 2.3 상태 필드 (`ConversationState`)

- `wiki_update_status`: `idle|queued|failed|applied|skipped`
- `wiki_update_error`, `wiki_pending_commit_id`
- 메시지별 `wiki_commit_id` (응답 ↔ commit 연결)
- `WikiCommitStatusResponse.wiki_thread_generation`: `current|legacy|missing` +
  `wiki_thread_diagnostic` — 이전 구현 thread 경고 표시에 사용.

---

## 3. 마일스톤별 site 델타

### M1 — Goal·Item·Secret (최우선, 진행 중)

백엔드가 단일 Updater로 goal/item/secret 문서를 **생성·갱신**하면, 그 변경이 그대로
commit payload의 `creations`/`patches`에 실린다. **payload 형태는 안 바뀐다.**

**site가 할 일:**

1. **commit 표기에 신규 문서 종류 렌더링** `[필수]`
   - `goals/`·`items/`·`secrets/` 경로를 인식해 라벨·아이콘 추가
     (예: 🎯 목표, 🎒 아이템, 🤫 비밀).
   - secret reveal은 `secrets/*.md`의 `## 공개 상태` 섹션 patch로 온다 →
     "비밀 공개" 같은 상태 전이 라벨로 표시.
   - 모르는 종류 fallback("기타 문서 변경")이 있으면 이 항목은 자동 처리됨 — 라벨만 개선.

2. **비밀·기억 스포일러 노출 정책** `[결정 필요]`
   - commit 패널은 **플레이어가 보는 화면**이다. secret/memory 문서의 본문
     (`actual_content`, `remembered_content` 등)을 그대로 펼치면 **플레이어에게 스포일러**가
     될 수 있다.
   - secret 문서 `visibility`는 `[updater, player]`(player 허용)이지만, 표시 여부는
     제품 판단이다. 권장: commit 패널에서 secret/memory 생성은 **"비밀/기억 생성됨"
     헤더만 접힌 상태로** 보여주고, 본문은 기본 접기(펼치기 선택). → **site 팀 결정 요청.**

3. **그 외 변경 없음.** knower-scoping(어떤 비밀·목표를 Actor가 아는가)과 secret reveal
   판단은 **전부 백엔드** 처리다. site는 결과 commit만 표시.

### M2 — 변경 이력·감사 (P2)

- **vault 진단 패널** `[신규·백엔드 완료]`: 아래 엔드포인트가 추가됐다. site는 결과를
  진단 패널로 표시하면 된다.
  - `GET /api/conversations/{tid}/wiki/diagnostics`
    → `{ "diagnostics": [ { "level": "error", "code": "frontmatter|sections|duplicate_id",
    "path": "world/…|thread/…", "message": "…" } ] }`
  - 빈 배열이면 무결성 정상. `code`별로 아이콘/필터를 붙이면 좋다.
- **manual commit 구분 표시** `[백엔드 완료]`: `operation: "manual"` commit을 자동
  commit과 시각적으로 구분한다. 기존 thread 상태 migration과 외부 Obsidian 편집이
  manual archive로 기록된다. 외부 편집은 다음 턴 전에 자동 기록되며 위 API로 즉시
  미리보기·기록할 수도 있다.
- **기존 thread migration 안내** `[신규]`: GET 결과가 `ready`이면 변경 문서와 complete
  H2 patch를 보여주고 적용 확인 버튼을 제공한다. `up_to_date`는 숨기거나 정상 배지,
  `conflict`는 기존 commit 처리 또는 문서 구조 수정을 안내한다.
- commit별 before/after diff 보기(선택): `applied_changes`의 before/after markdown 활용.

### M3 — Memory recall (P3)

- 주로 백엔드. site 영향 낮음(디버그 뷰 정도).

### M4~M7 — 시간/needs/감쇠/소문/성격/임신 (P3)

- 대부분 백엔드 상태 변화 → commit 표기로 자동 반영.
- **임신·질내사정**은 현재 site 제외 상태 — Wiki에서도 M7(후순위)이라 당장 site 작업 없음.
  편입 시 Graph처럼 OOC 메시지 채널로 전달 예정(별도 스펙).

### M8 — 상태 탐색·편집 (P4)

- **Wiki Explorer 문서 목록** `[신규·백엔드 완료]`: Explorer 트리를 만들 읽기 API가
  추가됐다.
  - `GET /api/conversations/{tid}/wiki/documents`
    → `{ "documents": [ { "scope": "world|thread", "path": "…", "type": "character|scene|event|goal|item|secret|…",
    "id": "…", "title": "…", "visibility": ["actor",…], "owner": "character_profile:…|null" } ] }`
  - `scope` → `type` → `path`로 정렬돼 있다. 이 목록으로 문서 tree/타입별 목록을 그린다.
- **Markdown 편집 UI / link·backlink / diff / 시점 복원** `[대형·별도]`: 문서 본문 편집,
  링크 탐색, 변경 이력 diff, 특정 시점 복원. 별도 스펙 문서로 분리 예정(문서 본문 읽기·
  쓰기 API는 추후 추가).

---

## 4. 요약 — site가 지금 당장 봐야 할 것

1. **[점검]** 2.1의 commit 제어 엔드포인트(재시도·건너뛰기·즉시반영·inverse·분기)가
   site에 버튼으로 연결돼 있는지. 백엔드는 이미 준비됨.
2. **[M1 필수]** commit 렌더러를 문서 종류에 관대하게 만들고 goal/item/secret 라벨 추가.
   모르는 종류 fallback이 있으면 대부분 자동 처리.
3. **[M1 결정]** commit 패널의 secret/memory 본문 스포일러 노출 정책(기본 접기 권장).

> 이후 각 마일스톤 착수 시 이 문서의 해당 절을 구체화한다. payload 형태는 유지되므로
> M1의 "관대한 렌더러 + fallback" 설계가 이후 마일스톤 부담을 크게 줄인다.
