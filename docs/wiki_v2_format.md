# Wiki V2 Markdown 규격

Wiki V2는 Kuzu나 파생 인덱스가 아니라 UTF-8 Markdown 문서를 상태의 원본으로
사용한다. 이 문서는 현재 구현된 vault 구조, frontmatter, 제목 주소, 템플릿의
최소 계약을 정의한다.

## Vault 구조

```text
wiki_v2/
├─ worlds/<world_id>/
│  ├─ world.md
│  ├─ prose.md
│  ├─ scenes/<scene_type>.md
│  ├─ scenarios/<scenario_id>/
│  │  ├─ scenario.md
│  │  ├─ start_state.md
│  │  ├─ opening_scene.md
│  │  └─ scenes/<scene_type>.md
│  ├─ characters/
│  ├─ locations/
│  └─ organizations/
└─ threads/<thread_id>/
   ├─ .wikirag-runtime.json
   ├─ thread.md
   ├─ scene/current.md
   ├─ characters/
   ├─ relationships/
   ├─ events/
   ├─ memories/
   ├─ goals/
   ├─ items/
   ├─ secrets/
   └─ commits/
```

`worlds/`는 다시 사용할 수 있는 세계 정의와 캐릭터 원본 프로필을 보관한다.
`threads/`는 특정 플레이의 독립된 현재 상태를 보관한다. 플레이에 참여하는
캐릭터는 world의 `character_profile`을 바탕으로 thread의 완전한 `character`
문서로 물질화한다. 이후 런타임 변경의 canonical home은 thread 문서이며,
world 프로필을 암묵적으로 되돌려 쓰지 않는다.

새 thread 물질화가 성공하면 내부 진단 파일 `.wikirag-runtime.json`이 생성된다.
이 파일은 현재 Wiki 런타임이 생성한 thread와 이전 구현 thread를 구분하기 위한
표식이며 Markdown 정본, Actor prompt, Updater 입력에 포함되지 않는다. 표식이
없는 기존 thread는 자동 변환하지 않고 `legacy` 진단으로 노출한다.

각 시나리오는 `scenarios/<scenario_id>/` 아래의 세 문서로 분리한다.

| 파일 | 역할 | 포함하지 않는 내용 |
| --- | --- | --- |
| `scenario.md` | 해당 시나리오에만 적용되는 특징과 묘사 규정 | 표시용 시나리오 이름, 다른 시나리오 언급, 시작 설정과 첫 장면 |
| `start_state.md` | 시작 시각·장소, 관계의 초기값, 인물 상태와 첫 계기 | 완성된 첫 장면 산문 |
| `opening_scene.md` | 플레이어의 첫 입력 직전에 보여주는 첫 장면 원문 | 장기 운용 규칙과 별도 상태 목록 |
| `scenes/<scene_type>.md` | 해당 장면 종류가 활성일 때만 적용되는 월드 또는 시나리오 전용 묘사 규정 | 지속 설정, 시작 상태, 다른 장면 종류와의 비교 |

`scenario.md`의 frontmatter ID와 디렉터리 이름은 런타임 식별자이므로
`scenario_id`를 포함하지만, Actor용 본문은 시나리오 이름을 되풀이하지 않는다.
새 대화를 만들면 `start_state.md` 본문은 해당 thread의 `scene/current.md`로,
선택 월드의 캐릭터 프로필은 thread-scoped `character` 문서로 물질화된다.
`opening_scene.md`는 상태 문서에 합치지 않고 최초 assistant 메시지로만 사용한다.

장면 프롬프트는 월드의 `scenes/`와 선택 시나리오의 `scenes/`에서 읽는다. 같은
`scene_type`이 양쪽에 있으면 시나리오 문서가 월드 문서를 교체하고, 둘 다 없으면
PromptBuilder의 공용 장면 프롬프트를 사용한다. frontmatter의 `scene_type`은 파일명과
일치해야 하며 `description`은 분류기가 이 key를 선택할 조건을 영어 한 문장으로
설명한다. 이 metadata와 경로, 분류 key는 Actor prompt에 포함하지 않는다.

### 정적 문서의 정본 책임

같은 사실을 여러 정적 문서에 요약본과 상세본으로 반복하지 않는다.

