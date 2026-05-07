# GraphRAG 기반 롤플레이 시뮬레이션 엔진

## 파일 구조

```
project-root/
│
├── app.py                              # Chainlit 메인 앱.
│                                       # 세션 초기화 · 메시지 루프 · OOC 분기 · Manager 파이프라인 · Actor 스트리밍.
│                                       # 이전 턴 DB 커밋을 다음 입력 시점에 지연 확정(Deferred Commit).
│
├── chainlit.md                         # Chainlit 시작 화면 마크다운
├── example.env                         # 환경변수 템플릿 (.env로 복사 후 사용)
│
├── public/                             # Chainlit 프론트엔드 에셋
│   ├── elements/
│   │   ├── EditableMessage.jsx         # 메시지 인라인 편집 UI 컴포넌트
│   │   └── TimeTheme.jsx               # 인게임 시간·날씨 테마 표시 컴포넌트
│   └── stylesheet.css
│
└── src/
    │
    ├── config.py                       # 환경변수 중앙화. 모든 모듈은 os.getenv 대신 여기서 import.
    │
    ├── agents/                         # ── LLM 에이전트 계층 ──────────────────────────────
    │   ├── actor.py                    # 3-파트 프롬프트를 Gemini Actor 모델에 전달하고 응답 반환
    │   ├── manager.py                  # 파이프라인 오케스트레이터.
    │   │                               # (1) 씬 타입 분류 (daily/emotional/physical/intimate 등)
    │   │                               # (2) 각 시뮬레이션 시스템 순차 실행
    │   │                               # (3) PromptBuilder 호출로 fixed/genre/dynamic 프롬프트 조립
    │   ├── resolver.py                 # 욕구(Needs) 임계 초과 시 NPC 자율행동 결정 및 이벤트 생성
    │   └── prompt_factory/
    │       ├── builder.py              # 3-파트 프롬프트 조립기.
    │       │                           # [fixed]   operator_policy + 규칙 + 세계관 + 캐릭터 (캐시 대상)
    │       │                           # [genre]   씬 타입별 묘사 규칙 + 퓨샷 예시
    │       │                           # [dynamic] 현재 헤더(시간·날씨·장소) + DB 컨텍스트 + 유저 입력
    │       └── ooc_handler.py          # OOC(*...* 마커) 텍스트 파싱 후 세계 상태 즉시 반영
    │
    ├── assets/                         # ── 세계 정의 에셋 ──────────────────────────────────
    │   └── worlds/
    │       ├── base.py                 # World 베이스 클래스. narrator/pc/chars/perspective 주입.
    │       │                           # _build_tables(DDL) · _build_world_events(no-op) ·
    │       │                           # build_schema(빌드 진입점) · get_full_config 정의
    │       ├── base_character.py       # Character 베이스 클래스 + 공용 헬퍼.
    │       │                           # _insert_rel(RELATIONSHIP 엣지) ·
    │       │                           # _merge_static_event(MERGE 기반 StaticEvent 생성)
    │       └── default/                # 기본 세계 구현체 (세계별 파일은 .gitignore 제외)
    │           ├── schema.py           # World 서브클래스. DDL ALTER + 장소 + 캐릭터 빌드 오케스트레이션.
    │           └── prompt/
    │               ├── world.md        # 세계관·배경 설명 (Fixed 프롬프트에 삽입)
    │               ├── prose_1p.md     # 1인칭 시점 묘사 규칙
    │               ├── prose_3p.md     # 3인칭 시점 묘사 규칙
    │               ├── blacklist.md    # 금지 표현·설정 목록
    │               └── few_shot/       # 씬 타입 × 시점별 퓨샷 예시
    │                   ├── daily_1p.md / daily_3p.md
    │                   ├── emotional_1p.md / emotional_3p.md
    │                   ├── intimate_1p.md / intimate_3p.md
    │                   └── physical_1p.md / physical_3p.md
    │
    ├── core/                           # ── 인프라 계층 ──────────────────────────────────────
    │   ├── database/
    │   │   ├── driver.py               # Kuzu 비동기 드라이버 싱글톤
    │   │   ├── helpers.py              # CRUD 헬퍼 (update_dynamic_state, move_location 등)
    │   │   └── schema_builder.py       # 스키마 초기화 CLI
    │   │                               # Usage: python -m src.core.database.schema_builder --world_id <id>
    │   ├── embedding/
    │   │   └── encoder.py              # HuggingFace 임베딩 래퍼 (1024-dim)
    │   ├── llm/
    │   │   └── client.py               # LLM 클라이언트 래퍼 (Gemini / OpenRouter).
    │   │                               # get_model() · get_response_text() · extract_json_from_llm() 제공
    │   └── logging/
    │       └── conversation_logger.py  # 대화 턴을 날짜별 Markdown 파일로 저장 (logs/YYYY-MM-DD.md)
    │
    └── simulation/                     # ── 시뮬레이션 시스템 계층 ───────────────────────────
        ├── events/
        │   ├── evaluator.py            # StaticEvent 조건 평가 (time / stat / flag 타입, AND 연산)
        │   └── manager.py              # StaticEvent 생명주기 관리.
        │                               # 매 턴 조건 평가 → dormant → foreshadowing → active 상태 전환
        ├── state/
        │   ├── classifier.py           # Actor 응답 텍스트 분류.
        │   │                           # LITERAL(물리적 사건) vs FIGURATIVE(감정·비유) 판별 후
        │   │                           # DynamicState 변경 필드 목록 추출
        │   └── updater.py              # 상태 업데이트 파이프라인 통합.
        │                               # Classifier + Complex Updater + 관계 깊이 파이프라인을
        │                               # 단일 LLM 호출로 처리. reputation / memory / personality 호출.
        └── systems/
            ├── memory.py               # 기억 노드 생성 + 시간 기반 왜곡·압축·삭제 (decay)
            ├── needs.py                # NPC 욕구 6종 추적 (hunger / rest / social / fun / safety / libido).
            │                           # 임계값(0.8) 초과 시 resolver에 자율행동 위임
            ├── organic.py              # 생리주기 상태 tick 및 관련 이벤트 처리
            ├── personality.py          # NPC 성격 drift.
            │                           # micro: 친밀도 ≥ 65 + 30일 쿨다운 / macro: 중대 이벤트(importance ≥ 9)
            ├── reputation.py           # NPC 간 소문 전파 (importance ≥ 5 이벤트 발생 시).
            │                           # 지인 NPC 호감도·기억 갱신
            └── social.py               # 세계 맥락 생성 (근처 활동 + SNS 피드) 및 캐릭터 그래프 관리
```

