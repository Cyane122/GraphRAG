# P2 Plan — World Editor (Codex 인수인계용)

> 이 문서는 Claude Code(`branch: Gemini`)에서 작성한 **자기완결적** 작업 지시서다.
> Codex가 이 문서만 읽고 P2를 구현할 수 있도록, 정확한 파일 경로·줄번호·함수명·함정을
> 모두 적어 두었다. 줄번호는 작성 시점(2026-06-02) 기준이며, 구현 전 해당 함수를 직접 열어 확인할 것.

---

## 0. 배경 — 지금까지 (P1·P3·P4·P5·P6 완료)

World Editor는 `python world_editor.py` → http://127.0.0.1:8765 (FastAPI+uvicorn, 단일 `world_editor.html` 프론트).
2026-05-31 대규모 스펙(12절)의 대부분이 이미 구현됐다. 완료된 핵심:

- **cfg 패턴이 런타임 기반**: `src/assets/worlds/base_character.py` `Character.build_schema`가
  `self.cfg`(`DEFAULT_CFG` + `SCENARIO_OVERRIDES[sid]` 병합)를 읽어 노드/4-tier 프로파일을 생성하는
  cfg-driven 구현이다(이전엔 `NotImplementedError`). 손글씨 캐릭터도 마이그레이션 후 `super().build_schema()`를 호출.
- **마이그레이션 엔진** `src/ui/world_editor/migrate.py`: 손글씨(imperative) → cfg 패턴 변환.
  verify-by-recompile(모든 시나리오 재컴파일 후 그래프 byte-identical일 때만 적용)이 안전 핵심.
  **sunghwa_high_school 14개 캐릭터 적용 완료**(`.bak` 백업 보존).
- **통합 캐릭터 cfg 에디터**(tier 탭 + 시나리오 탭), Item/Goal/Secret CRUD, schedule recurrence 폼,
  schedule_templates.json 런타임 반영, 마크다운 기호 버튼, KEY_HELP, Cytoscape 폴백 등 완료.

### P2가 손대야 할 두 가지 (그리고 보류 항목)

| 항목 | 스펙 | 상태 | P2에서 |
|------|------|------|--------|
| **A. character-file-not-found 자동 생성** | §3.1 | preview만 생성, apply 거부(`repair.py:612`) | **구현** |
| **B. 세계관→시나리오 위저드 흐름** | §1.1 | 평면 mode-toggle(`setEditorMode`) | **구현** |
| POV(시점) 편집 | §2 | **완료** — `world-perspective-input` + `PUT /perspective`(`app.py:275`) | 손대지 않음 |
| scenario_id 편집 | §2 | **완료** — `PUT .../scenarios/{sid}/id`(`app.py:265`) | 손대지 않음 |
| world_id rename | §2 | **보류**(사용자 의도적 분리) | out-of-scope. `world_editor.html:1340` 입력은 `disabled` 유지 |

---

## A. character-file-not-found 자동 생성 (§3.1)

### A.1 현재 동작과 거부 지점

진단(`build_repair_report`)이 `char["source_file"] is None`인 Character를 발견하면
`missing_character_source` 이슈를 `repairable=False`로 보고한다.
(`src/ui/world_editor/repair.py:56-70` `_character_issues`.)

복구 적용은 `repair_issue`(`repair.py:250`)가 `_repair_missing_character_source`(`repair.py:591`)로 위임:

```python
# repair.py:591-612 (현재)
def _repair_missing_character_source(world_id: str, char: dict, apply: bool) -> dict:
    char_id = str(char.get("id") or "")
    if not char_id:
        return se._fail("캐릭터 id가 없어 자동 생성할 수 없습니다.")
    if se.find_character_file(world_id, char_id) is not None:
        return {"ok": True, "message": "이미 캐릭터 파일이 있습니다.", ...}
    if not apply:
        source = scaffold.character_source(char_id, name, aliases, type).replace("%%WID%%", world_id)
        return {"ok": True, "message": "...preview...", "diff": source, ...}
    return se._fail("compiled-only 캐릭터는 inline CREATE 제거 여부가 모호해 자동 적용하지 않습니다.")
```

즉 **preview는 기본 스캐폴드 소스(정체성만)를 보여주지만 apply는 무조건 거부**한다.
거부 이유 = "노드를 만든 inline `CREATE`를 schema.py에서 제거할지 말지가 모호"하기 때문.