| 문서 | 정본으로 소유하는 정보 |
| --- | --- |
| `world.md` | 도시 규모, 교통, 경제, 기술, 계절, 제도와 현실 법칙 |
| `locations/*.md` | 공간 구조, 위치 관계, 감각적 특징과 이용 제약 |
| `organizations/*.md` | 기관 목적, 운영 리듬, 역할, 문화와 구성원 |
| `prose.md` | 해당 작품에서만 달라지는 장르 감각, 대화 관습과 생활 장면 작법 |
| `scenario.md` | 선택된 관계와 사건에서만 유효한 사실, 톤과 묘사 규정 |
| `scenes/*.md` | 활성 장면 종류에서만 필요한 진행 순서, 감각 우선순위, 물리·대화·리듬 제약 |

출력 언어, 제한 시점, 감정 증거, 물리적 연속성, NPC 주체성, 관계 변화 속도,
열린 장면 종결과 장르 공통 친밀 규정은 기존 PromptBuilder가 소유한다.
`prose.md`와 `scenario.md`는 이 공통 계약을 다시 서술하지 않는다.

### 동적 사실의 정본 책임

한 턴에서 여러 문서가 함께 바뀌더라도 같은 사실의 전문은 한 곳에만 저장한다.

| 문서 | 정본으로 소유하는 동적 사실 |
| --- | --- |
| `thread.md` | 대화 식별자, 표시 제목과 lifecycle metadata |
| `scene/current.md` | 현재 시각·장소, 현장 인물 배치, 공유 이동·활동, 즉시 긴장 |
| `characters/*.md` | 해당 인물만의 신체·감정 상태, needs, 동적 성격 원장, opt-in 생식 상태 |
| `relationships/*.md` | owner가 participant를 보는 방향의 durable 관계 변화 원장 |
| `events/*.md` | 실제로 발생한 객관적이고 durable한 사건과 직접 결과 |
| `memories/*.md` | owner가 기억하는 주관적 내용·해석·감정·확신·왜곡 가능성 |
| `goals/*.md` | owner의 지속 목표와 현재 단계·다음 행동·완료 상태 |
| `items/*.md` | 지속 물품의 정체성, 상태, 보관 위치와 접근 가능성 |
| `secrets/*.md` | 비공개 진실, 아는 사람, 공개 단서와 노출 상태 |

예를 들어 공유 이동은 scene에만 쓰고 character에는 반복하지 않는다. Event의
객관적 사실을 Memory에 복사하지 않고 Memory에는 그 owner가 무엇을 어떻게
기억하는지만 쓴다. Secret의 실제 내용은 관계나 Event의 공개 section에 복제하지
않으며, 공개된 결과가 별도 사건이 되면 Event에는 공개 행위와 그 결과만 기록한다.
문서 간 연결은 안정적인 ID 필드로 표현하고 Actor-visible 본문에 파일 경로나
`[[wikilink]]`를 넣지 않는다.

### 독립 조립식 프롬프트 원칙

Actor에게 전달될 수 있는 각 Markdown 본문은 서로 독립된 조립 모듈이다. 본문은
함께 조립되는 다른 모듈의 목록, 경로, 선택 과정이나 소비 주체를 알지 못한다.
각 문장은 자기 문서가 소유한 사실이나 묘사 규정을 직접 표현해야 한다.

- 다른 프롬프트, 문서, 파일, 섹션 또는 시나리오를 언급하거나 참조하지 않는다.
- Actor, Updater, assistant, model, runtime과 prompt 조립·갱신 절차를 언급하지 않는다.
- `위 규칙`, `아래 내용`, `이 파일`, `다른 시나리오와 달리` 같은 상대 참조를 쓰지 않는다.
- `사용자의 선택에 맡긴다`, `플레이어가 정한다`로 설정이나 시작 상태를 비워 두지 않는다.
- 시작 시점에 존재하는 모든 인물의 위치와 관찰 가능한 상태는 `start_state.md`에 구체적으로 기록한다.
- 플레이어가 첫 응답 뒤 무엇을 할지는 미리 서술하지 않되, 이를 설명하는 권한 문구도 본문에 넣지 않는다.

독립성은 중복을 허용한다는 뜻이 아니다. 한 사실은 한 문서만 정본으로 소유하고,
다른 문서는 그 위치를 참조하거나 요약 복제하지 않고 단순히 생략한다.
`visibility` 같은 runtime 역할명은 prompt 조립 전에 제거되는 frontmatter에만
존재할 수 있다.

### 프롬프트 작성 언어