---

## 요청 파이프라인 (1턴)

```
유저 입력 (Chainlit)
  → OOC 파서                                      *...* 마커 감지 → 상태 즉시 반영
  → Manager Agent                                씬 분류 + 각 시스템 실행 + 프롬프트 조립
      ├─ StaticEvent 평가                         이벤트 복선 hint 수집
      ├─ Memory Decay                            오래된 기억 왜곡/압축
      ├─ Organic Tick                            여성 캐릭터 생리주기 진행
      ├─ Needs Update                            욕구 수치 갱신, 임계 초과 시 자율행동
      └─ World Context                           근처 활동·SNS 피드 생성
  → PromptBuilder                                Fixed / Genre / Dynamic 3-파트 조립
  → Actor Agent                                  Gemini 롤플레이 응답 생성 (스트리밍)
  → State Updater                                Classifier → 복합 업데이트 → 관계 깊이 파이프라인
      ├─ reputation.propagate_gossip             소문 전파
      ├─ memory.distort_on_affinity_change       호감도 급변 시 기억 재해석
      └─ personality.check_personality_drift     성격 drift 체크
  → [DB 커밋은 다음 턴 시작 시 지연 확정]
```

## 3-파트 프롬프트

| 파트          | 내용                               | 특성                                    |
|-------------|----------------------------------|---------------------------------------|
| **Fixed**   | operator_policy + 규칙 + 세계관 + 캐릭터 | Gemini implicit cache 대상 — 매 턴 동일해야 함 |
| **Genre**   | 씬 타입별 묘사 규칙 + 퓨샷 예시              | 씬 분류 결과에 따라 교체                        |
| **Dynamic** | 현재 시각·장소·날씨 + DB 컨텍스트 + 유저 입력    | 매 턴 재조립                               |

씬 타입: `daily` · `emotional` · `physical` · `intimate` · `workplace` · `aegyo`

## 캐릭터 정의 방법

캐릭터 1명 = 파일 1개 (`src/assets/worlds/<world_id>/characters/<char_id>.py`).