### A.2 ⚠️ 핵심 정정 — "파일 없음"이 실제로 의미하는 것

기존 메모(`project-world-editor`)는 sunghwa의 `_build_rival_characters`가
"클래스 파일 없는 인라인 시드 캐릭터"라고 적었으나 **이는 부정확하다.**

`find_character_file`(`src/ui/world_editor/source_edit.py:746`)는 **파일명이 아니라
`id = "<char_id>"` 할당을 가진 클래스**를 `characters/**.py`, `characters.py`, `schema.py`
전체에서 ast로 탐색한다. 따라서:

- sunghwa 라이벌 27명(LeeSua 등)은 `characters/hansung_girls.py`·`miwon_girls.py`·`seogang_sports.py`·`eunmyeong.py`에
  **클래스로 존재**(한 파일에 여러 클래스). 각자 `id = "hansung_lee_sua"` 등을 가지므로
  `find_character_file`가 정상적으로 찾는다 → `source_file`이 채워짐 → **`missing_character_source`로 안 잡힌다.**
  (`sunghwa_high_school/schema.py:730` `_build_rival_characters`가 import 후 `char.build_schema(conn)` 호출.)

→ **`missing_character_source`는 오직 "어느 파일에도 클래스가 없고, schema.py의 raw `CREATE (:Character {...})`
cypher로만 노드가 만들어진" 진짜 클래스리스 캐릭터에서만 발생한다.**

### A.3 진짜 위험 — PK 중복

이 클래스리스 노드에 대해 새 클래스 파일을 만들고 `chars=[...]`에 등록하면,
컴파일 시 (1) schema.py의 raw `CREATE`와 (2) 새 클래스의 `build_schema`가 **둘 다** 실행되어
같은 `id`로 Character 노드를 두 번 만든다 → **PK 중복으로 컴파일 실패.**

그래서 자동 적용은 "새 파일 생성"과 "raw `CREATE` 제거"를 **반드시 한 트랜잭션으로 묶어야** 안전하다.
이것이 현재 코드가 거부하는 바로 그 모호함이다.

### A.4 설계

`_repair_missing_character_source`의 apply 경로를 다음 순서로 구현한다.
**보수적으로**: 안전하게 처리 가능한 경우에만 적용하고, 모호하면 정밀한 사유로 거부한다(현재처럼 막연히 막지 말 것).

1. **컴파일된 프로파일로 충실히 cfg 재구성.**
   진단 그래프의 `char` dict에는 이미 `static`·`personality`·`info`·`state` 하위 dict가 들어 있다
   (`annotate_graph`가 임시 Kuzu에서 추출, `source_edit.py:840` 이하; 다른 코드도 `char.get(role, {})`로 접근, `repair.py:89`).
   - 기본 스캐폴드(`scaffold.character_source`)는 `_default_cfg(biological_sex)`만 넣으므로
     **그대로 쓰면 컴파일된 값이 유실된다.** 대신:
     - `scaffold._default_cfg(...)`로 §8 골격을 만들고, 그 위에 `char`의 `static/personality/info/state`를
       덮어써 `DEFAULT_CFG`를 구성(`{"static":..., "personality":..., "info":..., "state":...}`).
     - DynamicState는 `base_character._DYNAMIC_STATE_COLUMNS` 화이트리스트로 필터하고
       계산식 id(`{cid}_static` 등)·비컬럼 키는 제외(`migrate.py`의 `_eval_state_dict` 로직 재사용/참조).
   - 신규 헬퍼 권장: `scaffold.character_source_from_cfg(char_id, name, aliases, char_type, default_cfg: dict) -> str`
     — `_CHARACTER_TMPL`의 `%%DEFAULT_CFG%%`에 `source_edit._emit(default_cfg, "    ")` 결과를 주입.
     (기존 `character_source`는 그대로 두고 새 진입점 추가; `_emit`은 ast 안전 리터럴 직렬화.)