`world.md`, `prose.md`, 캐릭터·장소·조직 문서, `scenario.md`, 장면 프롬프트,
`start_state.md`의 제목과 설명형 본문은 영어로 작성한다. 이는 최종 출력 언어가
아니라 LLM에 전달되는 설정·규정 프롬프트의 작성 언어에 대한 계약이다.

한국어는 다음 범위에만 남긴다.

- 한국 고유명사, 인명, 호칭, 존칭과 세계관 고유 용어
- 작품 속 대사 예시
- 한국어 표현 자체를 보여주는 짧은 묘사·산문 예시
- 사용자가 제공한 원문의 직접 인용
- parser가 정확한 문자열로 요구하는 구조 제목

한국어 원본 설정을 승격할 때에는 의미를 간결한 영어 정본으로 옮긴다. 원본이
한국어라는 이유로 설명형 산문을 그대로 남기지 않는다. ID, frontmatter 값과
작성용 selector는 번역하지 않는다.

`opening_scene.md`는 Actor를 위한 규정 모듈이 아니라 플레이어에게 그대로
표시되는 완성 산문이므로, 별도 요청이 없으면 한국어로 작성한다.

### Actor용 컴파일 경계

Actor는 vault 파일이나 저장소 구조를 직접 받지 않는다. 런타임은 frontmatter,
문서 경로, revision, world/scenario/thread ID와 작성용 분기 제목을 제거하고,
선택된 사실과 규칙의 Markdown 본문만 경로 없는 의미 기반 XML 태그로 감싼다.
`thread.md`는 내부 대화 관리 문서이므로 Actor prompt에 포함하지 않는다. Updater는
충돌 검증을 위해 문서 경로와 revision을 계속 사용한다.

Actor prompt 조립은 `[[wikilink]]`를 따라가며 다른 문서를 자동 로드하지 않는다.
Actor-visible 본문에 wikilink, Markdown 파일명 또는 frontmatter 필드가 남아
있으면 컴파일을 거부한다. 필요한 사실은 현재 문서 본문에 직접 작성하고 저작용
링크는 Actor-visible 본문 밖에 두거나 컴파일 전에 제거한다.

장면 분류는 공용 scene 설명과 활성 월드·시나리오의 `scene_prompt.description`을
함께 사용한다. 선택된 key의 본문만 Dynamic의 `scene_specific_prompts`에 들어가며,
선택되지 않은 장면 프롬프트와 시나리오 override metadata는 노출되지 않는다.

따라서 Actor runtime의 링크 탐색 깊이는 0이다. 누적되는 Event, Memory, Goal,
Item, Secret은 구조 관련성과 최근성으로 결정적으로 정렬한 뒤 문서 수와 보수적
추정 token 예산을 모두 적용한다. 기본값은 Actor 24개/12000 token, Updater
48개/32000 token이며 각각 `WIKI_ACTOR_RECALL_BUDGET`,
`WIKI_ACTOR_RECALL_TOKEN_BUDGET`, `WIKI_UPDATER_RECALL_BUDGET`,
`WIKI_UPDATER_RECALL_TOKEN_BUDGET`으로 조정한다. Scene, 활성 Character,
Relationship 같은 필수 상태 문서는 이 누적 문서 예산에서 제외한다.

컴파일된 prompt는 Fixed prose wrapper와 prose 규칙을 정확히 한 번 포함하고,
Dynamic에 현재 장면과 사용자 입력을 정확히 한 번 포함해야 한다. 현재 상태
블록은 Fixed 또는 Genre에 들어갈 수 없다.

`world.md`, 장소·조직, 선택된 상황 정보와 캐릭터 정적 섹션은
`world_lore`에 들어간다. `prose.md`는 PromptBuilder의
`world_specific_prose_prompt` 슬롯에 정확히 한 번만 들어가며 `world_lore`에
중복하지 않는다.

인물 원본은 H2 아래에 다음과 같은 작성용 분기를 둘 수 있다.

```markdown
## 진은서 외형

### common
#### 얼굴과 머리
...

### default
#### 체격과 움직임
...

### amputee_fwb
#### 현재 신체
...
```

`common`은 항상 포함한다. 현재 분기와 같은 선택기가 있으면 이를 사용하고,
없으면 `default`를 사용한다. 물질화할 때 선택기 H3는 삭제하고 그 아래 H4 이상의
실제 제목을 한 단계 올린다. 같은 H2 안에서 선택기 H3와 일반 H3를 섞지 않는다.
이 구조는 작성 편의를 위한 것이며 선택기 이름 자체는 Actor에게 전달되지 않는다.

