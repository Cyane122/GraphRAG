# ================================
# tests/wiki_smoke_fixtures.py
#
# Wiki smoke tests share sample documents, a basic store factory, and updater plan/reject helpers here.
#
# Functions
#   - _plan_update(documents: list[WikiDocument], payload: dict[str, object], user_input: str, actor_response: str, player_profile_id: str = "", actor_profile_id: str = "", max_attempts: int = 1, user_message_id: str | None = None, assistant_message_id: str | None = None) -> PendingWikiCommit : 정책을 통과하는 구조화 Updater 응답을 계획해 PendingWikiCommit을 반환합니다.
#   - _expect_update_rejected(documents: list[WikiDocument], payload: dict[str, object], user_input: str, actor_response: str, expected_message: str, player_profile_id: str = "", actor_profile_id: str = "") -> None : 정책 위반 Updater 응답이 거부되고 그 이유가 예외 메시지에 담기는지 검증합니다.
#   - _generate_event_creation(character: WikiDocument, scene: WikiDocument) -> PendingWikiCommit : Plan a validated durable event and matching memory creation.
#   - _character_b_document() -> WikiDocument : Build a third active thread character (B) who is neither player nor Actor.
#   - _scene_document_with_active_b() -> WikiDocument : Build the sample scene document with character B also present in the current scene.
#   - _relationship_b_document() -> WikiDocument : Build character B's relationship-to-player ledger (owner character_profile:character_b).
#   - create_base_store(root: Path) -> tuple[WikiStore, WikiDocument, WikiDocument] : Write and reload the sample character and scene documents.
#   - main() -> None : Print the standalone success marker for this module.
# ================================

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wiki import (  # noqa: E402
    PendingWikiCommit,
    WikiCommitPlanningError,
    WikiDocument,
    WikiStore,
    document_revision,
    parse_frontmatter,
    plan_pending_commit,
)

# 이 스위트의 모든 model 호출은 Mock/AsyncMock으로 대체된다(실제 provider에 닿지 않는다).
# 값 자체는 아무 의미도 갖지 않으므로 하나로 고정한다 - 예전에는 "test-updater"(20곳),
# "gemini-3.1-pro-preview"(6곳), "mock"(1곳)이 난립했다. "test-updater"가 이미 다수였고,
# 실제 provider 이름을 흉내 내지 않아 "이건 테스트 더블이다"를 이름만으로 드러낸다.
_DEFAULT_TEST_UPDATER_MODEL = "test-updater"

_CHARACTER_DOCUMENT = """---
id: character:character_a
type: character
schema_version: 1
visibility: [actor, updater, player]
created_at: 2026-07-21T00:00:00+00:00
world_id: demo_world
thread_id: thread_001
profile_id: character_profile:character_a
description: |
  ## frontmatter 섹션이 아님
---
# 캐릭터 A

## 기본 신상

### 나이와 생년월일

- 나이: 23세
- 생년월일: 2003년 3월 17일

### 직업과 소속

- 직업: 대학생

## 메모

````markdown
```text
<!-- 코드 안의 닫히지 않은 주석
### 실제 섹션이 아님
```
````

   ### 들여쓴 실제 섹션

- 메모: 유지

<!--
### HTML 주석 섹션이 아님
-->

본문 <!--
### 중간에서 시작한 HTML 주석 섹션이 아님
-->

%%
### Obsidian 주석 섹션이 아님
%%

### C#

- 언어: C#

## 현재 상태

### 현재 위치와 활동

- 위치: 현재 장면 참조
- 활동:

### 신체 상태와 감정 상태

- 신체 상태: 안정
- 감정 상태: 평온
"""

_SCENE_DOCUMENT = """---
id: thread:thread_001:scene:current
type: scene
schema_version: 1
visibility: [actor, updater, player]
created_at: 2026-07-21T00:00:00+00:00
world_id: demo_world
thread_id: thread_001
---
# 현재 장면

## 시작 기준

### 시작 시각과 장소

- 2026년 7월 23일 13시, 대학 도서관이다.

### 인물 위치와 현재 상태

- 캐릭터 A는 창가 책상에 앉아 있다.

### 당장의 계기

- 발표 자료 제출 시간이 다가온다.
"""

_EVENT_DOCUMENT = """---
id: event:existing-outage
type: event
schema_version: 1
thread_id: thread_001
visibility: [actor, updater, player]
created_at: 2026-07-21T00:00:00+00:00
---
# Existing Outage

## 발생 정보

### 시각과 장소

- 시각: 2026-07-23 13:00
- 장소: 대학 도서관

## 사건 내용

### 객관적으로 발생한 일

- 발생 내용: The library power failed.

## 진행 상태

- 상태: concluded
- 진행 경과: The outage was resolved in the same turn.
- 종료 시각: 2026-07-23 13:00
"""