```python
from src.assets.worlds.base import insert_static_inline
from src.assets.worlds.base_character import Character, _insert_rel, _merge_static_event

class Alice(Character):
    id = "alice"
    name = "앨리스"
    aliases = ["앨리스", "부인"]
    char_type = "npc"

    def build_schema(self, conn: kuzu.Connection) -> None:
        """Character 노드, StaticProfile, DynamicState를 생성합니다."""
        conn.execute(
            "CREATE (:Character {id: $id, name: $name, aliases: $aliases, type: $type})",
            {"id": self.id, "name": self.name, "aliases": self.aliases, "type": self.char_type},
        )
        insert_static_inline(
            conn, self.id, "HAS_PROFILE", "StaticProfile", f"{self.id}_static",
            age=22,
            appearance="...",
        )
        conn.execute(
            "CREATE (:DynamicState {id: $id, mood: $mood, current_location: $current_location})",
            {"id": "alice_state", "mood": "guarded", "current_location": "default_location"},
        )
        conn.execute(
            "MATCH (c:Character {id: $id}), (d:DynamicState {id: $did}) CREATE (c)-[:HAS_STATE]->(d)",
            {"id": self.id, "did": "alice_state"},
        )

    def build_relationship(self, conn: kuzu.Connection, other: Character) -> None:
        """관계 엣지와 관련 StaticEvent를 함께 생성합니다."""
        _RELS = {
            "bob":     ("ally",  70, 80, "Trusted companion."),
            "villain": ("enemy",  5,  0, "Deep-seated hostility."),
        }
        if other.id not in _RELS:
            return
        rel_type, affinity, trust, status = _RELS[other.id]
        _insert_rel(conn, self.id, other.id, rel_type, affinity, trust, status)

        # 관계 생성과 동시에 관련 이벤트를 인라인으로 선언
        if other.id == "villain":
            _merge_static_event(
                conn,
                event_id="villain_discovers_secret",
                name="빌런, 비밀을 알게 되다",
                foreshadow_conditions='{"type":"flag","key":"villain_suspicious","value":true}',
                foreshadow_hint="빌런의 시선이 자꾸 앨리스에게 머문다.",
                trigger_conditions='{"type":"flag","key":"secret_exposed","value":true}',
                involved_ids=["alice", "villain"],
            )
```

**규칙**
- `build_schema` — 노드·프로파일·상태만. 관계·이벤트는 넣지 않는다.
- `build_relationship` — `_RELS` dict로 상대방 id를 키로 조회. 모르는 상대는 자동 no-op.
- 같은 event_id를 두 캐릭터가 모두 선언해도 MERGE로 중복 방지된다.

---

## 세계 초기화 방법

`schema.py` 의 World 서브클래스가 `build_schema(conn)` 한 번으로 전체 스키마를 빌드한다.

```python
# schema.py
class MyWorld(World):
    WORLD_ID = "my_world"

    def build_schema(self, conn: kuzu.Connection) -> None:
        self._build_tables(conn)                   # 공통 DDL (노드/관계 테이블, 벡터 인덱스)

        # 세계 전용 컬럼 추가
        for col, col_type in [("custom_field", "STRING")]:
            try: conn.execute(f"ALTER TABLE DynamicState ADD {col} {col_type}")
            except: pass

        # 장소
        conn.execute("CREATE (:Location {id: 'town', name: '마을', ...})")

        # 캐릭터 노드 + 프로파일 + 상태
        for char in self.chars:
            char.build_schema(conn)

        # 관계 + 인라인 이벤트
        for a in self.chars:
            for b in self.chars:
                if a.id != b.id:
                    a.build_relationship(conn, b)

        # 세계 레벨 이벤트 (캐릭터 무관)
        self._build_world_events(conn)

    def _build_world_events(self, conn: kuzu.Connection) -> None:
        """계절 전환, 전쟁 선포 등 세계 레벨 StaticEvent."""
        _merge_static_event(conn, event_id="season_change", ...)
```

**서술자·PC 변경** — `world_instance` 한 줄만 수정한다. 세계 파일 복사 불필요.

```python
# schema.py 맨 아래
world_instance = MyWorld(
    narrator=Alice(),   # ← 이 줄만 바꾸면 서술자 교체
    pc=Bob(),
    chars=[Alice(), Bob(), Villain(), ...],
)
```

---

## 새 세계 추가

1. `src/assets/worlds/<world_id>/` 디렉터리 생성
2. `characters/` 하위에 캐릭터 파일 1명 = 1파일로 작성, `__init__.py` 에 일괄 export
3. `schema.py` 에 World 서브클래스 정의 (위 템플릿 참고)
4. `prompt/` 하위에 `world.md`, `prose_1p.md`, `prose_3p.md`, `blacklist.md`, `few_shot/*.md` 작성
5. `src/assets/worlds/__init__.py` 에 world_id → 클래스 매핑 추가
6. `python -m src.core.database.schema_builder --world_id <world_id>` 실행

## 환경변수 (`.env`)

| 변수                      | 용도                   |
|-------------------------|----------------------|
| `MODEL_ACTOR`           | 롤플레이 생성 LLM (Gemini) |
| `MODEL_CLASSIFIER`      | 씬 분류 LLM             |
| `MODEL_STATE_UPDATER`   | 경량 상태 업데이트 LLM       |
| `MODEL_COMPLEX_UPDATER` | 다중 노드 복합 업데이트 LLM    |
| `GOOGLE_PROJECT_ID`     | Google Cloud 프로젝트 ID |
| `MODEL_EMBEDDER`        | HuggingFace 임베딩 모델   |
| `EMBEDDING_DIM`         | 임베딩 차원 수             |
| `HF_TOKEN`              | HuggingFace API 토큰   |
| `WORLD_ID`              | 활성 세계 ID             |
| `PERSPECTIVE`           | `1` = 1인칭, `3` = 3인칭 |
| `MAX_TOKEN`             | Actor 출력 토큰 상한       |
| `IMPERSONATION`         | PC-as-NPC 모드 플래그     |