Actor 가시 본문에는 `아직 정해지지 않았다`, `플레이 중 확정한다`, `세부 값은
미정이다` 같은 저자용 placeholder를 저장하지 않는다. 필요한 설정은 구체적인
사실로 채운다. 인물이 어떤 사실을 모르는 경우에는 객관적 사실과 인물의 인식
범위를 함께 적어 지식 차이로 표현한다. 작성 권한이 없는 정보는 placeholder를
남기지 말고 사용자 확인 후 반영한다.

## Frontmatter

템플릿으로 생성하는 모든 상태 문서는 다음 공통 필드를 가진다.

| 필드 | 의미 |
| --- | --- |
| `id` | `world:`, `thread:`, `character:` 같은 종류 prefix를 가진 안정적인 ID |
| `type` | `world`, `scene`, `character`, `memory` 같은 문서 종류 |
| `schema_version` | 템플릿 구조 버전. 현재 `1` |
| `visibility` | 문서를 읽을 수 있는 소비자 목록 |
| `created_at` | 생성 UTC 시각 |

Wiki 채팅이 가능한 `world.md`는 다음 런타임 확장 필드를 추가한다.

| 필드 | 의미 |
| --- | --- |
| `pc_profile_id` | 플레이어가 맡는 `character_profile` 문서 ID |
| `npc_profile_id` | 기본 Actor가 맡는 `character_profile` 문서 ID |
| `pov_mode` | `1p_user`, `1p_char`, `3p_user`, `3p_char` 중 하나 |
| `rating` | `all_ages`, `15`, `r18` 중 하나 |

`scenario.md`는 특정 시나리오에서만 시점이 달라질 때 같은 `pov_mode` 필드를
선택적으로 가질 수 있다. 새 thread를 만들 때 값이 있으면 월드 기본 시점을
덮어쓰고, 없으면 `world.md`의 시점을 그대로 사용한다.

문서 종류에 따라 `world_id`, `thread_id`, `profile_id`, `owner`,
`participants`를 추가한다. Frontmatter는 문서의 안정적인 정체성과 접근 경계만
담는다. Updater가 바꿀 수 있어야 하는 상태, 시각, 위치, 진행 단계는 H2/H3
본문 섹션에 두며 frontmatter와 중복 저장하지 않는다. YAML frontmatter가 없는 일반 Markdown도
읽을 수 있지만, frontmatter가 시작되었다면 올바른 YAML mapping이어야 한다.
Frontmatter가 존재하면 공통 필드는 모두 필수이며 `schema_version`은 양수여야
한다. `visibility` 값은 `actor`, `updater`, `player`만 허용하고 중복할 수 없다.
문서 `type`은 현재 제공되는 15종 템플릿 중 하나여야 하며, YAML key도 중복할
수 없다. Type별 ID namespace와 `world_id`/`thread_id`/`owner`/`participants`
계약도 로더에서 검증한다.

`revision`은 frontmatter에 저장하지 않는다. 저장된 숫자와 실제 내용이 서로
어긋나는 이중 원본을 만들지 않기 위해 UTF-8 문서 전체의 SHA-256 hash를 읽을
때마다 계산한다. 따라서 Obsidian에서 직접 저장한 변경도 별도 revision 갱신
과정 없이 즉시 새 revision으로 인식된다.

적용 대기 `commit.md`의 각 `SectionPatch`는 계획 시점 target section 원문을
`base_markdown`으로 함께 보관한다. 적용된 `commits/<commit_id>.md`는
`applied_changes`에 문서 경로, section 경로, 적용 전·후 Markdown과 각각의
SHA-256을 기록한다. 이 자료는 향후 inverse patch와 수동 편집 보존 3-way merge의
기준이며 Actor prompt에는 포함되지 않는다. 표식 도입 전 archive처럼
`applied_changes`가 없는 이력은 자동 inverse할 수 없다.

각 thread의 Actor 비가시 `.wikirag-audit-baseline.json`은 마지막으로 내부 commit
queue가 인지한 canonical Markdown의 경로·revision·전문을 보관한다. 새 thread는
물질화가 끝난 뒤 baseline을 만들고, 정상 update·inverse·migration이 적용된 뒤에만
현재 정본으로 갱신한다. 기존 thread에서 baseline이 처음 만들어질 때는 현재 상태를
기준선으로 채택하므로 과거 변경을 소급 추정하지 않는다.