_ONGOING_EVENT_DOCUMENT = """---
id: event:ongoing-search
type: event
schema_version: 1
thread_id: thread_001
visibility: [actor, updater, player]
created_at: 2026-07-21T00:00:00+00:00
---
# Ongoing Search

## 발생 정보

### 시각과 장소

- 시각: 2026-07-23 13:00
- 장소: 대학 도서관

### 참여자와 목격자

- 참여자: 캐릭터 A; NPC
- 목격자: None.

## 사건 내용

### 객관적으로 발생한 일

- 발생 내용: The search for the missing files began in the library.

### 직접 결과와 남은 영향

- 직접 결과: The participants started checking the workstations.
- 남은 영향: The search is still underway.

## 진행 상태

- 상태: ongoing
- 진행 경과: The participants are still checking each workstation.
- 종료 시각:
"""

_RELATIONSHIP_DOCUMENT = """---
id: relationship:npc--character_a
type: relationship
schema_version: 1
thread_id: thread_001
owner: character_profile:npc
participants: [character_profile:npc, character_profile:character_a]
visibility: [actor, updater, player]
created_at: 2026-07-21T00:00:00+00:00
---
# NPC's Relationship with 캐릭터 A

## Relationship Development

### Accepted Durable Changes

- No durable relationship change has occurred since the story began.
"""

def create_base_store(root: Path) -> tuple[WikiStore, WikiDocument, WikiDocument]:
    """Write and reload the sample character and scene documents."""
    store = WikiStore(root)
    store.write_document("characters/character_a.md", _CHARACTER_DOCUMENT)
    store.write_document("scene/current.md", _SCENE_DOCUMENT)
    return (
        store,
        store.read_document("characters/character_a.md"),
        store.read_document("scene/current.md"),
    )

async def _plan_update(
    documents: list[WikiDocument],
    payload: dict[str, object],
    user_input: str,
    actor_response: str,
    player_profile_id: str = "",
    actor_profile_id: str = "",
    max_attempts: int = 1,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
) -> PendingWikiCommit:
    """정책을 통과하는 구조화 Updater 응답을 계획해 PendingWikiCommit을 반환합니다.

    `_expect_update_rejected`의 거울짝이다 - `commit_planner.get_model` patch와 mock
    model 조립을 흡수하므로, 호출자에게 고유한 것은 payload dict와 그 뒤의 assert뿐이다.
    """
    model = Mock()
    model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))
    )
    with patch("src.wiki.commit_planner.get_model", return_value=model):
        return await plan_pending_commit(
            documents=documents,
            user_input=user_input,
            actor_response=actor_response,
            model_name=_DEFAULT_TEST_UPDATER_MODEL,
            max_attempts=max_attempts,
            player_profile_id=player_profile_id,
            actor_profile_id=actor_profile_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        )

async def _expect_update_rejected(
    documents: list[WikiDocument],
    payload: dict[str, object],
    user_input: str,
    actor_response: str,
    expected_message: str,
    player_profile_id: str = "",
    actor_profile_id: str = "",
) -> None:
    """정책을 위반한 구조화 Updater 응답이 거부되고, 그 이유가 예외 메시지에 실제로
    담기는지 검증합니다(타입만 보고 이유를 보지 않으면 무관한 버그도 초록이 된다)."""
    model = Mock()
    model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))
    )
    with patch("src.wiki.commit_planner.get_model", return_value=model):
        try:
            await plan_pending_commit(
                documents=documents,
                user_input=user_input,
                actor_response=actor_response,
                model_name=_DEFAULT_TEST_UPDATER_MODEL,
                max_attempts=1,
                player_profile_id=player_profile_id,
                actor_profile_id=actor_profile_id,
            )
        except WikiCommitPlanningError as exc:
            assert expected_message in str(exc), (
                f"expected {expected_message!r} in rejection message, got: {exc}"
            )
        else:
            raise AssertionError("Updater policy violation must be rejected")