2. **schema.py의 inline `CREATE` 탐지 + 안전성 판정.**
   schema.py를 ast 파싱해 해당 `char_id`로 Character 노드를 만드는 문장을 찾는다:
   - `conn.execute("... CREATE (:Character {... id: $id ...})", {"id": char_id, ...})` 형태,
   - 또는 `CREATE (:Character {id: "char_id"})` 리터럴 형태,
   - 또는 헬퍼 호출(`migrate.py`의 `_is_character_create_stmt` 참고 — name에 character+create/node 포함).
   **안전 조건(모두 충족 시에만 apply):**
   - 그 `char_id`에 대한 inline CREATE가 **정확히 1개** (0개면 "이미 클래스가 있어야 정상" → 별도 사유로 거부;
     2개 이상이거나 루프·분기 안이면 거부).
   - 제거 대상 문장이 **단순 구문**(단일 `conn.execute` statement, 주변 INVOLVED_IN/관계 엣지가 같은 id에 강결합돼
     있지 않음)일 것. 복잡하면 거부.
   - 적합하지 않으면 `se._fail("...구체 사유...")`로 거부(막연한 거부 금지).

3. **원자적 적용 (파일 생성 + 등록 + inline CREATE 제거 를 묶음).**
   - 새 클래스 파일 작성: 위 1의 소스 문자열 → `characters/{char_id}.py`.
   - 등록: `source_create.register_character(world_id, _camel(char_id), char_id, char_type)`
     (`source_create.py:838` — `characters/__init__.py` import + schema.py import/`chars=[...]`/narrator·pc 자동 갱신, 모두 ast 안전).
   - inline CREATE 제거: `source_edit`의 노드 스팬 도구(`_node_span`/`_replace_node_span`,
     `repair.py`의 `_repair_schedule`이 쓰는 패턴) + `_safe_write`(`.bak` + atomic) 로 그 statement만 삭제.
   - 모든 쓰기 전 각 파일을 `ast.parse`로 검증, 실패 시 전부 롤백.

4. **verify-by-recompile (PK 중복·그래프 드리프트 차단).**
   - `compiler._purge_world_modules(world_id)`로 모듈 캐시 무효화(쓰기 후 필수).
   - 임시 Kuzu DB로 모든 시나리오 재컴파일. `migrate.py`의 `_char_snapshot`/스냅샷 비교 로직을 재사용.
   - **불변식**: 적용 전후로 그 캐릭터의 노드/엣지/속성이 **동일**해야 한다(파일화는 무손상이어야 함).
     PK 중복(컴파일 예외)·노드 차이 발생 시 `.bak` 복원 후 `se._fail(사유)`.

5. **`repairable=True` 노출.**
   `_character_issues`(`repair.py:63-70`)에서 `missing_character_source` 이슈의 `repairable`를
   `_can_create_character_source(world_id, char)` 신규 판정 함수 결과로 채운다
   (위 2의 안전 조건을 dry-run으로 평가; `_can_repair_blob`/`_can_repair_schedule` 패턴을 따름).
   안전 조건 미충족이면 `False` + action 문구에 이유(예: "inline CREATE가 2개 이상이라 자동 정리 불가").

### A.5 프론트 (이미 배선됨, 확인만)

repair 리포트 UI는 `world_editor.html`에 이미 있다(진단 버튼 → 이슈 목록 → preview/apply).
`missing_character_source` 이슈가 `repairable:true`가 되면 기존 apply 버튼이 활성화된다.
preview diff에 "schema.py inline CREATE 제거 1건 포함" 같은 요약 메시지를 `result.message`로 노출.
**새 UI는 불필요** — 메시지/버튼 라벨만 다듬을 것.

### A.6 검증 (Task A)

1. **합성 케이스**: 임의 월드 schema.py에 클래스 없이 `CREATE (:Character {id:"ghost", ...})`만 추가 →
   진단에서 `missing_character_source` + `repairable:true` 확인 → preview diff(새 파일 + CREATE 제거) →
   apply → 재컴파일 시 PK 중복 없이 통과, `ghost` 노드 속성이 적용 전과 동일.
2. **음성 케이스**: sunghwa 라이벌(LeeSua 등)이 `missing_character_source`로 **잡히지 않음**을 확인(클래스 존재).
3. **거부 케이스**: inline CREATE가 루프/분기/2개 이상인 캐릭터 → `repairable:false` + 정밀 사유.
4. 회귀: `volleyball_team` 등 모든 시나리오 정상 컴파일.

### A.7 Task A 관련 파일