다음 턴의 pending 적용 전에는 현재 Markdown과 baseline을 먼저 비교한다. H2 구조가
같은 본문 수정은 H2별 `applied_changes`, frontmatter/H1/H2 구조 변경은
`applied_replacements`, 외부 파일 생성·삭제는 `applied_creations`/
`applied_deletions`로 구성한 deterministic `operation: manual` applied archive를
먼저 남긴다. 같은 비교 결과는 같은 commit ID를 사용해 crash retry가 중복 이력을
만들지 않는다. 이후 pending은 최신 외부 편집 위에 기존 section-rebase 규칙으로
적용되며, 같은 section을 건드렸으면 외부 원문을 보존하고 failed conflict가 된다.
`GET .../wiki/manual-audit`은 쓰기 없는 미리보기, `POST
.../wiki/manual-audit/record`는 채팅을 기다리지 않는 명시 기록 경로다. 실시간
watcher/debounce가 없어도 턴 경계의 상태 안전성은 유지되며 watcher는 별도 UI 편의
범위다.

이전 런타임에서 만든 thread는 자동으로 다시 쓰지 않는다.
`GET /api/conversations/{thread_id}/wiki/migration`은 각 character의 `현재 상태`
아래에 runtime-owned `욕구와 컨디션`, `Personality Change Ledger`,
`Reproductive State`가 빠졌는지 미리 보기만 한다.
`POST .../wiki/migration/apply`를 사용자가 명시적으로 실행하면 기존 H2의 다른
내용을 그대로 보존한 complete-section patch를 `operation: manual` commit으로
즉시 적용하고 before/after 원문과 hash를 archive한다. 기존 `commit.md`가 있거나
character에 완전한 `현재 상태` H2가 없으면 아무것도 쓰지 않고 conflict를
반환한다. 같은 migration은 멱등적이며 적용 archive의 일반 inverse 계약을 따른다.

새 commit은 `user_message_id`와 `assistant_message_id`를 저장하고 assistant
메시지는 `wiki_commit_id`로 같은 commit을 가리킨다. Applied inverse는 원래
archive를 수정하지 않고 `operation: inverse`, `source_commit_id`를 가진 새
commit으로 기록한다. 문서 전체 replacement도 exact after revision일 때 before
전문으로 원자 복원하고 이후 수정됐으면 충돌한다. 현재 section이 after와 같으면 before로 복원하고, 다른 줄의
수동 편집은 line-based 3-way merge로 유지한다. 같은 줄 변경이나 section 구조
충돌은 어떤 Markdown도 쓰지 않고 비교 payload만 반환한다.

중간 과거 턴의 안전 분기는 원본 `threads/<source_thread_id>`를 수정하지 않고
새 `threads/<branch_thread_id>`를 만든다. Canonical Markdown과 runtime marker는
새 thread ID로 다시 소유권을 표시하며, `commit.md`, lock, debug 산출물은 복사하지
않는다. 선택한 사용자 입력 이후의 message-linked applied commit을 복사본에서
역순 inverse한 뒤, 대화 메시지는 선택 입력 직전까지만 남기고 그 입력을 UI 초안으로
반환한다. 복사된 `commits/` archive는 원본 이력의 provenance로 유지한다.

파일 경로를 바꾸더라도 `id`는 유지한다. 스캐폴드 문서는 예를 들어
`world:demo_world`, `world:demo_world:prose`, `thread:thread_001`,
`thread:thread_001:scene:current`처럼 namespace를 포함한다. 전체 ID의 중복을
검사하는 인덱스는 후속 단계에서 구현한다.

## 제목과 섹션 주소

- `#`은 문서의 고유 대상을 나타내며 patch 주소에는 포함하지 않는다.
- `##`는 큰 정보 영역이다.
- `###`는 기본 수정 단위다.
- `####` 이하는 꼭 필요한 세부 수정 단위에만 사용한다.
- 섹션은 해당 제목부터 다음 동급 또는 상위 제목 직전까지다.
- 같은 문서 안에서 완전히 같은 제목 경로를 두 번 사용할 수 없다.
- leaf 제목은 현재 값이 아니라 그 섹션의 의미를 설명해야 한다.

예를 들어 `## 기본 신상` 아래의 `### 나이와 생년월일`은
`("기본 신상", "나이와 생년월일")`로 주소화한다. `### 나이: 24세`처럼
값을 제목에 넣으면 값이 바뀔 때 주소도 바뀌므로 허용하지 않는다.