async def _generate_event_creation(
    character: WikiDocument,
    scene: WikiDocument,
) -> PendingWikiCommit:
    """Exact evidence가 있는 durable event 생성과 source turn 연결을 계획합니다.

    `smoke_wiki_creation`(자체 스위트)과 `smoke_wiki_policy`(event/memory 짝 검증)가
    함께 쓰므로 두 check 모듈이 아니라 이 공유 fixtures 모듈에 둔다.
    """
    evidence = "도서관 정전으로 발표 파일 제출 창구가 일시 중단됐다."
    payload = {
        "summary": "발표 제출을 막는 정전 사건 생성",
        "patches": [],
        "creations": [{
            "document_type": "event",
            "document_id": "event:library-power-outage",
            "title": "Library Power Outage",
            "occurred_at": "2026-07-23 13:10",
            "location": "대학 도서관",
            "participants": [],
            "witnesses": ["캐릭터 A"],
            "facts": [
                "A power outage interrupted the presentation-file submission desk."
            ],
            "direct_results": [
                "Digital submissions are unavailable until service returns."
            ],
            "lasting_effects": [
                "The presentation deadline remains active during the interruption."
            ],
            "evidence": evidence,
            "evidence_source": "actor_response",
            "confidence": 0.96,
        }, {
            "document_type": "memory",
            "document_id": "memory:character-a-remembers-outage",
            "title": "Character A Remembers the Outage",
            "owner": "character_profile:character_a",
            "related_event_id": "event:library-power-outage",
            "formation_trigger": "The submission desk stopped during the outage.",
            "formed_at": "2026-07-23 13:10",
            "location": "대학 도서관",
            "remembered_content": (
                "Character A remembers the submission desk going offline."
            ),
            "interpretation": (
                "Character A believes the deadline can fail for reasons outside personal control."
            ),
            "emotion": "Urgency and frustration.",
            "certainty": "High about the outage and uncertain about its duration.",
            "distortion_risk": "Later anxiety may exaggerate how long the outage lasted.",
            "evidence": evidence,
            "evidence_source": "actor_response",
            "confidence": 0.91,
        }],
    }
    actor_response = (
        "**2026년 7월 23일 목요일 13시 10분, 대학 도서관**\n\n"
        f"{evidence}"
    )
    return await _plan_update(
        [character, scene],
        payload,
        "상황을 지켜본다.",
        actor_response,
        actor_profile_id="character_profile:character_a",
        user_message_id="user-event",
        assistant_message_id="assistant-event",
    )


def _character_b_document() -> WikiDocument:
    """Build a third active thread character (B) who is neither player nor Actor.

    Used by the severable-authority checks (`SeverableCreationAuthorityError`):
    B must be a real active thread profile (present in `available_profile_ids`)
    so that authority-checking code exercises the "active but not player/Actor"
    branch specifically, not the "unknown owner" branch.
    """
    content = (
        _CHARACTER_DOCUMENT
        .replace("id: character:character_a", "id: character:character_b")
        .replace(
            "profile_id: character_profile:character_a",
            "profile_id: character_profile:character_b",
        )
        .replace("# 캐릭터 A", "# 캐릭터 B")
    )
    return WikiDocument(
        path="characters/character_b.md",
        revision=document_revision(content),
        content=content,
        metadata=parse_frontmatter(content),
    )


def _scene_document_with_active_b() -> WikiDocument:
    """Build the sample scene document with character B also present in the current scene.

    Used by the scene-active third-party owner-authority checks
    (`scene_active_profile_ids` in `commit_policy.py`), as opposed to the
    default `_SCENE_DOCUMENT` (which names only 캐릭터 A and therefore keeps
    B scene-inactive for the severable-authority checks).
    """
    content = _SCENE_DOCUMENT.replace(
        "- 캐릭터 A는 창가 책상에 앉아 있다.",
        "- 캐릭터 A는 창가 책상에 앉아 있다. 캐릭터 B도 옆자리에 함께 있다.",
    )
    return WikiDocument(
        path="scene/current.md",
        revision=document_revision(content),
        content=content,
        metadata=parse_frontmatter(content),
    )


def _relationship_b_document() -> WikiDocument:
    """Build character B's relationship-to-player ledger (owner character_profile:character_b).

    Used by the scene-active relationship-patch authority checks — the same
    active-but-not-Actor ownership `_character_b_document` exercises for
    Memory/Goal/Item/Secret creation, applied instead to a relationship patch
    (`_check_relationship_patch` in `commit_patch_policy.py`).
    """
    content = (
        _RELATIONSHIP_DOCUMENT
        .replace(
            "id: relationship:npc--character_a",
            "id: relationship:character_b--character_a",
        )
        .replace("owner: character_profile:npc", "owner: character_profile:character_b")
        .replace(
            "participants: [character_profile:npc, character_profile:character_a]",
            "participants: [character_profile:character_b, character_profile:character_a]",
        )
        .replace(
            "# NPC's Relationship with 캐릭터 A",
            "# 캐릭터 B's Relationship with 캐릭터 A",
        )
    )
    return WikiDocument(
        path="relationships/character_b--character_a.md",
        revision=document_revision(content),
        content=content,
        metadata=parse_frontmatter(content),
    )


def main() -> None:
    """Print the standalone success marker for this module."""
    print("wiki_smoke_fixtures: ok")

if __name__ == "__main__":
    main()