- `src/ui/world_editor/repair.py:56-70`(이슈 생성), `:250`(`repair_issue` 디스패치), `:591`(`_repair_missing_character_source` — **핵심 수정**), `:211/231`(`_can_repair_*` 판정 패턴), `:537-570`(`_repair_schedule` — 노드 스팬 교체/검증 패턴 참고).
- `src/ui/world_editor/source_edit.py:746`(`find_character_file`), `_node_span`/`_replace_node_span`/`_safe_write`/`_emit`/`_find_character_class`.
- `src/ui/world_editor/source_create.py:838`(`register_character`).
- `src/ui/world_editor/scaffold.py:34`(`_default_cfg`), `:397`(`character_source`), `:302`(`_CHARACTER_TMPL` / `%%DEFAULT_CFG%%`) — 신규 `character_source_from_cfg` 추가 지점.
- `src/ui/world_editor/migrate.py`(`_char_snapshot`·`_eval_state_dict`·`_is_character_create_stmt` 재사용).
- `src/assets/worlds/base_character.py`(`_DYNAMIC_STATE_COLUMNS` 화이트리스트, cfg-driven `build_schema`).
- `src/ui/world_editor/compiler.py`(`_purge_world_modules`, 임시 DB 재컴파일).

---

## B. 세계관→시나리오 위저드 흐름 (§1.1)

### B.1 현재 동작

헤더에 평면 mode-toggle 버튼이 있다(`world_editor.html:577-580`):

```html
<div class="mode-toggle" id="mode-toggle" title="편집 모드">
  <button class="mode-btn active" type="button" data-mode="scenario">시나리오</button>
  <button class="mode-btn" type="button" data-mode="world">세계관</button>
</div>
```

`setEditorMode(mode)`(`world_editor.html:1017`)가 `state.editorMode`("scenario"|"world", 기본 `:725`)를 토글하고
`visibleSections()`(`:840`, `s.mode==="both"||s.mode===state.editorMode`)로 좌측 nav 섹션 가시성을 바꾼다.
시나리오 모드에서만 roster(`:2573/2600`)·scenario 탭(`renderScenarioTabs`)이 보인다.

**스펙 §1.1이 원하는 것**: 단순 토글이 아니라
**"① 세계관 선택 → ② 세계관 모드(공통 설정 편집) → ③ 시나리오 선택 → ④ 시나리오 모드(시나리오별 편집)"**
의 **단계형 흐름**. 즉 세계관 레벨 설정을 먼저 확정한 뒤 시나리오로 내려가는 진입 게이트.

### B.2 설계 (점진적·기존 자산 재사용)

기존 `setEditorMode`·`visibleSections`·`renderNav`·`renderSection`을 **버리지 말고** 그 위에 진입 단계만 얹는다.

1. **`state.wizardStep` 도입**: `"world"`(②) | `"scenario"`(④). `state.editorMode`와 1:1 매핑되므로
   실질적으로 `editorMode`를 단계 의미로 재해석한다(별도 state 최소화).
2. **헤더 재구성** (`:574-583` 영역):
   - `world-select`(① 세계관 선택)는 유지.
   - mode-toggle을 **단계 표시 + 전이 컨트롤**로 바꾼다:
     - 세계관 모드일 때: `[세계관: {name}]` 활성 + `시나리오 선택 →` 버튼(다음 단계 진입).
     - 시나리오 모드일 때: `← 세계관` (뒤로) + scenario 탭(`scn-tabs`) 노출.
   - 즉 두 모드를 **나란히 켜는 토글**이 아니라 **한 번에 한 단계만** 보이는 stepper로.
3. **진입 게이트**:
   - `onWorldChange` 직후 기본 단계 = **세계관 모드**(②). scenario 탭/roster는 숨김.
   - "시나리오 선택 →" 클릭 시 시나리오 선택(드롭다운/탭) → `setEditorMode("scenario")` 호출하며
     선택된 `scenario_id`로 그래프 로드.
   - "← 세계관" 클릭 시 `setEditorMode("world")`로 복귀.
4. **섹션 가시성은 기존 `visibleSections()` 그대로** — 세계관 모드에선 world 공통 섹션, 시나리오 모드에선
   시나리오 의존 섹션. (이미 `mode` 필드로 분류돼 있으니 데이터는 손대지 않음.)
5. **상태 보존**: 단계 전환 시 dirty(미저장) 편집이 있으면 경고(`state.promptDirty`/`rosterDirty` 등 기존 플래그 활용).

### B.3 구현 메모