## Gameplay Updater 수정 경계

`SectionPatch.evidence_source`는 `player_input` 또는 `actor_response`다.
`evidence`는 지정한 원문에 실제로 존재하는 연속된 exact quote여야 한다.
현재 장면 전체 H2가 Actor 응답의 NPC·환경 결과와 사용자 입력의 관찰 가능한
플레이어 행동을 함께 보존해야 하면 `evidence_source: actor_response`와 별도로
`player_evidence`에 사용자 입력의 연속된 exact quote를 넣을 수 있다.
`player_evidence`는 `scene/current.md`의 complete-H2 patch에서만 허용한다.
플레이어의 감정·동의·믿음·욕구를 추론하거나 관계 입장을 확정하는 근거로는
사용할 수 없다.

- 플레이어 캐릭터의 행동, 이동, 감정, 선택과 현재 상태는 사용자 입력만 근거로 한다.
- Actor 응답은 NPC, 환경, 시간과 플레이어 행동을 새로 만들지 않는 결과의 근거다.
- NPC 행동의 대상이나 기준점으로 플레이어 이름이 등장하는 것만으로 플레이어 행동으로 판정하지 않는다.
- `함께 가자고 요구했다` 같은 제안은 공동 이동이 아니며, 실제 수락·이동·도착을 확정한 문장만 차단한다.
- 캐릭터 문서에서는 `## 현재 상태`만 gameplay Updater가 수정할 수 있다.
- `현재 상태 > 욕구와 컨디션`은 `Needs: hunger=...; rest=...; social=...; fun=...; safety=...; libido=...` canonical 행을 사용한다. gameplay 모델은 이 section을 수정하지 않고 accepted header 경과 시간 기반 결정적 규칙이 수치와 pressure만 갱신한다. 작성자가 기록한 `Condition` 서술은 보존한다.
- `Personality Change Ledger`와 `Reproductive State`도 일반 gameplay 모델이 수정하지 않는다. 기본-off 장기 시스템 gate가 전용 section patch로만 변경한다.
- `Reproductive State`는 작성자가 프로필에 `## 현재 상태` H2를 직접 쓰고 `- Menstrual cycle: enabled`를 남길 때만 켜진다. 런타임은 그 H2가 **아예 없을 때만** 기본 블록을 붙이므로, H2를 직접 쓰는 프로필은 runtime-owned H3(`현재 위치와 활동`, `신체 상태와 감정 상태`, `욕구와 컨디션`, `Personality Change Ledger`, `Reproductive State`)를 모두 포함해야 한다. 하나라도 빠지면 해당 캐릭터의 needs·성격 원장·주기가 오류 없이 조용히 멈춘다.
- `Reproductive State`는 Actor 문서 블록에 전혀 들어가지 않는다. Actor에게는 Graph와 같은 공용 `CYCLE:` 체크리스트 한 줄로만 전달되며, 그 줄은 주기 일차 같은 정수 대신 국면(`생리 중`/`난포기`/`가임기`/`황체기`)과 `pregnancy_risk`만 노출한다. 임신 중이면 단계와 신체 단서만 주고 정확한 일수·주수를 세지 않도록 명시한다. 정수를 그대로 주면 모델이 "N일째"를 서술에 받아쓰기 때문이며, 이는 Graph에서 먼저 확인된 제약이다.
- 체크리스트 줄은 활성 Actor 캐릭터에 대해서만 만든다. 결정적 tick도 활성 Actor 캐릭터만 대상으로 하므로, 시뮬레이션하지 않는 주기를 Actor에게 알리지 않는다.
- `Contraception`은 `none`과 `oral`을 가지며 `Menstrual cycle` 바로 다음 줄에 온다. 값이 없거나 알 수 없으면 `none`으로 강등한다. `oral`은 임신 확률을 공용 모델의 게임플레이 상수로 낮추고 체크리스트의 가임 위험 표시도 함께 낮추지만 0으로 만들지는 않는다. 사후피임약은 지속 상태가 아니라 사건으로 다루어 `Internal ejaculation count this cycle`을 0으로 되돌린다.
- 이번 턴의 보호 여부는 Actor가 응답 끝의 숨김 `<ooc>` 블록에 `- Protection: none|condom|n/a` 한 줄로 보고한다. 콘돔은 여러 턴 전에 착용될 수 있고 Updater와 postprocessor는 대화 히스토리를 받지 않으므로, 이 사실을 아는 컴포넌트는 Actor뿐이다. 값이 `condom`이나 `n/a`면 위험을 세지 않고, `none`이면 산문에 관용 표현이 없어도 위험으로 센다. 블록이 없거나 형식이 어긋나면 기존 산문 판정으로 물러난다.
- `<ooc>` 블록은 사용자에게 보이지 않으며 산문에 영향을 주거나 언급되어서는 안 된다. 이 채널에는 히스토리가 있어야만 알 수 있는 사실만 싣는다. 관계 변화, 사건 생성, 이동처럼 이번 턴 산문만으로 판정 가능한 것은 계속 Updater가 소유한다. 제안자를 둘로 늘리면 충돌하는 patch를 조정할 근거가 없어진다.
- 피임 변화 판정은 gameplay Updater가 아니라 `Reproductive State`를 소유한 postprocessor가 한다. 값싼 부분 문자열 게이트가 먼저 걸러 대부분의 턴은 추가 호출 없이 끝나고, 게이트에 걸린 턴만 작은 구조화 호출로 실제 상태 변화인지 판정한다. 단순 언급, 가정, 타인의 피임은 상태를 바꾸지 않는다.
- 정본 Markdown은 계속 정수와 부기 필드를 보관하고 Updater·postprocessor는 전문을 그대로 받는다. 축약은 표시 전용이며 정본을 바꾸지 않는다.
- 정적 외형, 이력, 성격, 능력, 관계와 시나리오 사실은 Obsidian이나 별도 편집 흐름에서 수정한다.
- thread relationship은 `owner` Actor profile이 다른 participant를 보는 방향성 문서다.
- 관계 문서는 affinity/trust 수치를 저장하지 않고 `## Relationship Development` 아래에 시작 이후의 durable 변화를 자연어 bullet로 누적한다.
- 관계 patch는 complete H2, `actor_response` exact quote와 현재 Actor owner가 필요하다. 기존 durable bullet을 삭제·의역하거나 플레이어의 행동·동의·감정·믿음·욕구·관계 입장을 확정하면 거부한다.
- 일상적 친절, 근접, 당황, 매력, 순응, 흥분과 친밀 행위만으로 관계 변화를 기록하지 않는다.
- `scene/current.md`는 현재 장면 H2 전체를 한 patch로 교체한다.
- `현재 장면`과 legacy `시작 기준`을 혼동한 출력은 실제 문서에 하나만 존재하는 경우 그 H2 제목으로 정규화한다.
- 활성 인물의 공유 위치와 활동은 scene이 정본이며 character 문서에 반복하지 않는다.
- `thread.md`는 대화 관리 문서이므로 Updater가 수정하지 않는다.