- 새 컴포넌트 최소화: stepper는 `mode-toggle` 자리(`:577`)를 재사용해 버튼 라벨/핸들러만 교체.
- `setEditorMode`(`:1017`)에 "scenario로 전환할 땐 유효한 `state.scenarioId`가 있어야 한다"는 가드 추가
  (없으면 시나리오 선택 UI를 먼저 띄움).
- CSS는 기존 `.mode-toggle`/`.mode-btn`(`:86`) 재사용 가능.
- **백엔드 변경 불필요** — 순수 프론트(`world_editor.html`) 작업. 그래프 로드/scenario 전환 API는 그대로.

### B.4 검증 (Task B)

1. 세계관 선택 → 자동으로 세계관 모드 진입, scenario 탭/roster 숨김, world 공통 섹션만 노출.
2. "시나리오 선택 →" → 시나리오 고르면 시나리오 모드 진입, roster·scenario 의존 섹션 노출, 올바른 `scenario_id`로 로드.
3. "← 세계관" 왕복 시 상태/섹션 일관, dirty 편집 경고 동작.
4. 기존 단축 동작(직접 섹션 클릭 등) 회귀 없음.

### B.5 Task B 관련 파일

- `world_editor.html` 전부 프론트: `:577`(mode-toggle markup), `:725`(state.editorMode 기본), `:767`(`modeToggle` 핸들), `:840`(`visibleSections`), `:1017`(`setEditorMode`), `:1028`(`loadWorlds`)/`onWorldChange`, `:1171`(`state.editorMode==="world"` 분기), `:2573/2600`(roster 가시성), `renderScenarioTabs`/`renderNav`/`renderSection`.

---

## C. 공통 함정 (Codex 필독)

1. **모듈 캐시**: 소스 .py 편집 후 재컴파일하려면 `compiler._purge_world_modules(world_id)`로
   해당 월드 `sys.modules` 서브트리를 비워야 디스크 새 내용이 반영된다(Python import 캐시 함정).
2. **base 모듈은 purge 안 됨**: `_purge_world_modules`는 `src.assets.worlds.{world_id}.*`만 비운다.
   `base_character.py`·`source_edit.py` 등 **공유 모듈을 수정하면 world_editor 서버를 재시작**해야 반영된다
   (과거 "sunghwa 컴파일 실패"는 stale 서버가 옛 `build_schema`(NotImplementedError)를 캐시한 것이 원인이었다).
3. **라이브 DB 비접촉**: 읽기 뷰/검증은 항상 **임시 Kuzu DB로 build_schema 후 추출**한다.
   `graph/{WORLD_ID}` 라이브 DB는 건드리지 않는다(챗봇과 동시 실행 가능). `src.agents.manager` 경유 import 금지
   (그 패키지는 import 시 기본 Kuzu DB 락을 잡는다) — 월드 `schema` 모듈을 직접 import.
4. **.gitignore**: `src/assets/worlds/*/`는 git-ignore, `src/ui/world_editor/`는 untracked.
   디스크 변경이 `git status`에 안 보일 수 있으니 파일을 직접 열어 확인.
5. **AST 쓰기 안전 원칙**: 데이터 .py write-back은 정적평가 가능한 clean 리터럴만 편집.
   저장은 `ast.parse` 검증 → `.bak` 백업 → atomic write(`source_edit._safe_write`). 소스 손상 불가가 불변식.
6. **sunghwa SCENARIOS의 `altered`** (← `altered_mind` 아님): 임시값 미복원 버그였고 `altered`가 의도값. 다시 바꾸지 말 것.

---

## D. 우선순위 / 권장 착수 순서

1. **B(위저드)** 먼저 — 순수 프론트, 백엔드 무변경, 위험 낮음. 빠른 가시적 성과.
2. **A(자동 생성)** — AST·재컴파일·롤백이 얽혀 위험 높음. 합성 케이스로 TDD하듯 검증부터 세우고 진행.
   특히 A.3(PK 중복)·A.4-2(inline CREATE 안전 판정)를 **보수적으로**: 모호하면 적용하지 말고 정밀한 사유로 거부.
3. world_id rename은 **건드리지 말 것**(사용자 보류).

작업 후 메모 갱신: `C:\Users\bling\.claude\projects\F--python-NLP-GraphRAG\memory\project_world_editor.md`
(P2 항목 진행/완료 반영). 관련 메모: `project-arch-findings`, `project-static-event`.