`scene/current.md`의 현재 장면 H2에는 `### Time and Place`, legacy
`### 시작 시각과 장소` 또는 `### 현재 시각과 장소` 중 정확히 하나가 있어야 한다.
런타임은 accepted Actor 응답의 첫 굵은 헤더를 파싱해 이 하위 섹션을 동기화한다.
같은 날짜의 시간 전진은 허용하고 시간 역행은 무시한다. 날짜 변경은 사용자 입력에
명시적인 다음 날·미래 점프가 있을 때만 허용하며, 장소 변경은 새 장소가 사용자
입력에 구체적으로 등장할 때만 허용한다. 이 규칙은 모델이 scene patch를 생략해도
적용되지만, 안전한 값이 현재 정본과 같으면 새 commit을 만들지 않는다.

이 경계를 위반한 모델 출력은 commit으로 저장하지 않고 재시도한다. 재시도 후에도
유효한 결과가 없으면 canonical Markdown을 변경하지 않는다.

### `CreateDocument`, Event와 Memory

Gameplay Updater의 신규 문서 생성은 durable Event와 owner-private Memory를
허용한다. Event는
`document_type: event`, `event:<stable-ascii-slug>` ID, 단일 행 제목·시각·장소,
참여자·목격자, 발생 사실·직접 결과·남은 영향과 exact-quote 근거를 구조화해
반환한다. 런타임이 `event.md` template으로 `events/<slug>.md`를 렌더링하므로
모델이 frontmatter나 vault 경로를 직접 작성하지 않는다.

Memory는 `document_type: memory`, `memory:<stable-ascii-slug>` ID, 정확한
`character_profile:*` owner, 관련 Event ID, 형성 계기·시각·장소, 기억 내용,
해석·감정·확신·왜곡 가능성을 구조화해 반환한다. Actor 응답은 현재 Actor owner,
player 입력은 player owner의 Memory만 만들 수 있다. 다른 profile의 Memory는
거부한다. 관련 Event는 기존 문서 또는 같은 Updater 결과에 존재해야 한다. 내부
Event ID는 검증에만 사용하고 렌더링된 Memory 본문에는 Event H1 제목을 기록한다.

Memory template visibility는 `[actor, updater]`다. Actor prompt에는 현재 NPC
profile과 `owner`가 일치하는 Memory만 포함하지만, Updater는 모든 owner의
Memory 전문을 받아 상태를 검증한다. Memory는 객관적 Event 요약이 아니라 owner의
주관적 인식이며 불확실성과 왜곡 가능성을 보존한다.

생성 문서 frontmatter에는 `source_commit_id`와 가능한 경우
`source_user_message_id`·`source_assistant_message_id`를 기록한다. 기존 ID나
경로는 덮어쓰지 않는다. Applied inverse는 문서 revision과 원문이 그대로일 때만
삭제하고, 수동 편집이 있으면 conflict로 중단한다.

Goal·Item·Secret도 같은 구조화 생성과 inverse 계약을 사용한다. Goal과 Item은
owner가 현재 Actor일 때만 Actor prompt에 들어간다. Secret template visibility는
`[actor, updater, player]`지만 runtime이 현재 Actor profile을 `owner + knowers`와
대조해 알려진 Secret만 포함한다. `hidden`/`suspected` 상태의 실제 내용은 NPC 판단에
사용할 수 있으나 독자 설명으로 누설하지 않고 이미 정본에 기록된 공개 단서만
드러낸다. `revealed`로 전환된 뒤에만 실제 내용을 공개적으로 사용할 수 있다.
가시 Actor 출력은 모든 hidden/suspected Secret의 실제 내용과 normalized/token
중첩을 다시 검사한다. 누설이 감지되면 output repair가 private truth를 제거하고,
repair가 꺼져 있거나 수정 후에도 남으면 실제 내용을 오류 문자열에 넣지 않고
응답을 거부한다. 공개 단서만 재사용한 출력은 누설로 보지 않는다.

각 Updater 실행은 thread의 `debug/updater/<run>/` 아래에 요청, 모델 원문,
검증 오류와 최종 상태를 `.txt`/`.json`으로 남긴다. 진단 자료는 `.md` 확장자를
사용하지 않으므로 runtime Markdown 문서 검색에 포함되지 않는다.
재시도 correction prompt에는 직전 오류 하나가 아니라 이전 시도의 서로 다른
거부 사유를 모두 누적한다. 한 규칙을 고치다가 앞서 해결한 규칙을 다시 위반하는
진동을 막기 위한 계약이다.

## 문서 템플릿

템플릿은 `src/wiki/templates/` 아래에 있고 큰 Python 문자열로 복제하지 않는다.
현재 제공되는 문서 종류는 world, prose, thread, scene, scene_prompt, scenario, character
profile, thread character, relationship, event, memory, goal, item, secret,
location, organization이다. Scenario 문서 종류에는 역할이 분리된
`scenario.md`, `scenario_start_state.md`, `scenario_opening_scene.md` 템플릿이
있고, 선택형 장면 규정에는 `scene_prompt.md` 템플릿이 있다.

스캐폴드 함수는 world의 `world.md`와 `prose.md`, thread의 `thread.md`와
`scene/current.md`, 그리고 표준 하위 디렉터리를 만든다. 대화 초기화는 character
물질화 뒤 활성 Actor→player의 owner 관계 원장을 빠진 경우에만 추가한다. 핵심 문서가 하나라도
이미 있더라도 예상 내용과 hash가 같으면 중단된 생성을 이어서 완료한다. 내용이
다르면 즉시 충돌로 중단하며 기존 문서를 덮어쓰거나 지우지 않는다. symlink나
junction을 따라 지정한 vault 범위 밖으로 나가는 경로도 거부한다.

## 아직 확정되지 않은 규격

- 저작·Wiki Explorer용 `[[wikilink]]`의 ID 해석과 상대 경로 규칙
- 문서 ID 전역/월드/스레드 범위 및 중복 진단
- `schema_version` 사이의 자동 마이그레이션
- world 프로필을 thread 캐릭터로 물질화하는 선택·복사 정책
