# ================================
# tests/smoke_wiki_v2.py
#
# Wiki V2 스캐폴드, 섹션 파싱, Updater 재시도, 지연 적용을 검증합니다.
#
# Functions
#   - _generate_with_one_retry(document: WikiDocument) -> PendingWikiCommit : 첫 실패 뒤 유효한 Updater 결과를 반환합니다.
#   - _check_retry_exhaustion(document: WikiDocument) -> None : 모든 Updater 시도 실패 시 예외와 시도별 원문 진단 자료를 검증합니다.
#   - _expect_update_rejected(documents: list[WikiDocument], payload: dict, user_input: str, actor_response: str, player_profile_id: str = "", actor_profile_id: str = "") -> None : 정책 위반 Updater 결과가 거부되는지 검증합니다.
#   - _check_update_policy(character: WikiDocument, scene: WikiDocument) -> None : 플레이어 출처·정적 섹션·event-memory 결속·장면·관계 원자성과 Event/Memory patch 경계를 검증합니다.
#   - _check_accepted_header_sync(scene: WikiDocument) -> None : accepted 헤더의 시간·장소 hard guard와 결정적 scene patch를 검증합니다.
#   - _generate_event_creation(character: WikiDocument, scene: WikiDocument) -> PendingWikiCommit : 검증된 durable event 신규 문서 commit을 만듭니다.
#   - _generate_goal_creation(character: WikiDocument) -> PendingWikiCommit : owner=Actor인 durable goal 신규 문서 commit을 만듭니다.
#   - _check_goal_item_secret(character: WikiDocument) -> PendingWikiCommit : goal/item/secret 생성·갱신 권한과 knower-scoped 가시성을 검증합니다.
#   - _check_postprocess() -> None : 결정적 needs와 게이트 postprocessor의 증거·병합 안전성을 검증합니다.
#   - _check_recall() -> None : 예산 초과 시 최근성·구조 관련성 recall 축소를 검증합니다.
#   - _check_diagnostics(vault_root: Path) -> None : 중복 문서 ID와 잘못된 frontmatter 진단을 검증합니다.
#   - _check_scaffolds(root: Path) -> None : world/thread 템플릿과 frontmatter 로딩을 검증합니다.
#   - _check_wiki_context_scenario_overrides(root: Path) -> None : scenario.md의 npc override와 cast allowlist를 검증합니다.
#   - main() -> None : 임시 vault에서 Wiki V2 핵심 흐름을 검증합니다.
# ================================

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wiki import (  # noqa: E402
    PendingCommitExists,
    PendingWikiCommit,
    SectionPatch,
    WikiCommitQueue,
    WikiDocument,
    WikiFrontmatterError,
    WikiScaffoldError,
    WikiStore,
    WikiCommitPlanningError,
    WikiUpdaterResult,
    diagnose_wiki_scope,
    document_revision,
    plan_pending_commit,
    parse_frontmatter,
    parse_markdown_sections,
    render_wiki_template,
    scaffold_thread,
    scaffold_world,
)
from src.wiki.commit_planner import _synchronize_accepted_header  # noqa: E402
from src.wiki.context import (  # noqa: E402
    WikiContextError,
    _profile_documents,
    initialize_wiki_thread,
    load_wiki_setup,
)


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


def _check_accepted_header_sync(scene: WikiDocument) -> None:
    """Accepted 헤더의 시간 전진, 장소 grounding과 날짜 점프 guard를 검증합니다."""
    forward = WikiUpdaterResult(summary="", patches=[])
    _synchronize_accepted_header(
        forward,
        [scene],
        "계속 이야기한다.",
        "**2026년 7월 23일 목요일 13시 05분, 대학 도서관**\n\n대화가 이어졌다.",
    )
    assert len(forward.patches) == 1
    assert "13:05" in forward.patches[0].replacement_markdown
    assert "대학 도서관" in forward.patches[0].replacement_markdown

    ungrounded_move = WikiUpdaterResult(summary="", patches=[])
    _synchronize_accepted_header(
        ungrounded_move,
        [scene],
        "계속 이야기한다.",
        "**2026년 7월 23일 목요일 13시 06분, 학생회관**\n\n문이 닫혔다.",
    )
    assert "13:06" in ungrounded_move.patches[0].replacement_markdown
    assert "대학 도서관" in ungrounded_move.patches[0].replacement_markdown
    assert "학생회관" not in ungrounded_move.patches[0].replacement_markdown

    grounded_move = WikiUpdaterResult(summary="", patches=[])
    _synchronize_accepted_header(
        grounded_move,
        [scene],
        "학생회관으로 이동한다.",
        "**2026년 7월 23일 목요일 13시 06분, 학생회관**\n\n문이 닫혔다.",
    )
    assert "학생회관" in grounded_move.patches[0].replacement_markdown

    rejected_jump = WikiUpdaterResult(summary="", patches=[])
    _synchronize_accepted_header(
        rejected_jump,
        [scene],
        "계속 이야기한다.",
        "**2026년 7월 24일 금요일 09시 00분, 대학 도서관**\n\n아침이었다.",
    )
    assert rejected_jump.patches == []

    explicit_jump = WikiUpdaterResult(summary="", patches=[])
    _synchronize_accepted_header(
        explicit_jump,
        [scene],
        "다음날 아침까지 기다린다.",
        "**2026년 7월 24일 금요일 09시 00분, 대학 도서관**\n\n아침이었다.",
    )
    assert "July 24, 2026" in explicit_jump.patches[0].replacement_markdown


async def _generate_event_creation(
    character: WikiDocument,
    scene: WikiDocument,
) -> PendingWikiCommit:
    """Exact evidence가 있는 durable event 생성과 source turn 연결을 계획합니다."""
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
    model = Mock()
    model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))
    )
    actor_response = (
        "**2026년 7월 23일 목요일 13시 10분, 대학 도서관**\n\n"
        f"{evidence}"
    )
    with patch("src.wiki.commit_planner.get_model", return_value=model):
        return await plan_pending_commit(
            documents=[character, scene],
            user_input="상황을 지켜본다.",
            actor_response=actor_response,
            model_name="test-updater",
            actor_profile_id="character_profile:character_a",
            user_message_id="user-event",
            assistant_message_id="assistant-event",
        )


async def _generate_goal_creation(character: WikiDocument) -> PendingWikiCommit:
    """owner=Actor인 durable goal 신규 문서 생성 commit을 계획합니다."""
    evidence = "캐릭터 A는 이번 시험에 반드시 합격하겠다고 다짐했다."
    payload = {
        "summary": "새 목표 생성",
        "patches": [],
        "creations": [{
            "document_type": "goal",
            "document_id": "goal:pass-exam",
            "title": "Pass the Exam",
            "owner": "character_profile:character_a",
            "desired_outcome": "이번 시험에 합격한다.",
            "success_look": "합격자 명단에 이름이 오른다.",
            "motivation": "학업을 이어가기 위한 자립.",
            "priority": "높음",
            "current_step": "핵심 과목을 복습하는 중.",
            "next_action": "기출문제를 3개년 분량 푼다.",
            "obstacles": "준비할 시간이 부족하다.",
            "completion_conditions": "최종 합격 발표가 나온다.",
            "evidence": evidence,
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    model = Mock()
    model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))
    )
    with patch("src.wiki.commit_planner.get_model", return_value=model):
        return await plan_pending_commit(
            documents=[character],
            user_input="상황을 지켜본다.",
            actor_response=evidence,
            model_name="test-updater",
            actor_profile_id="character_profile:character_a",
            user_message_id="user-goal",
            assistant_message_id="assistant-goal",
        )


async def _check_goal_item_secret(character: WikiDocument) -> PendingWikiCommit:
    """goal/item/secret 생성·갱신 권한과 knower-scoped secret 가시성을 검증합니다."""
    goal_pending = await _generate_goal_creation(character)
    assert len(goal_pending.creations) == 1
    assert goal_pending.creations[0].document == "goals/pass-exam.md"
    goal_content = goal_pending.creations[0].content
    assert "source_commit_id" in goal_content
    goal_document = WikiDocument(
        path="goals/pass-exam.md",
        revision=document_revision(goal_content),
        content=goal_content,
        metadata=parse_frontmatter(goal_content),
    )

    # owner=Actor인 goal은 actor_response 근거만 허용한다(exact-quote는 통과시켜 권한만 검사).
    owner_authority_payload = {
        "summary": "잘못된 출처로 목표 생성",
        "patches": [],
        "creations": [{
            "document_type": "goal",
            "document_id": "goal:secret-plan",
            "title": "Secret Plan",
            "owner": "character_profile:character_a",
            "desired_outcome": "계획을 이룬다.",
            "success_look": "계획이 완성된다.",
            "motivation": "개인적 동기.",
            "priority": "중간",
            "current_step": "준비 중.",
            "next_action": "다음 단계를 밟는다.",
            "obstacles": "장애물이 있다.",
            "completion_conditions": "목표를 달성한다.",
            "evidence": "나는 새로운 계획을 세운다.",
            "evidence_source": "player_input",
            "confidence": 0.9,
        }],
    }
    await _expect_update_rejected(
        [character],
        owner_authority_payload,
        "나는 새로운 계획을 세운다.",
        "캐릭터 A가 고개를 끄덕였다.",
        actor_profile_id="character_profile:character_a",
    )

    # goal의 진행 상태 patch는 허용된다.
    progress_patch = {
        "summary": "목표 진행 갱신",
        "patches": [{
            "document": goal_document.path,
            "base_revision": goal_document.revision,
            "section_path": ["진행 상태", "현재 단계와 다음 행동"],
            "replacement_markdown": (
                "### 현재 단계와 다음 행동\n\n"
                "- 상태: active\n"
                "- 현재 단계: 기출 3개년을 모두 끝냈다.\n"
                "- 다음 행동: 모의고사를 본다."
            ),
            "evidence": "캐릭터 A는 기출문제 3개년 분량을 모두 끝냈다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    progress_model = Mock()
    progress_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(progress_patch, ensure_ascii=False))
    )
    with patch("src.wiki.commit_planner.get_model", return_value=progress_model):
        progress_pending = await plan_pending_commit(
            documents=[character, goal_document],
            user_input="지켜본다.",
            actor_response="캐릭터 A는 기출문제 3개년 분량을 모두 끝냈다.",
            model_name="test-updater",
            max_attempts=1,
            actor_profile_id="character_profile:character_a",
        )
    assert progress_pending.patches[0].section_path == ("진행 상태", "현재 단계와 다음 행동")

    # goal의 정체성 섹션은 read-only다.
    identity_patch = {
        "summary": "목표 정체성 변경",
        "patches": [{
            "document": goal_document.path,
            "base_revision": goal_document.revision,
            "section_path": ["목표 정체성", "원하는 결과와 성공 모습"],
            "replacement_markdown": (
                "### 원하는 결과와 성공 모습\n\n"
                "- 원하는 결과: 전혀 다른 목표\n"
                "- 성공 모습: 바뀐 성공"
            ),
            "evidence": "캐릭터 A는 목표를 완전히 바꿨다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    await _expect_update_rejected(
        [character, goal_document],
        identity_patch,
        "지켜본다.",
        "캐릭터 A는 목표를 완전히 바꿨다.",
        actor_profile_id="character_profile:character_a",
    )

    # item 생성이 성공한다.
    item_payload = {
        "summary": "아이템 생성",
        "patches": [],
        "creations": [{
            "document_type": "item",
            "document_id": "item:brass-key",
            "title": "Brass Key",
            "owner": "character_profile:character_a",
            "kind": "낡은 황동 열쇠",
            "appearance": "표면이 긁힌 작은 황동 열쇠.",
            "function": "오래된 서랍을 연다.",
            "constraint": "이 열쇠는 하나뿐이다.",
            "storage_location": "코트 안주머니.",
            "access_state": "본인이 소지 중.",
            "recent_change": "복도에서 주웠다.",
            "evidence": "캐릭터 A는 복도에서 낡은 황동 열쇠를 주워 주머니에 넣었다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    item_model = Mock()
    item_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(item_payload, ensure_ascii=False))
    )
    with patch("src.wiki.commit_planner.get_model", return_value=item_model):
        item_pending = await plan_pending_commit(
            documents=[character],
            user_input="지켜본다.",
            actor_response=(
                "캐릭터 A는 복도에서 낡은 황동 열쇠를 주워 주머니에 넣었다."
            ),
            model_name="test-updater",
            max_attempts=1,
            actor_profile_id="character_profile:character_a",
        )
    assert item_pending.creations[0].document == "items/brass-key.md"

    # secret 생성이 성공하고 knower에게만 Actor-visible하며 knower 목록이 저장된다.
    secret_payload = {
        "summary": "비밀 생성",
        "patches": [],
        "creations": [{
            "document_type": "secret",
            "document_id": "secret:hidden-debt",
            "title": "Hidden Debt",
            "owner": "character_profile:character_a",
            "knowers": ["character_profile:character_a"],
            "actual_content": "갚지 못한 큰 빚이 있다.",
            "who_knows": "본인만 알고 있다.",
            "concealment": "평소에는 밝게 행동한다.",
            "public_clue": "가끔 전화를 급히 피한다.",
            "misunderstanding": "그저 바쁜 것으로 보인다.",
            "exposure_condition": "독촉장이 발견된다.",
            "exposure_result": "주변의 신뢰가 흔들린다.",
            "evidence": "캐릭터 A는 아무에게도 말 못 한 빚을 떠올리며 표정을 감췄다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    secret_model = Mock()
    secret_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(secret_payload, ensure_ascii=False))
    )
    with patch("src.wiki.commit_planner.get_model", return_value=secret_model):
        secret_pending = await plan_pending_commit(
            documents=[character],
            user_input="지켜본다.",
            actor_response=(
                "캐릭터 A는 아무에게도 말 못 한 빚을 떠올리며 표정을 감췄다."
            ),
            model_name="test-updater",
            max_attempts=1,
            actor_profile_id="character_profile:character_a",
        )
    assert secret_pending.creations[0].document == "secrets/hidden-debt.md"
    secret_metadata = parse_frontmatter(secret_pending.creations[0].content)
    assert secret_metadata is not None
    assert "actor" in secret_metadata.visibility
    assert secret_metadata.model_extra["knowers"] == ["character_profile:character_a"]
    from src.wiki.runtime import _actor_document_visible

    assert _actor_document_visible(
        secret_metadata,
        "character_profile:character_a",
    )
    assert not _actor_document_visible(
        secret_metadata,
        "character_profile:character_b",
    )
    from src.wiki.secret_guard import find_hidden_secret_leaks

    secret_document = WikiDocument(
        path=secret_pending.creations[0].document,
        revision=document_revision(secret_pending.creations[0].content),
        content=secret_pending.creations[0].content,
        metadata=secret_metadata,
    )
    assert find_hidden_secret_leaks(
        "그녀에게는 갚지 못한 큰 빚이 있다.",
        [secret_document],
    )
    assert not find_hidden_secret_leaks(
        "그녀는 가끔 걸려온 전화를 급히 피했다.",
        [secret_document],
    )
    revealed_content = secret_document.content.replace(
        "- 상태: hidden",
        "- 상태: revealed",
    )
    revealed_document = WikiDocument(
        path=secret_document.path,
        revision=document_revision(revealed_content),
        content=revealed_content,
        metadata=parse_frontmatter(revealed_content),
    )
    assert not find_hidden_secret_leaks(
        "그녀에게는 갚지 못한 큰 빚이 있다.",
        [revealed_document],
    )

    secret_status_patch_payload = {
        "summary": "비밀 상태 변경",
        "patches": [{
            "document": secret_document.path,
            "base_revision": secret_document.revision,
            "section_path": ["공개 상태", "공개 단서와 오해"],
            "replacement_markdown": (
                "### 공개 단서와 오해\n\n"
                "- 상태: revealed\n"
                "- 공개 단서: 가끔 전화를 급히 피한다.\n"
                "- 오해: 그저 바쁜 것으로 보인다."
            ),
            "evidence": "캐릭터 A는 가끔 전화를 급히 피했다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
        "creations": [],
    }
    secret_status_patch_model = Mock()
    secret_status_patch_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(secret_status_patch_payload, ensure_ascii=False)
        )
    )
    with patch(
        "src.wiki.commit_planner.get_model",
        return_value=secret_status_patch_model,
    ):
        try:
            await plan_pending_commit(
                documents=[character, secret_document],
                user_input="지켜본다.",
                actor_response="캐릭터 A는 가끔 전화를 급히 피했다.",
                model_name="test-updater",
                max_attempts=1,
                actor_profile_id="character_profile:character_a",
            )
        except WikiCommitPlanningError as exc:
            assert "secrets/hidden-debt.md" in str(exc)
            assert "Runtime-owned secret disclosure status cannot be patched" in str(exc)
        else:
            raise AssertionError("Secret disclosure status patch must be rejected")

    secret_clue_patch_payload = {
        "summary": "비밀 공개 단서 갱신",
        "patches": [{
            "document": secret_document.path,
            "base_revision": secret_document.revision,
            "section_path": ["공개 상태", "공개 단서와 오해"],
            "replacement_markdown": (
                "### 공개 단서와 오해\n\n"
                "- 상태: hidden\n"
                "- 공개 단서: 가끔 전화를 급히 피하고 독촉장을 가방 깊숙이 숨긴다.\n"
                "- 오해: 그저 바쁜 것으로 보인다."
            ),
            "evidence": "캐릭터 A는 가끔 전화를 급히 피하고 독촉장을 가방 깊숙이 숨겼다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
        "creations": [],
    }
    secret_clue_patch_model = Mock()
    secret_clue_patch_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(secret_clue_patch_payload, ensure_ascii=False)
        )
    )
    with patch("src.wiki.commit_planner.get_model", return_value=secret_clue_patch_model):
        secret_clue_pending = await plan_pending_commit(
            documents=[character, secret_document],
            user_input="지켜본다.",
            actor_response="캐릭터 A는 가끔 전화를 급히 피하고 독촉장을 가방 깊숙이 숨겼다.",
            model_name="test-updater",
            max_attempts=1,
            actor_profile_id="character_profile:character_a",
        )
    assert secret_clue_pending.patches[0].document == "secrets/hidden-debt.md"
    assert secret_clue_pending.patches[0].section_path == ("공개 상태", "공개 단서와 오해")

    wikilink_patch_payload = {
        "summary": "목표 patch에 wikilink 삽입",
        "patches": [{
            "document": goal_document.path,
            "base_revision": goal_document.revision,
            "section_path": ["진행 상태", "현재 단계와 다음 행동"],
            "replacement_markdown": (
                "### 현재 단계와 다음 행동\n\n"
                "- 상태: active\n"
                "- 현재 단계: [[vault-note]]를 확인했다.\n"
                "- 다음 행동: 모의고사를 본다."
            ),
            "evidence": "캐릭터 A는 참고 메모를 확인했다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
        "creations": [],
    }
    wikilink_patch_model = Mock()
    wikilink_patch_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(wikilink_patch_payload, ensure_ascii=False)
        )
    )
    with patch("src.wiki.commit_planner.get_model", return_value=wikilink_patch_model):
        try:
            await plan_pending_commit(
                documents=[character, goal_document],
                user_input="지켜본다.",
                actor_response="캐릭터 A는 참고 메모를 확인했다.",
                model_name="test-updater",
                max_attempts=1,
                actor_profile_id="character_profile:character_a",
            )
        except WikiCommitPlanningError as exc:
            assert "goals/pass-exam.md" in str(exc)
            assert "wikilink" in str(exc)
        else:
            raise AssertionError("Wikilink patch must be rejected at planning time")

    wikilink_creation_payload = {
        "summary": "wikilink가 섞인 목표 생성",
        "patches": [],
        "creations": [{
            "document_type": "goal",
            "document_id": "goal:linked-note",
            "title": "Linked Note Goal",
            "owner": "character_profile:character_a",
            "desired_outcome": "준비를 끝낸다.",
            "success_look": "지원서 초안을 완성한다.",
            "motivation": "장학금 기회를 놓치지 않는다.",
            "priority": "높음",
            "current_step": "[[vault-note]]를 읽는 중.",
            "next_action": "요건을 정리한다.",
            "obstacles": "준비 시간이 부족하다.",
            "completion_conditions": "제출 가능한 초안이 생긴다.",
            "evidence": "캐릭터 A는 오늘부터 장학금 신청 준비를 시작하겠다고 마음먹었다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    wikilink_creation_model = Mock()
    wikilink_creation_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(wikilink_creation_payload, ensure_ascii=False)
        )
    )
    with patch(
        "src.wiki.commit_planner.get_model",
        return_value=wikilink_creation_model,
    ):
        try:
            await plan_pending_commit(
                documents=[character],
                user_input="지켜본다.",
                actor_response="캐릭터 A는 오늘부터 장학금 신청 준비를 시작하겠다고 마음먹었다.",
                model_name="test-updater",
                max_attempts=1,
                actor_profile_id="character_profile:character_a",
            )
        except WikiCommitPlanningError as exc:
            assert "goals/linked-note.md" in str(exc)
            assert "wikilink" in str(exc)
        else:
            raise AssertionError("Wikilink creation must be rejected at planning time")

    normal_creation_payload = {
        "summary": "정상 목표 생성",
        "patches": [],
        "creations": [{
            "document_type": "goal",
            "document_id": "goal:scholarship-plan",
            "title": "Scholarship Plan",
            "owner": "character_profile:character_a",
            "desired_outcome": "장학금 신청 준비를 마친다.",
            "success_look": "제출 가능한 신청서 초안이 완성된다.",
            "motivation": "학비 부담을 줄인다.",
            "priority": "높음",
            "current_step": "요건을 정리하는 중.",
            "next_action": "증빙 서류 목록을 적는다.",
            "obstacles": "제출 기한이 가깝다.",
            "completion_conditions": "신청서를 제출한다.",
            "evidence": "캐릭터 A는 오늘부터 장학금 신청 준비를 시작하겠다고 마음먹었다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    normal_creation_model = Mock()
    normal_creation_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(normal_creation_payload, ensure_ascii=False)
        )
    )
    with patch("src.wiki.commit_planner.get_model", return_value=normal_creation_model):
        normal_creation_pending = await plan_pending_commit(
            documents=[character],
            user_input="지켜본다.",
            actor_response="캐릭터 A는 오늘부터 장학금 신청 준비를 시작하겠다고 마음먹었다.",
            model_name="test-updater",
            max_attempts=1,
            actor_profile_id="character_profile:character_a",
        )
    assert normal_creation_pending.creations[0].document == "goals/scholarship-plan.md"

    # 존재하지 않는 profile을 knower로 넣으면 거부한다.
    unknown_knower_payload = json.loads(json.dumps(secret_payload))
    unknown_knower_payload["creations"][0]["document_id"] = "secret:other"
    unknown_knower_payload["creations"][0]["knowers"] = ["character_profile:ghost"]
    await _expect_update_rejected(
        [character],
        unknown_knower_payload,
        "지켜본다.",
        "캐릭터 A는 아무에게도 말 못 한 빚을 떠올리며 표정을 감췄다.",
        actor_profile_id="character_profile:character_a",
    )
    return goal_pending


async def _check_postprocess() -> None:
    """결정적 needs와 게이트 postprocessor의 증거·충돌 안전성을 검증합니다."""
    from src.wiki.postprocess import plan_memory_distortion, _merge_patches
    from src.wiki.needs import plan_needs_decay

    memory_content = (
        "---\nid: memory:m1\ntype: memory\nschema_version: 1\n"
        "thread_id: thread_001\nowner: character_profile:character_a\n"
        "visibility: [actor, updater]\ncreated_at: 2026-07-21T00:00:00+00:00\n---\n"
        "# Memory One\n\n## 주관적 기억\n\n### 기억하는 내용\n\n- 기억 내용: 파일을 찾았다.\n\n"
        "### 해석과 감정\n\n- 해석: 운이 좋았다.\n- 감정: 안도.\n\n"
        "### 확신과 왜곡 가능성\n\n- 확신: 높음.\n- 왜곡 가능성: 과장될 수 있음.\n"
    )
    memory = WikiDocument(
        path="memories/m1.md",
        revision=document_revision(memory_content),
        content=memory_content,
        metadata=parse_frontmatter(memory_content),
    )
    payload = {
        "distortions": [
            {
                "memory_id": "memory:m1",
                "interpretation": "사실은 내 실력이었다고 믿게 됐다.",
                "emotion": "은근한 자부심.",
            }
        ]
    }
    model = Mock()
    model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))
    )
    with patch("src.wiki.postprocess.get_model", return_value=model):
        patches = await plan_memory_distortion(
            [memory],
            "캐릭터 A는 파일을 되찾았다.",
            "character_profile:character_a",
            "test-updater",
        )
    assert len(patches) == 1
    assert patches[0].section_path == ("주관적 기억", "해석과 감정")
    assert "내 실력이었다고" in patches[0].replacement_markdown
    assert "- 해석:" in patches[0].replacement_markdown
    assert patches[0].evidence == "캐릭터 A는 파일을 되찾았다."

    # 같은 (문서, 섹션) 대상이 이미 있으면 병합에서 건너뛴다.
    pending = PendingWikiCommit(
        user_input_hash="u",
        actor_response_hash="a",
        updater_model="test",
        patches=[patches[0]],
    )
    _merge_patches(pending, [patches[0]])
    assert len(pending.patches) == 1

    needs_character_content = (
        "---\nid: character:thread_001:character_a\ntype: character\n"
        "schema_version: 1\nworld_id: demo_world\nthread_id: thread_001\n"
        "profile_id: character_profile:character_a\n"
        "visibility: [actor, updater, player]\n"
        "created_at: 2026-07-21T00:00:00+00:00\n---\n"
        "# Character A\n\n## 현재 상태\n\n### 욕구와 컨디션\n\n"
        "- Needs: hunger=0.3000; rest=0.2000; social=0.1000; "
        "fun=0.4000; safety=0.0500; libido=0.2000\n"
        "- Active pressure: none\n- Condition: stable\n\n"
        "### Personality Change Ledger\n\n"
        "- No durable personality change has occurred since the story began.\n\n"
        "### Reproductive State\n\n"
        "- Menstrual cycle: enabled\n- Contraception: none\n- Cycle day: 14\n- Pregnant: no\n"
        "- Pregnancy day: 0\n- Internal ejaculation count this cycle: 0\n"
        "- Other parent: unknown\n"
    )
    scene_content = (
        "---\nid: thread:thread_001:scene:current\ntype: scene\nschema_version: 1\n"
        "world_id: demo_world\nthread_id: thread_001\n"
        "visibility: [actor, updater, player]\n"
        "created_at: 2026-07-21T00:00:00+00:00\n---\n"
        "# 현재 장면\n\n## 현재 장면\n\n### 시각과 장소\n\n"
        "- 2026년 7월 23일 13시, 대학 도서관이다.\n"
    )
    needs_documents = [
        WikiDocument(
            path="characters/character_a.md",
            revision=document_revision(needs_character_content),
            content=needs_character_content,
            metadata=parse_frontmatter(needs_character_content),
        ),
        WikiDocument(
            path="scene/current.md",
            revision=document_revision(scene_content),
            content=scene_content,
            metadata=parse_frontmatter(scene_content),
        ),
    ]
    header = "**2026년 7월 23일 목요일 14시, 대학 도서관**"
    needs_patches = plan_needs_decay(
        needs_documents,
        header,
        "character_profile:character_a",
    )
    assert len(needs_patches) == 1
    assert "hunger=0.4980" in needs_patches[0].replacement_markdown
    assert "safety=0.0500" in needs_patches[0].replacement_markdown
    assert "- Condition: stable" in needs_patches[0].replacement_markdown
    assert "reflects accumulated time" not in needs_patches[0].replacement_markdown
    assert needs_patches[0].evidence == header
    _merge_patches(pending, needs_patches, replace_exact=True)
    assert len(pending.patches) == 2

    from src.wiki.character_postprocess import (
        plan_organic_state,
        plan_personality_drift,
    )

    relationship_content = (
        "---\nid: relationship:character-a--player\ntype: relationship\n"
        "schema_version: 1\nthread_id: thread_001\n"
        "owner: character_profile:character_a\n"
        "participants: [character_profile:character_a, character_profile:player]\n"
        "visibility: [actor, updater, player]\n"
        "created_at: 2026-07-21T00:00:00+00:00\n---\n"
        "# Relationship\n\n## Relationship Development\n\n"
        "- No durable relationship change has occurred since the story began.\n"
    )
    relationship = WikiDocument(
        path="relationships/character-a--player.md",
        revision=document_revision(relationship_content),
        content=relationship_content,
        metadata=parse_frontmatter(relationship_content),
    )
    relationship_section = parse_markdown_sections(relationship.content)[
        ("Relationship Development",)
    ]
    trigger_pending = PendingWikiCommit(
        user_input_hash="u",
        actor_response_hash="a",
        updater_model="test",
        patches=[
            SectionPatch(
                document=relationship.path,
                base_revision=relationship.revision,
                base_section_revision=document_revision(
                    relationship_section.markdown
                ),
                base_markdown=relationship_section.markdown,
                section_path=("Relationship Development",),
                replacement_markdown=(
                    "## Relationship Development\n\n"
                    "- She now treats the promise as a durable obligation."
                ),
                evidence="캐릭터 A는 약속을 반드시 지키겠다고 선언했다.",
                evidence_source="actor_response",
                confidence=1.0,
            )
        ],
    )
    drift_model = Mock()
    drift_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(
                {
                    "ledger_entry": (
                        "She has become slightly more deliberate about keeping promises."
                    )
                }
            )
        )
    )
    with patch(
        "src.wiki.character_postprocess.get_model",
        return_value=drift_model,
    ):
        drift_patches = await plan_personality_drift(
            [*needs_documents, relationship],
            "캐릭터 A는 약속을 반드시 지키겠다고 선언했다.",
            trigger_pending,
            "character_profile:character_a",
            "test-updater",
        )
    assert len(drift_patches) == 1
    assert drift_patches[0].section_path == (
        "현재 상태",
        "Personality Change Ledger",
    )
    assert "keeping promises" in drift_patches[0].replacement_markdown
    assert (
        "No durable personality change has occurred"
        not in drift_patches[0].replacement_markdown
    )

    organic_response = "캐릭터 A의 몸 안에 질내사정했다."
    with (
        patch(
            "src.wiki.character_postprocess.calculate_pregnancy_probability",
            return_value=1.0,
        ) as probability_mock,
        patch("src.wiki.character_postprocess.get_model") as contraception_model,
    ):
        organic_patches, ooc_message = await plan_organic_state(
            needs_documents,
            organic_response,
            trigger_pending,
            "character_profile:character_a",
            "character_profile:player",
            "test-updater",
        )
    assert contraception_model.call_count == 0
    assert probability_mock.call_args.kwargs["contraception"] == "none"
    assert len(organic_patches) == 1
    assert "- Contraception: none" in organic_patches[0].replacement_markdown
    assert "- Pregnant: yes" in organic_patches[0].replacement_markdown
    assert organic_patches[0].evidence == organic_response
    assert ooc_message is not None and "임신 상태" in ooc_message

    with (
        patch(
            "src.wiki.character_postprocess.calculate_pregnancy_probability",
            return_value=1.0,
        ) as condom_probability_mock,
        patch("src.wiki.character_postprocess.get_model") as condom_contraception_model,
    ):
        condom_patches, condom_ooc = await plan_organic_state(
            needs_documents,
            (
                "캐릭터 A의 몸 안에 질내사정했다.\n\n"
                "<ooc>\n- Protection: condom\n</ooc>"
            ),
            trigger_pending,
            "character_profile:character_a",
            "character_profile:player",
            "test-updater",
        )
    assert condom_contraception_model.call_count == 0
    assert condom_probability_mock.call_count == 0
    assert condom_patches == []
    assert condom_ooc is None

    with (
        patch(
            "src.wiki.character_postprocess.calculate_pregnancy_probability",
            return_value=1.0,
        ) as ooc_none_probability_mock,
        patch("src.wiki.character_postprocess.get_model") as ooc_none_contraception_model,
    ):
        ooc_none_patches, ooc_none_message = await plan_organic_state(
            needs_documents,
            (
                "안에 사정했다.\n\n"
                "<ooc>\n- Protection: none\n</ooc>"
            ),
            trigger_pending,
            "character_profile:character_a",
            "character_profile:player",
            "test-updater",
        )
    assert ooc_none_contraception_model.call_count == 0
    assert ooc_none_probability_mock.call_args.kwargs["contraception"] == "none"
    assert len(ooc_none_patches) == 1
    assert "- Pregnant: yes" in ooc_none_patches[0].replacement_markdown
    assert ooc_none_patches[0].evidence == "안에 사정했다."
    assert ooc_none_message is not None and "임신 상태" in ooc_none_message

    with (
        patch(
            "src.wiki.character_postprocess.calculate_pregnancy_probability",
            return_value=1.0,
        ) as fallback_probability_mock,
        patch("src.wiki.character_postprocess.get_model") as fallback_contraception_model,
    ):
        fallback_patches, fallback_ooc = await plan_organic_state(
            needs_documents,
            "안에 사정했다.",
            trigger_pending,
            "character_profile:character_a",
            "character_profile:player",
            "test-updater",
        )
    assert fallback_contraception_model.call_count == 0
    assert fallback_probability_mock.call_count == 0
    assert fallback_patches == []
    assert fallback_ooc is None

    with (
        patch(
            "src.wiki.character_postprocess.calculate_pregnancy_probability",
            return_value=1.0,
        ) as malformed_probability_mock,
        patch("src.wiki.character_postprocess.get_model") as malformed_contraception_model,
    ):
        malformed_patches, malformed_ooc = await plan_organic_state(
            needs_documents,
            (
                "안에 사정했다.\n\n"
                "<ooc>\n- Protection: maybe\n</ooc>"
            ),
            trigger_pending,
            "character_profile:character_a",
            "character_profile:player",
            "test-updater",
        )
    assert malformed_contraception_model.call_count == 0
    assert malformed_probability_mock.call_count == 0
    assert malformed_patches == []
    assert malformed_ooc is None

    contraception_model = Mock()
    contraception_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(
                {
                    "state_change_established": True,
                    "new_contraception": "oral",
                    "emergency_contraception_taken": False,
                    "evidence_quote": "캐릭터 A는 먹는 피임약을 계속 복용 중이라고 분명히 말했다.",
                },
                ensure_ascii=False,
            )
        )
    )
    with (
        patch(
            "src.wiki.character_postprocess.get_model",
            return_value=contraception_model,
        ),
        patch(
            "src.wiki.character_postprocess.calculate_pregnancy_probability",
            return_value=0.0,
        ) as protected_probability_mock,
    ):
        protected_patches, protected_ooc = await plan_organic_state(
            needs_documents,
            "캐릭터 A는 먹는 피임약을 계속 복용 중이라고 분명히 말했다.",
            trigger_pending,
            "character_profile:character_a",
            "character_profile:player",
            "test-updater",
        )
    assert protected_probability_mock.call_count == 0
    assert protected_ooc is None
    assert len(protected_patches) == 1
    assert "- Contraception: oral" in protected_patches[0].replacement_markdown
    assert protected_patches[0].evidence == (
        "캐릭터 A는 먹는 피임약을 계속 복용 중이라고 분명히 말했다."
    )

    passing_model = Mock()
    passing_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(
                {
                    "state_change_established": False,
                    "new_contraception": "none",
                    "emergency_contraception_taken": False,
                    "evidence_quote": "",
                },
                ensure_ascii=False,
            )
        )
    )
    with patch(
        "src.wiki.character_postprocess.get_model",
        return_value=passing_model,
    ):
        passing_patches, passing_ooc = await plan_organic_state(
            needs_documents,
            "캐릭터 A는 친구가 피임약을 먹는다는 말을 들었다.",
            trigger_pending,
            "character_profile:character_a",
            "character_profile:player",
            "test-updater",
        )
    assert passing_model.generate_content_async.await_count == 1
    assert passing_patches == []
    assert passing_ooc is None

    emergency_documents = [
        needs_documents[0].model_copy(
            update={
                "content": needs_character_content.replace(
                    "- Internal ejaculation count this cycle: 0",
                    "- Internal ejaculation count this cycle: 2",
                ),
                "revision": document_revision(
                    needs_character_content.replace(
                        "- Internal ejaculation count this cycle: 0",
                        "- Internal ejaculation count this cycle: 2",
                    )
                ),
                "metadata": parse_frontmatter(
                    needs_character_content.replace(
                        "- Internal ejaculation count this cycle: 0",
                        "- Internal ejaculation count this cycle: 2",
                    )
                ),
            }
        ),
        needs_documents[1],
    ]
    emergency_model = Mock()
    emergency_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(
                {
                    "state_change_established": False,
                    "new_contraception": "none",
                    "emergency_contraception_taken": True,
                    "evidence_quote": "캐릭터 A는 사후피임약을 바로 복용했다.",
                },
                ensure_ascii=False,
            )
        )
    )
    with (
        patch(
            "src.wiki.character_postprocess.get_model",
            return_value=emergency_model,
        ),
        patch(
            "src.wiki.character_postprocess.calculate_pregnancy_probability",
            return_value=0.0,
        ) as emergency_probability_mock,
    ):
        emergency_patches, emergency_ooc = await plan_organic_state(
            emergency_documents,
            "캐릭터 A는 사후피임약을 바로 복용했다.",
            trigger_pending,
            "character_profile:character_a",
            "character_profile:player",
            "test-updater",
        )
    assert emergency_probability_mock.call_count == 0
    assert emergency_ooc is None
    assert len(emergency_patches) == 1
    assert "- Internal ejaculation count this cycle: 0" in (
        emergency_patches[0].replacement_markdown
    )
    assert emergency_patches[0].evidence == "캐릭터 A는 사후피임약을 바로 복용했다."

    protected_payload = {
        "summary": "runtime section 침범",
        "patches": [
            {
                "document": needs_documents[0].path,
                "base_revision": needs_documents[0].revision,
                "section_path": ["현재 상태", "욕구와 컨디션"],
                "replacement_markdown": (
                    "### 욕구와 컨디션\n\n"
                    "- Needs: hunger=0.0000; rest=0.0000; social=0.0000; "
                    "fun=0.0000; safety=0.0000; libido=0.0000\n"
                    "- Active pressure: none\n- Condition: reset"
                ),
                "evidence": "캐릭터 A는 모든 욕구가 사라졌다고 말했다.",
                "evidence_source": "actor_response",
                "confidence": 0.99,
            }
        ],
        "creations": [],
    }
    await _expect_update_rejected(
        needs_documents,
        protected_payload,
        "지켜본다.",
        "캐릭터 A는 모든 욕구가 사라졌다고 말했다.",
        actor_profile_id="character_profile:character_a",
    )


async def _generate_with_one_retry(document: WikiDocument) -> PendingWikiCommit:
    """첫 응답 파싱 실패 후 두 번째 응답에서 유효한 patch를 반환합니다."""
    valid_payload = {
        "summary": "캐릭터 A 수정: ## Machine Payload\n\n```json\n 문자열 포함",
        "patches": [
            {
                "document": document.path,
                "base_revision": document.revision,
                "section_path": ["현재 상태", "신체 상태와 감정 상태"],
                "replacement_markdown": (
                    "### 신체 상태와 감정 상태\n\n"
                    "- 신체 상태: 계단을 올라 숨이 차고 피곤하다.\n"
                    "- 감정 상태: 평온"
                ),
                "evidence": "캐릭터 A는 계단을 올라 숨이 차고 피곤해졌다.",
                "evidence_source": "actor_response",
                "confidence": 0.98,
            }
        ],
    }
    model = Mock()
    model.generate_content_async = AsyncMock(side_effect=[
        SimpleNamespace(text="not-json"),
        RuntimeError("second-model-error"),
        SimpleNamespace(text=json.dumps(valid_payload, ensure_ascii=False)),
    ])
    with (
        patch("src.wiki.commit_planner.get_model", return_value=model),
        patch("src.wiki.commit_planner.asyncio.sleep", new=AsyncMock()),
    ):
        pending = await plan_pending_commit(
            documents=[document],
            user_input="생일이 지났다.",
            actor_response="캐릭터 A는 계단을 올라 숨이 차고 피곤해졌다.",
            model_name="gemini-3.1-pro-preview",
            max_attempts=3,
        )
    second_prompt = model.generate_content_async.await_args_list[1].args[0]
    third_prompt = model.generate_content_async.await_args_list[2].args[0]
    assert "## Previous Attempt Rejected" in second_prompt
    assert "No JSON structure found" in third_prompt
    assert "second-model-error" in third_prompt
    return pending


async def _check_retry_exhaustion(document: WikiDocument) -> None:
    """재시도 소진 시 예외와 각 시도의 원문 진단 자료가 남는지 검증합니다."""
    model = Mock()
    model.generate_content_async = AsyncMock(side_effect=[
        SimpleNamespace(text="not-json-1"),
        SimpleNamespace(text="not-json-2"),
    ])
    with TemporaryDirectory() as debug_directory:
        debug_root = Path(debug_directory)
        with (
            patch("src.wiki.commit_planner.get_model", return_value=model),
            patch("src.wiki.commit_planner.asyncio.sleep", new=AsyncMock()),
        ):
            try:
                await plan_pending_commit(
                    documents=[document],
                    user_input="변화 없음",
                    actor_response="평범한 하루였다.",
                    model_name="gemini-3.1-pro-preview",
                    max_attempts=2,
                    debug_root=debug_root,
                )
            except WikiCommitPlanningError:
                pass
            else:
                raise AssertionError(
                    "Updater retry exhaustion must raise WikiCommitPlanningError"
                )
        run_directories = [path for path in debug_root.iterdir() if path.is_dir()]
        assert len(run_directories) == 1
        run_dir = run_directories[0]
        assert (run_dir / "attempt_01_response.txt").read_text(
            encoding="utf-8"
        ) == "not-json-1"
        assert (run_dir / "attempt_02_response.txt").read_text(
            encoding="utf-8"
        ) == "not-json-2"
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        assert result["status"] == "failed"
        assert result["attempts"] == 2
    assert model.generate_content_async.await_count == 2


async def _expect_update_rejected(
    documents: list[WikiDocument],
    payload: dict[str, object],
    user_input: str,
    actor_response: str,
    player_profile_id: str = "",
    actor_profile_id: str = "",
) -> None:
    """정책을 위반한 구조화 Updater 응답이 commit으로 생성되지 않는지 검증합니다."""
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
                model_name="gemini-3.1-pro-preview",
                max_attempts=1,
                player_profile_id=player_profile_id,
                actor_profile_id=actor_profile_id,
            )
        except WikiCommitPlanningError:
            pass
        else:
            raise AssertionError("Updater policy violation must be rejected")


async def _check_update_policy(
    character: WikiDocument,
    scene: WikiDocument,
) -> None:
    """플레이어 출처, 정적 섹션, event-memory 결속과 Event/Memory patch 경계를 검증합니다."""
    event = WikiDocument(
        path="events/existing-outage.md",
        revision=document_revision(_EVENT_DOCUMENT),
        content=_EVENT_DOCUMENT,
        metadata=parse_frontmatter(_EVENT_DOCUMENT),
    )
    ongoing_event = WikiDocument(
        path="events/ongoing-search.md",
        revision=document_revision(_ONGOING_EVENT_DOCUMENT),
        content=_ONGOING_EVENT_DOCUMENT,
        metadata=parse_frontmatter(_ONGOING_EVENT_DOCUMENT),
    )
    memory_content = (
        "---\nid: memory:existing-outage-memory\ntype: memory\nschema_version: 1\n"
        "thread_id: thread_001\nowner: character_profile:character_a\n"
        "visibility: [actor, updater]\ncreated_at: 2026-07-21T00:00:00+00:00\n---\n"
        "# Character A Remembers the Outage\n\n## 주관적 기억\n\n### 기억하는 내용\n\n"
        "- 기억 내용: 정전이 갑자기 찾아왔다.\n\n### 해석과 감정\n\n"
        "- 해석: 누군가 일부러 전원을 끊었을지도 모른다.\n- 감정: 불안하다.\n\n"
        "### 확신과 왜곡 가능성\n\n- 확신: 정전이 있었다는 사실에는 높음.\n"
        "- 왜곡 가능성: 시간이 지나면 원인을 더 음모처럼 기억할 수 있다.\n"
    )
    memory = WikiDocument(
        path="memories/existing-outage-memory.md",
        revision=document_revision(memory_content),
        content=memory_content,
        metadata=parse_frontmatter(memory_content),
    )
    npc_content = (
        _CHARACTER_DOCUMENT
        .replace("id: character:character_a", "id: character:npc")
        .replace(
            "profile_id: character_profile:character_a",
            "profile_id: character_profile:npc",
        )
        .replace("# 캐릭터 A", "# NPC")
    )
    npc = WikiDocument(
        path="characters/npc.md",
        revision=document_revision(npc_content),
        content=npc_content,
        metadata=parse_frontmatter(npc_content),
    )
    relationship = WikiDocument(
        path="relationships/npc--character_a.md",
        revision=document_revision(_RELATIONSHIP_DOCUMENT),
        content=_RELATIONSHIP_DOCUMENT,
        metadata=parse_frontmatter(_RELATIONSHIP_DOCUMENT),
    )
    static_patch = {
        "summary": "정적 프로필 변경",
        "patches": [{
            "document": character.path,
            "base_revision": character.revision,
            "section_path": ["기본 신상", "나이와 생년월일"],
            "replacement_markdown": "### 나이와 생년월일\n\n- 나이: 24세",
            "evidence": "내 나이는 스물넷이다.",
            "evidence_source": "player_input",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, scene],
        static_patch,
        "내 나이는 스물넷이다.",
        "캐릭터 A가 고개를 끄덕였다.",
        player_profile_id="character_profile:character_a",
    )

    event_without_memory_payload = {
        "summary": "기억 없는 사건 생성",
        "patches": [],
        "creations": [{
            "document_type": "event",
            "document_id": "event:library-confession",
            "title": "Library Confession",
            "occurred_at": "2026-07-23 13:05",
            "location": "대학 도서관 복도",
            "participants": ["캐릭터 A", "NPC"],
            "witnesses": [],
            "facts": ["NPC confessed a hidden feeling to Character A in the library hallway."],
            "direct_results": ["The confession changes how both participants must address the relationship."],
            "lasting_effects": ["The confession becomes a durable reference point between them."],
            "evidence": "NPC는 도서관 복도에서 캐릭터 A에게 오래 숨겨 온 마음을 고백했다.",
            "evidence_source": "actor_response",
            "confidence": 0.94,
        }],
    }
    event_without_memory_model = Mock()
    event_without_memory_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(event_without_memory_payload, ensure_ascii=False)
        )
    )
    with patch(
        "src.wiki.commit_planner.get_model",
        return_value=event_without_memory_model,
    ):
        try:
            await plan_pending_commit(
                documents=[character, scene],
                user_input="상황을 지켜본다.",
                actor_response="NPC는 도서관 복도에서 캐릭터 A에게 오래 숨겨 온 마음을 고백했다.",
                model_name="test-updater",
                max_attempts=1,
                actor_profile_id="character_profile:character_a",
            )
        except WikiCommitPlanningError as exc:
            error_text = str(exc)
            assert "event:library-confession" in error_text
            assert (
                "Each created Event requires at least one Memory created in the same "
                "response with `related_event_id` equal to the Event `document_id`."
            ) in error_text
        else:
            raise AssertionError(
                "Created event without matching memory must be rejected"
            )

    matching_memory_pending = await _generate_event_creation(character, scene)
    assert len(matching_memory_pending.creations) == 2
    assert {
        creation.document for creation in matching_memory_pending.creations
    } == {
        "events/library-power-outage.md",
        "memories/character-a-remembers-outage.md",
    }

    no_event_payload = {
        "summary": "변경 없음",
        "patches": [],
        "creations": [],
    }
    no_event_model = Mock()
    no_event_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(no_event_payload, ensure_ascii=False))
    )
    with patch("src.wiki.commit_planner.get_model", return_value=no_event_model):
        no_event_pending = await plan_pending_commit(
            documents=[character, scene],
            user_input="상황을 지켜본다.",
            actor_response="캐릭터 A가 잠시 숨을 골랐다.",
            model_name="test-updater",
            max_attempts=1,
            actor_profile_id="character_profile:character_a",
        )
    assert no_event_pending.creations == []
    assert no_event_pending.patches == []

    actor_evidence_for_player_memory = {
        "summary": "Actor 근거로 플레이어 기억 생성",
        "patches": [],
        "creations": [{
            "document_type": "memory",
            "document_id": "memory:player-remembers-outage",
            "title": "Player Remembers the Outage",
            "owner": "character_profile:character_a",
            "related_event_id": "event:existing-outage",
            "formation_trigger": "The lights went out.",
            "formed_at": "2026-07-23 13:00",
            "location": "대학 도서관",
            "remembered_content": "The player remembers the lights going out.",
            "interpretation": "The player thinks the outage was suspicious.",
            "emotion": "Unease.",
            "certainty": "High about the darkness.",
            "distortion_risk": "The cause remains uncertain.",
            "evidence": "캐릭터 A는 정전을 수상하게 기억했다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    await _expect_update_rejected(
        [character, scene, event],
        actor_evidence_for_player_memory,
        "정전을 떠올린다.",
        "캐릭터 A는 정전을 수상하게 기억했다.",
        player_profile_id="character_profile:character_a",
    )

    event_progress_patch_payload = {
        "summary": "기존 event 진행 상태 갱신",
        "patches": [{
            "document": ongoing_event.path,
            "base_revision": ongoing_event.revision,
            "section_path": ["진행 상태"],
            "replacement_markdown": (
                "## 진행 상태\n\n"
                "- 상태: concluded\n"
                "- 진행 경과: The participants found the missing files and ended the search.\n"
                "- 종료 시각: 2026-07-23 13:18"
            ),
            "evidence": "참가자들은 잃어버린 파일을 찾아 수색을 끝냈다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    event_progress_patch_model = Mock()
    event_progress_patch_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(event_progress_patch_payload, ensure_ascii=False)
        )
    )
    with patch(
        "src.wiki.commit_planner.get_model",
        return_value=event_progress_patch_model,
    ):
        event_progress_pending = await plan_pending_commit(
            documents=[character, scene, ongoing_event],
            user_input="기록을 본다.",
            actor_response="참가자들은 잃어버린 파일을 찾아 수색을 끝냈다.",
            model_name="test-updater",
            max_attempts=1,
        )
    assert event_progress_pending.patches[0].document == "events/ongoing-search.md"
    assert event_progress_pending.patches[0].section_path == ("진행 상태",)

    event_identity_patch_payload = {
        "summary": "기존 event 발생 정보 수정",
        "patches": [{
            "document": event.path,
            "base_revision": event.revision,
            "section_path": ["발생 정보", "시각과 장소"],
            "replacement_markdown": (
                "### 시각과 장소\n\n"
                "- 시각: 2026-07-23 14:00\n"
                "- 장소: 대학 도서관"
            ),
            "evidence": "도서관 정전은 오후 두 시였다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    event_identity_patch_model = Mock()
    event_identity_patch_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(event_identity_patch_payload, ensure_ascii=False)
        )
    )
    with patch(
        "src.wiki.commit_planner.get_model",
        return_value=event_identity_patch_model,
    ):
        try:
            await plan_pending_commit(
                documents=[character, scene, event],
                user_input="기록을 본다.",
                actor_response="도서관 정전은 오후 두 시였다.",
                model_name="test-updater",
                max_attempts=1,
            )
        except WikiCommitPlanningError as exc:
            assert "events/existing-outage.md" in str(exc)
            assert "event updates may modify only the '진행 상태' section" in str(exc)
        else:
            raise AssertionError("Event identity section patch must be rejected")

    event_reopen_patch_payload = {
        "summary": "종결된 event 재개",
        "patches": [{
            "document": event.path,
            "base_revision": event.revision,
            "section_path": ["진행 상태"],
            "replacement_markdown": (
                "## 진행 상태\n\n"
                "- 상태: ongoing\n"
                "- 진행 경과: The outage unexpectedly resumed.\n"
                "- 종료 시각:"
            ),
            "evidence": "정전이 다시 이어지는 듯했다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    event_reopen_patch_model = Mock()
    event_reopen_patch_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(event_reopen_patch_payload, ensure_ascii=False)
        )
    )
    with patch(
        "src.wiki.commit_planner.get_model",
        return_value=event_reopen_patch_model,
    ):
        try:
            await plan_pending_commit(
                documents=[character, scene, event],
                user_input="기록을 본다.",
                actor_response="정전이 다시 이어지는 듯했다.",
                model_name="test-updater",
                max_attempts=1,
            )
        except WikiCommitPlanningError as exc:
            assert "events/existing-outage.md" in str(exc)
            assert "Event progress cannot reopen a concluded record" in str(exc)
        else:
            raise AssertionError("Concluded event must not be reopened")

    event_missing_status_patch_payload = {
        "summary": "event 상태 줄 누락",
        "patches": [{
            "document": event.path,
            "base_revision": event.revision,
            "section_path": ["진행 상태"],
            "replacement_markdown": (
                "## 진행 상태\n\n"
                "- 진행 경과: The outage was summarized without a status line.\n"
                "- 종료 시각: 2026-07-23 13:00"
            ),
            "evidence": "정전 기록을 다시 요약했다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    event_missing_status_patch_model = Mock()
    event_missing_status_patch_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(event_missing_status_patch_payload, ensure_ascii=False)
        )
    )
    with patch(
        "src.wiki.commit_planner.get_model",
        return_value=event_missing_status_patch_model,
    ):
        try:
            await plan_pending_commit(
                documents=[character, scene, event],
                user_input="기록을 본다.",
                actor_response="정전 기록을 다시 요약했다.",
                model_name="test-updater",
                max_attempts=1,
            )
        except WikiCommitPlanningError as exc:
            assert "events/existing-outage.md" in str(exc)
            assert (
                "event progress must include exactly one '- 상태:' line with "
                "'ongoing' or 'concluded'"
            ) in str(exc)
        else:
            raise AssertionError("Event progress without status line must be rejected")

    event_invalid_status_patch_payload = {
        "summary": "event 상태 값 오류",
        "patches": [{
            "document": event.path,
            "base_revision": event.revision,
            "section_path": ["진행 상태"],
            "replacement_markdown": (
                "## 진행 상태\n\n"
                "- 상태: 진행중\n"
                "- 진행 경과: The outage was described with an invalid status value.\n"
                "- 종료 시각: 2026-07-23 13:00"
            ),
            "evidence": "정전 기록 상태를 잘못 적었다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    event_invalid_status_patch_model = Mock()
    event_invalid_status_patch_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(event_invalid_status_patch_payload, ensure_ascii=False)
        )
    )
    with patch(
        "src.wiki.commit_planner.get_model",
        return_value=event_invalid_status_patch_model,
    ):
        try:
            await plan_pending_commit(
                documents=[character, scene, event],
                user_input="기록을 본다.",
                actor_response="정전 기록 상태를 잘못 적었다.",
                model_name="test-updater",
                max_attempts=1,
            )
        except WikiCommitPlanningError as exc:
            assert "events/existing-outage.md" in str(exc)
            assert (
                "event progress must include exactly one '- 상태:' line with "
                "'ongoing' or 'concluded'"
            ) in str(exc)
        else:
            raise AssertionError("Event progress with invalid status must be rejected")

    memory_patch_payload = {
        "summary": "기존 memory 문서 수정",
        "patches": [{
            "document": memory.path,
            "base_revision": memory.revision,
            "section_path": ["주관적 기억", "해석과 감정"],
            "replacement_markdown": (
                "### 해석과 감정\n\n"
                "- 해석: 이제는 누군가 일부러 전원을 끊었다고 거의 확신한다.\n"
                "- 감정: 더 강한 불안과 의심."
            ),
            "evidence": "정전 기억이 점점 더 수상하게 느껴진다.",
            "evidence_source": "player_input",
            "confidence": 0.9,
        }],
    }
    memory_patch_model = Mock()
    memory_patch_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(memory_patch_payload, ensure_ascii=False)
        )
    )
    with patch("src.wiki.commit_planner.get_model", return_value=memory_patch_model):
        try:
            await plan_pending_commit(
                documents=[character, scene, event, memory],
                user_input="정전 기억이 점점 더 수상하게 느껴진다.",
                actor_response="캐릭터 A는 잠시 침묵했다.",
                model_name="test-updater",
                max_attempts=1,
                player_profile_id="character_profile:character_a",
            )
        except WikiCommitPlanningError as exc:
            assert "memories/existing-outage-memory.md" in str(exc)
            assert (
                "Gameplay updater cannot patch memory documents; gated memory distortion "
                "owns their mutable section"
            ) in str(exc)
        else:
            raise AssertionError("Memory document patch must be rejected")

    player_from_actor_patch = {
        "summary": "Actor가 만든 플레이어 상태",
        "patches": [{
            "document": character.path,
            "base_revision": character.revision,
            "section_path": ["현재 상태", "신체 상태와 감정 상태"],
            "replacement_markdown": (
                "### 신체 상태와 감정 상태\n\n"
                "- 신체 상태: 안정\n"
                "- 감정 상태: 불안"
            ),
            "evidence": "캐릭터 A는 불안해졌다.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, scene],
        player_from_actor_patch,
        "가만히 있는다.",
        "캐릭터 A는 불안해졌다.",
        player_profile_id="character_profile:character_a",
    )

    active_location_patch = {
        "summary": "활성 인물 위치 중복",
        "patches": [{
            "document": character.path,
            "base_revision": character.revision,
            "section_path": ["현재 상태", "현재 위치와 활동"],
            "replacement_markdown": (
                "### 현재 위치와 활동\n\n"
                "- 위치: 현재 장면 참조\n"
                "- 활동: 도서관에서 걸어 나간다."
            ),
            "evidence": "캐릭터 A는 도서관에서 걸어 나갔다.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, scene],
        active_location_patch,
        "밖으로 가자.",
        "캐릭터 A는 도서관에서 걸어 나갔다.",
        actor_profile_id="character_profile:character_a",
    )

    scene_leaf_patch = {
        "summary": "장면 일부만 변경",
        "patches": [{
            "document": scene.path,
            "base_revision": scene.revision,
            "section_path": ["시작 기준", "시작 시각과 장소"],
            "replacement_markdown": (
                "### 시작 시각과 장소\n\n"
                "- 2026년 7월 23일 13시 5분, 대학 도서관 밖이다."
            ),
            "evidence": "두 사람은 13시 5분에 도서관 밖으로 나왔다.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, scene],
        scene_leaf_patch,
        "밖으로 가자.",
        "두 사람은 13시 5분에 도서관 밖으로 나왔다.",
    )

    actor_player_scene_patch = {
        "summary": "Actor가 만든 플레이어 공동 이동",
        "patches": [{
            "document": scene.path,
            "base_revision": scene.revision,
            "section_path": ["현재 장면"],
            "replacement_markdown": (
                "## 현재 장면\n\n"
                "### 시작 시각과 장소\n\n"
                "- 2026년 7월 23일 13시 5분, 대학 도서관 밖이다.\n\n"
                "### 인물 위치와 현재 상태\n\n"
                "- 두 사람은 함께 도서관 밖으로 걸어 나왔다.\n\n"
                "### 당장의 계기\n\n"
                "- 제출 전에 인쇄할 곳을 찾는다."
            ),
            "evidence": "두 사람은 함께 도서관 밖으로 걸어 나왔다.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, scene],
        actor_player_scene_patch,
        "밖으로 가자.",
        "두 사람은 함께 도서관 밖으로 걸어 나왔다.",
        player_profile_id="character_profile:character_a",
    )

    # Actor 결과와 Player Input이 함께 장면을 바꾸면 전용 exact quote로 플레이어 쪽을 승인한다.
    mixed_scene_patch = {
        "summary": "플레이어가 명시한 관찰 행동과 NPC 반응을 함께 반영",
        "patches": [{
            "document": scene.path,
            "base_revision": scene.revision,
            "section_path": ["현재 장면"],
            "replacement_markdown": (
                "## 현재 장면\n\n"
                "### 시작 시각과 장소\n\n"
                "- 2026년 7월 23일 13시 5분, 대학 도서관 밖이다.\n\n"
                "### 인물 위치와 현재 상태\n\n"
                "- 캐릭터 A is looking out the window.\n"
                "- NPC는 캐릭터 A의 시선을 따라 창밖을 바라본다.\n\n"
                "### 당장의 공기\n\n"
                "- 둘은 맑은 날씨에 관해 짧게 이야기한다."
            ),
            "evidence": "NPC는 캐릭터 A의 시선을 따라 창밖을 바라봤다.",
            "evidence_source": "actor_response",
            "player_evidence": "나는 창밖을 보며 날씨가 좋다고 말했다.",
            "confidence": 0.95,
        }],
    }
    mixed_model = Mock()
    mixed_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(mixed_scene_patch, ensure_ascii=False)
        )
    )
    with patch("src.wiki.commit_planner.get_model", return_value=mixed_model):
        mixed_pending = await plan_pending_commit(
            documents=[character, scene],
            user_input="나는 창밖을 보며 날씨가 좋다고 말했다.",
            actor_response="NPC는 캐릭터 A의 시선을 따라 창밖을 바라봤다.",
            model_name="gemini-3.1-pro-preview",
            max_attempts=1,
            player_profile_id="character_profile:character_a",
        )
    assert len(mixed_pending.patches) == 1
    assert (
        mixed_pending.patches[0].player_evidence
        == "나는 창밖을 보며 날씨가 좋다고 말했다."
    )

    non_quote_patch = {
        "summary": "원문에 없는 근거",
        "patches": [{
            "document": character.path,
            "base_revision": character.revision,
            "section_path": ["현재 상태", "신체 상태와 감정 상태"],
            "replacement_markdown": (
                "### 신체 상태와 감정 상태\n\n"
                "- 신체 상태: 안정\n"
                "- 감정 상태: 즐거움"
            ),
            "evidence": "캐릭터가 즐거워졌다는 장면 묘사.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, scene],
        non_quote_patch,
        "웃는다.",
        "캐릭터 A는 작게 웃었다.",
    )

    player_relationship_claim = {
        "summary": "Actor가 플레이어 감정을 확정",
        "patches": [{
            "document": relationship.path,
            "base_revision": relationship.revision,
            "section_path": ["Relationship Development"],
            "replacement_markdown": (
                "## Relationship Development\n\n"
                "### Accepted Durable Changes\n\n"
                "- 캐릭터 A now loves NPC."
            ),
            "evidence": "캐릭터 A는 NPC를 사랑하게 되었다.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, npc, scene, relationship],
        player_relationship_claim,
        "NPC를 바라본다.",
        "캐릭터 A는 NPC를 사랑하게 되었다.",
        player_profile_id="character_profile:character_a",
        actor_profile_id="character_profile:npc",
    )

    player_sourced_relationship = {
        "summary": "플레이어 근거로 Actor 관계 변경",
        "patches": [{
            "document": relationship.path,
            "base_revision": relationship.revision,
            "section_path": ["Relationship Development"],
            "replacement_markdown": (
                "## Relationship Development\n\n"
                "### Accepted Durable Changes\n\n"
                "- NPC now trusts the player with the hidden key."
            ),
            "evidence": "나는 NPC에게 숨겨 둔 열쇠를 맡긴다.",
            "evidence_source": "player_input",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, npc, scene, relationship],
        player_sourced_relationship,
        "나는 NPC에게 숨겨 둔 열쇠를 맡긴다.",
        "NPC는 열쇠를 받아 들었다.",
        player_profile_id="character_profile:character_a",
        actor_profile_id="character_profile:npc",
    )

    valid_relationship_patch = {
        "summary": "Actor 관계 변화 누적",
        "patches": [{
            "document": relationship.path,
            "base_revision": relationship.revision,
            "section_path": ["Relationship Development"],
            "replacement_markdown": (
                "## Relationship Development\n\n"
                "### Accepted Durable Changes\n\n"
                "- NPC now trusts the player with the hidden key."
            ),
            "evidence": "NPC는 숨겨 둔 열쇠를 맡기며 처음으로 전적인 신뢰를 보였다.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    relationship_model = Mock()
    relationship_model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(valid_relationship_patch, ensure_ascii=False)
        )
    )
    with patch("src.wiki.commit_planner.get_model", return_value=relationship_model):
        relationship_pending = await plan_pending_commit(
            documents=[character, npc, scene, relationship],
            user_input="열쇠를 보관하겠다고 말한다.",
            actor_response="NPC는 숨겨 둔 열쇠를 맡기며 처음으로 전적인 신뢰를 보였다.",
            model_name="gemini-3.1-pro-preview",
            max_attempts=1,
            player_profile_id="character_profile:character_a",
            actor_profile_id="character_profile:npc",
        )
    assert relationship_pending.patches[0].section_path == (
        "Relationship Development",
    )

    established_content = _RELATIONSHIP_DOCUMENT.replace(
        "- No durable relationship change has occurred since the story began.",
        "- NPC now trusts the player with the hidden key.",
    )
    established_relationship = WikiDocument(
        path=relationship.path,
        revision=document_revision(established_content),
        content=established_content,
        metadata=parse_frontmatter(established_content),
    )
    destructive_relationship_patch = {
        "summary": "기존 관계 기록 삭제",
        "patches": [{
            "document": established_relationship.path,
            "base_revision": established_relationship.revision,
            "section_path": ["Relationship Development"],
            "replacement_markdown": (
                "## Relationship Development\n\n"
                "### Accepted Durable Changes\n\n"
                "- NPC now relies on the player during emergencies."
            ),
            "evidence": "NPC는 위기 상황에서 플레이어에게 의지하기로 했다.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, npc, scene, established_relationship],
        destructive_relationship_patch,
        "도울 준비를 한다.",
        "NPC는 위기 상황에서 플레이어에게 의지하기로 했다.",
        player_profile_id="character_profile:character_a",
        actor_profile_id="character_profile:npc",
    )

    valid_scene_patch = {
        "summary": "현재 장면 전체 갱신",
        "patches": [{
            "document": scene.path,
            "base_revision": scene.revision,
            "section_path": ["시작 기준"],
            "replacement_markdown": (
                "## 시작 기준\n\n"
                "### 시작 시각과 장소\n\n"
                "- 2026년 7월 23일 13시 5분, 대학 도서관이다.\n\n"
                "### 인물 위치와 현재 상태\n\n"
                "- 캐릭터 A는 창가 책상에 앉아 있다.\n"
                "- NPC는 캐릭터 A에게 인쇄소에 함께 가자고 제안했다.\n\n"
                "### 당장의 계기\n\n"
                "- 제출 전에 인쇄할 곳을 찾는다."
            ),
            "evidence": "13시 5분, NPC는 캐릭터 A에게 인쇄소에 함께 가자고 제안했다.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    model = Mock()
    model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(
            text=json.dumps(valid_scene_patch, ensure_ascii=False)
        )
    )
    with patch("src.wiki.commit_planner.get_model", return_value=model):
        pending = await plan_pending_commit(
            documents=[character, scene],
            user_input="밖으로 가자.",
            actor_response="13시 5분, NPC는 캐릭터 A에게 인쇄소에 함께 가자고 제안했다.",
            model_name="gemini-3.1-pro-preview",
            max_attempts=1,
            player_profile_id="character_profile:character_a",
        )
    assert len(pending.patches) == 1
    assert pending.patches[0].section_path == ("시작 기준",)


def _check_recall() -> None:
    """예산 초과 시 최근성·구조 관련성으로 누적 문서를 축소하는지 검증합니다."""
    from src.wiki import estimate_recall_tokens, select_recall_documents

    def _make(path: str, content: str) -> WikiDocument:
        """진단용 임시 WikiDocument를 만듭니다."""
        return WikiDocument(
            path=path,
            revision=document_revision(content),
            content=content,
            metadata=parse_frontmatter(content),
        )

    scene = _make("scene/current.md", _SCENE_DOCUMENT)
    events = [
        _make(
            f"events/e{index}.md",
            (
                f"---\nid: event:e{index}\ntype: event\nschema_version: 1\n"
                "thread_id: thread_001\nvisibility: [actor, updater, player]\n"
                f"created_at: 2026-07-2{index}T00:00:00+00:00\n---\n# Event {index}\n"
            ),
        )
        for index in range(8)
    ]
    documents = [scene, *events]

    # 예산 이하이면 전체를 그대로 반환한다(짧은 thread 무변경).
    assert select_recall_documents(documents, set(), "", budget=100) == documents

    # 예산 초과 시 누적 문서만 최신 3개로 축소하고 scene은 항상 포함한다.
    selected = select_recall_documents(documents, set(), "", budget=3)
    assert scene in selected
    kept_events = [d for d in selected if d.metadata.type == "event"]
    assert len(kept_events) == 3
    kept_ids = {d.metadata.id for d in kept_events}
    assert "event:e7" in kept_ids and "event:e0" not in kept_ids

    # 활성 owner의 오래된 memory가 구조 관련성으로 최신 event를 이긴다.
    memory = _make(
        "memories/m-old.md",
        (
            "---\nid: memory:m-old\ntype: memory\nschema_version: 1\n"
            "thread_id: thread_001\nowner: character_profile:character_a\n"
            "visibility: [actor, updater]\n"
            "created_at: 2026-07-19T00:00:00+00:00\n---\n# Old Memory\n"
        ),
    )
    with_memory = [scene, memory, *events]
    single = select_recall_documents(
        with_memory,
        {"character_profile:character_a"},
        "",
        budget=1,
    )
    accumulating = [
        d for d in single if d.metadata.type in {"event", "memory"}
    ]
    assert len(accumulating) == 1
    assert accumulating[0].metadata.type == "memory"

    # 문서 수가 적어도 누적 문서의 추정 token 합이 넘으면 같은 순위 규칙으로 축소합니다.
    oversized = _make(
        "events/oversized.md",
        (
            "---\nid: event:oversized\ntype: event\nschema_version: 1\n"
            "thread_id: thread_001\nvisibility: [actor, updater, player]\n"
            "created_at: 2026-07-01T00:00:00+00:00\n---\n# Oversized\n"
            + ("long context " * 500)
        ),
    )
    newest = events[-1]
    newest_budget = estimate_recall_tokens(newest)
    token_limited = select_recall_documents(
        [scene, oversized, newest],
        set(),
        "",
        budget=100,
        token_budget=newest_budget,
    )
    assert scene in token_limited
    assert newest in token_limited
    assert oversized not in token_limited

    # 음수 token 예산은 기존 문서 수 전용 동작을 유지합니다.
    assert select_recall_documents(
        documents,
        set(),
        "",
        budget=100,
        token_budget=-1,
    ) == documents


def _check_migrations() -> None:
    """schema_version 계약: 현재 버전은 무변경, 미래 버전은 거부함을 검증합니다."""
    from src.wiki import (
        CURRENT_SCHEMA_VERSION,
        WikiMigrationError,
        migrate_document_content,
    )
    current = (
        "---\nid: event:mig\ntype: event\n"
        f"schema_version: {CURRENT_SCHEMA_VERSION}\n"
        "thread_id: thread_001\nvisibility: [actor, updater, player]\n"
        "created_at: 2026-07-21T00:00:00+00:00\n---\n# Mig\n"
    )
    assert migrate_document_content(current) == current
    future = current.replace(
        f"schema_version: {CURRENT_SCHEMA_VERSION}",
        f"schema_version: {CURRENT_SCHEMA_VERSION + 1}",
    )
    try:
        migrate_document_content(future)
    except WikiMigrationError:
        pass
    else:
        raise AssertionError("Future schema_version must be rejected")


def _check_diagnostics(vault_root: Path) -> None:
    """중복 문서 ID와 잘못된 frontmatter를 vault 진단이 잡는지 검증합니다."""
    healthy_codes = {
        diagnostic.code
        for diagnostic in diagnose_wiki_scope(vault_root, "thread_001", "demo_world")
    }
    assert "duplicate_id" not in healthy_codes
    assert "frontmatter" not in healthy_codes

    events = vault_root / "threads" / "thread_001" / "events"
    events.mkdir(parents=True, exist_ok=True)
    duplicate_body = (
        "---\nid: event:dup\ntype: event\nschema_version: 1\n"
        "thread_id: thread_001\nvisibility: [actor, updater, player]\n"
        "created_at: 2026-07-21T00:00:00+00:00\n---\n# Dup\n"
    )
    (events / "a.md").write_text(duplicate_body, encoding="utf-8")
    (events / "b.md").write_text(duplicate_body, encoding="utf-8")
    (events / "broken.md").write_text("---\nnot: valid\n---\n# Broken\n", encoding="utf-8")
    codes = {
        diagnostic.code
        for diagnostic in diagnose_wiki_scope(vault_root, "thread_001", "demo_world")
    }
    assert "duplicate_id" in codes, codes
    assert "frontmatter" in codes, codes
    for name in ("a.md", "b.md", "broken.md"):
        (events / name).unlink()


def _check_explorer(vault_root: Path) -> None:
    """Explorer 문서 목록이 world/thread 문서를 종류와 함께 나열하는지 검증합니다."""
    from src.wiki import list_wiki_documents
    summaries = list_wiki_documents(vault_root, "thread_001", "demo_world")
    types = {summary.type for summary in summaries}
    scopes = {summary.scope for summary in summaries}
    assert "world" in types
    assert "scene" in types
    assert scopes == {"world", "thread"}
    assert all(summary.id and summary.title for summary in summaries)


def _check_scaffolds(root: Path) -> None:
    """표준 world/thread 구조, 템플릿 제목, frontmatter 안전성을 검증합니다."""
    vault_root = root / "scaffold"
    world = scaffold_world(vault_root, "demo_world", "데모 월드")
    assert {document.path for document in world.documents} == {"world.md", "prose.md"}
    assert (world.root / "characters").is_dir()
    assert (world.root / "organizations").is_dir()

    world_store = WikiStore(world.root)
    world_document = world_store.read_document("world.md")
    assert world_document.metadata is not None
    assert world_document.metadata.id == "world:demo_world"
    assert world_document.metadata.type == "world"
    assert world_document.metadata.schema_version == 1
    assert world_document.metadata.visibility == ["actor", "updater", "player"]

    block_scalar = parse_frontmatter(
        "---\ndescription: |\n  ---\nid: world:block\ntype: world\n"
        "schema_version: 1\nvisibility: [player]\n"
        "created_at: 2026-07-21T00:00:00+00:00\n---\n# Block\n"
    )
    assert block_scalar is not None and block_scalar.id == "world:block"
    assert block_scalar.model_extra["description"] == "---\n"
    try:
        parse_frontmatter(
            "---\nid: character:first\nid: character:second\ntype: character\n"
            "schema_version: 1\nvisibility: [player]\n---\n# Duplicate\n"
        )
    except WikiFrontmatterError:
        pass
    else:
        raise AssertionError("Duplicate frontmatter keys must be rejected")
    try:
        parse_frontmatter("---\nfoo: bar\n---\n# Incomplete\n")
    except WikiFrontmatterError:
        pass
    else:
        raise AssertionError("Frontmatter must include the common metadata contract")
    for invalid_contract in (
        "---\nid: event:bad\ntype: event\nschema_version: true\n"
        "visibility: [player]\ncreated_at: 2026-07-21T00:00:00+00:00\n"
        "thread_id: thread_001\n---\n# Bad schema\n",
        "---\nid: character:wrong\ntype: event\nschema_version: 1\n"
        "visibility: [player]\ncreated_at: 2026-07-21T00:00:00+00:00\n"
        "thread_id: thread_001\n---\n# Bad namespace\n",
        "---\nid: event:bad_scope\ntype: event\nschema_version: 1\n"
        "visibility: [player]\ncreated_at: 2026-07-21T00:00:00+00:00\n"
        "thread_id: [wrong]\n---\n# Bad scope\n",
        "---\nid: world:other:prose\ntype: prose\nschema_version: 1\n"
        "visibility: [player]\ncreated_at: 2026-07-21T00:00:00+00:00\n"
        "world_id: demo_world\n---\n# Wrong world\n",
        "---\nid: thread:other:scene:current\ntype: scene\nschema_version: 1\n"
        "visibility: [player]\ncreated_at: 2026-07-21T00:00:00+00:00\n"
        "world_id: demo_world\nthread_id: thread_001\n---\n# Wrong thread\n",
    ):
        try:
            parse_frontmatter(invalid_contract)
        except WikiFrontmatterError:
            pass
        else:
            raise AssertionError("Invalid type-specific metadata must be rejected")

    orphan_root = root / "orphan"
    try:
        scaffold_thread(orphan_root, "orphan_thread", "missing_world", "고아")
    except WikiScaffoldError:
        pass
    else:
        raise AssertionError("Thread scaffold must reject a missing world")
    assert not (orphan_root / "worlds" / "missing_world").exists()

    thread = scaffold_thread(vault_root, "thread_001", "demo_world", "첫 번째 이야기")
    assert {document.path for document in thread.documents} == {
        "thread.md",
        "scene/current.md",
    }
    assert (thread.root / "memories").is_dir()
    assert (thread.root / "commits").is_dir()
    thread_store = WikiStore(thread.root)
    scene = thread_store.read_document("scene/current.md")
    assert scene.metadata is not None
    assert scene.metadata.id == "thread:thread_001:scene:current"
    assert scene.metadata.type == "scene"
    assert scene.metadata.world_id == "demo_world"
    assert ("시공간", "현재 시각과 경과 시간") in parse_markdown_sections(
        scene.content
    )

    all_values = {
        "DOCUMENT_ID": "character:sample",
        "WORLD_ID": "demo_world",
        "THREAD_ID": "thread_001",
        "OWNER_ID": "character:owner",
        "PARTICIPANT_A_ID": "character:a",
        "PARTICIPANT_B_ID": "character:b",
        "PROFILE_ID": "character_profile:sample",
        "TITLE": "예제 문서",
        "DISPLAY_NAME": "데모 월드",
        "SCENE_TYPE": "intimate",
        "DESCRIPTION": "Adult physical intimacy with world-specific constraints.",
        "CREATED_AT": "2026-07-21T00:00:00+00:00",
    }
    template_cases = {
        "character.md": ("character", ("DOCUMENT_ID", "WORLD_ID", "THREAD_ID", "PROFILE_ID", "TITLE", "CREATED_AT"), "thread_id"),
        "character_profile.md": ("character_profile", ("DOCUMENT_ID", "WORLD_ID", "TITLE", "CREATED_AT"), "world_id"),
        "event.md": ("event", ("DOCUMENT_ID", "THREAD_ID", "TITLE", "CREATED_AT"), "thread_id"),
        "goal.md": ("goal", ("DOCUMENT_ID", "THREAD_ID", "OWNER_ID", "TITLE", "CREATED_AT"), "thread_id"),
        "item.md": ("item", ("DOCUMENT_ID", "THREAD_ID", "OWNER_ID", "TITLE", "CREATED_AT"), "thread_id"),
        "location.md": ("location", ("DOCUMENT_ID", "WORLD_ID", "TITLE", "CREATED_AT"), "world_id"),
        "memory.md": ("memory", ("DOCUMENT_ID", "THREAD_ID", "OWNER_ID", "TITLE", "CREATED_AT"), "thread_id"),
        "organization.md": ("organization", ("DOCUMENT_ID", "WORLD_ID", "TITLE", "CREATED_AT"), "world_id"),
        "relationship.md": ("relationship", ("DOCUMENT_ID", "THREAD_ID", "OWNER_ID", "PARTICIPANT_A_ID", "PARTICIPANT_B_ID", "TITLE", "CREATED_AT"), "thread_id"),
        "prose.md": ("prose", ("DOCUMENT_ID", "WORLD_ID", "DISPLAY_NAME", "CREATED_AT"), "world_id"),
        "scenario.md": ("scenario", ("DOCUMENT_ID", "WORLD_ID", "CREATED_AT"), "world_id"),
        "scenario_opening_scene.md": ("scenario", ("DOCUMENT_ID", "WORLD_ID", "CREATED_AT"), "world_id"),
        "scenario_start_state.md": ("scenario", ("DOCUMENT_ID", "WORLD_ID", "CREATED_AT"), "world_id"),
        "scene.md": ("scene", ("DOCUMENT_ID", "THREAD_ID", "WORLD_ID", "TITLE", "CREATED_AT"), "thread_id"),
        "scene_prompt.md": ("scene_prompt", ("DOCUMENT_ID", "WORLD_ID", "SCENE_TYPE", "DESCRIPTION", "TITLE", "CREATED_AT"), "world_id"),
        "secret.md": ("secret", ("DOCUMENT_ID", "THREAD_ID", "OWNER_ID", "TITLE", "CREATED_AT"), "thread_id"),
        "thread.md": ("thread", ("DOCUMENT_ID", "WORLD_ID", "TITLE", "CREATED_AT"), "world_id"),
        "world.md": ("world", ("DOCUMENT_ID", "DISPLAY_NAME", "CREATED_AT"), None),
    }

    def render_case(template_name: str) -> str:
        """명시된 template별 입력 key만 사용해 문서를 렌더링합니다."""
        expected_type, keys, _scope = template_cases[template_name]
        document_ids = {
            "prose": "world:demo_world:prose",
            "scene": "thread:thread_001:scene:sample",
            "scene_prompt": "scene_prompt:demo_world:intimate",
            "thread": "thread:thread_001",
            "world": "world:demo_world",
        }
        values = {key: all_values[key] for key in keys}
        values["DOCUMENT_ID"] = document_ids.get(
            expected_type,
            f"{expected_type}:sample",
        )
        return render_wiki_template(
            template_name,
            values,
        )

    character = thread_store.create_document(
        "characters/character_a.md",
        render_case("character.md"),
    )
    assert character.metadata is not None and character.metadata.type == "character"
    character_sections = parse_markdown_sections(character.content)
    assert ("기본 신상", "나이와 생년월일") in character_sections
    assert ("현재 상태", "신체 상태와 감정 상태") in character_sections

    world_store.create_document(
        "characters/character_profile_a.md",
        render_case("character_profile.md"),
    )
    for template_name, (expected_type, keys, scope_field) in template_cases.items():
        rendered = render_case(template_name)
        metadata = parse_frontmatter(rendered)
        assert metadata is not None and metadata.type == expected_type
        if template_name in {"scenario_opening_scene.md", "scene_prompt.md"}:
            expected_visibility = ["actor", "player"]
        elif expected_type == "memory":
            expected_visibility = ["actor", "updater"]
        elif expected_type == "thread":
            expected_visibility = ["updater", "player"]
        else:
            expected_visibility = ["actor", "updater", "player"]
        assert metadata.schema_version == 1
        assert metadata.visibility == expected_visibility
        if scope_field is not None:
            expected_scope = (
                all_values["WORLD_ID"]
                if scope_field == "world_id"
                else all_values["THREAD_ID"]
            )
            assert getattr(metadata, scope_field) == expected_scope
        if "OWNER_ID" in keys:
            assert metadata.owner == all_values["OWNER_ID"]
        if "PARTICIPANT_A_ID" in keys:
            assert metadata.participants == [
                all_values["PARTICIPANT_A_ID"],
                all_values["PARTICIPANT_B_ID"],
            ]
        if expected_type == "scene_prompt":
            assert metadata.model_extra["scene_type"] == all_values["SCENE_TYPE"]
            assert metadata.model_extra["description"] == all_values["DESCRIPTION"]
        assert parse_markdown_sections(rendered)

    scenario_template = render_case("scenario.md")
    assert "## 시나리오 특징" in scenario_template
    assert "## 시나리오 한정 묘사 규정" in scenario_template
    assert "시작 시각" not in scenario_template

    literal_values = {
        "DOCUMENT_ID": "world:literal",
        "DISPLAY_NAME": "{{WORLD_ID}}",
        "CREATED_AT": all_values["CREATED_AT"],
    }
    assert "# {{WORLD_ID}}" in render_wiki_template("world.md", literal_values)
    try:
        render_wiki_template(
            "world.md",
            {
                "DOCUMENT_ID_YAML": "world:bypass",
                "DISPLAY_NAME": "우회",
                "CREATED_AT": all_values["CREATED_AT"],
            },
        )
    except WikiScaffoldError:
        pass
    else:
        raise AssertionError("Callers must not provide _YAML template keys")

    repeated = scaffold_world(vault_root, "demo_world", "데모 월드")
    assert [document.revision for document in repeated.documents] == [
        document.revision for document in world.documents
    ]
    world_store.write_document(
        "world.md",
        world_document.content.replace("- Genre:", "- Genre: fantasy"),
        expected_revision=world_document.revision,
    )
    try:
        scaffold_world(vault_root, "demo_world", "데모 월드")
    except FileExistsError:
        pass
    else:
        raise AssertionError("Scaffolding must not overwrite an edited world")

    invalid_path = "characters/invalid.md"
    try:
        world_store.write_document(invalid_path, "---\nvisibility: [actor\n---\n# 오류\n")
    except WikiFrontmatterError:
        pass
    else:
        raise AssertionError("Invalid frontmatter must be rejected")
    assert not world_store.resolve_path(invalid_path).exists()

    atomic_root = root / "atomic"
    original_create_document = WikiStore.create_document
    create_calls = 0

    def fail_second_create(
        target_store: WikiStore,
        relative_path: str,
        content: str,
    ) -> WikiDocument:
        """두 번째 핵심 문서 생성만 실패시켜 scaffold rollback을 검증합니다."""
        nonlocal create_calls
        create_calls += 1
        if create_calls == 2:
            raise OSError("simulated scaffold failure")
        return original_create_document(target_store, relative_path, content)

    with patch.object(WikiStore, "create_document", new=fail_second_create):
        try:
            scaffold_world(atomic_root, "atomic_world", "원자성 월드")
        except WikiScaffoldError:
            pass
        else:
            raise AssertionError("Partial scaffold failure must be reported")
    atomic_world = atomic_root / "worlds" / "atomic_world"
    assert (atomic_world / "world.md").exists()
    assert not (atomic_world / "prose.md").exists()
    resumed = scaffold_world(atomic_root, "atomic_world", "원자성 월드")
    assert len(resumed.documents) == 2
    assert (atomic_world / "prose.md").exists()


def _check_wiki_context_scenario_overrides(root: Path) -> None:
    """scenario.md의 optional NPC override와 world cast allowlist를 검증합니다."""

    def replace_frontmatter_line(content: str, key: str, value: str) -> str:
        """Frontmatter 안의 단일 scalar line을 키 기준으로 교체합니다."""
        lines = content.splitlines()
        if not lines or lines[0] != "---":
            raise AssertionError("expected YAML frontmatter")
        for index, line in enumerate(lines[1:], start=1):
            if line == "---":
                break
            if line.startswith(f"{key}:"):
                lines[index] = f"{key}: {value}"
                updated = "\n".join(lines) + "\n"
                if f"{key}: {value}" not in updated:
                    raise AssertionError(f"{key} frontmatter override was not applied")
                return updated
        raise AssertionError(f"{key} frontmatter line not found")

    def insert_frontmatter_lines(content: str, extra: str) -> str:
        """Frontmatter 종료 구분자 직전에 추가 YAML 줄을 삽입합니다."""
        if not extra:
            return content
        lines = content.splitlines()
        if not lines or lines[0] != "---":
            raise AssertionError("expected YAML frontmatter")
        closing_index = -1
        for index, line in enumerate(lines[1:], start=1):
            if line == "---":
                closing_index = index
                break
        if closing_index < 0:
            raise AssertionError("frontmatter closing delimiter not found")
        extra_lines = extra.splitlines()
        updated_lines = lines[:closing_index] + extra_lines + lines[closing_index:]
        updated = "\n".join(updated_lines) + "\n"
        for line in extra_lines:
            if line not in updated:
                raise AssertionError(f"scenario frontmatter injection was not applied: {line}")
        return updated

    def build_case(
        case_name: str,
        scenario_extra: str = "",
        include_scenario_character: bool = True,
    ) -> Path:
        """Wiki context 테스트용 최소 world/scenario 번들을 만듭니다."""
        vault_root = root / case_name
        scaffold_world(vault_root, "demo_world", "데모 월드")
        world_root = vault_root / "worlds" / "demo_world"
        store = WikiStore(world_root)
        created_at = "2026-07-21T00:00:00+00:00"

        world_document = store.read_document("world.md")
        world_content = render_wiki_template(
            "world.md",
            {
                "DOCUMENT_ID": "world:demo_world",
                "DISPLAY_NAME": "데모 월드",
                "CREATED_AT": created_at,
            },
        )
        world_content = replace_frontmatter_line(
            world_content,
            "pc_profile_id",
            "character_profile:pc",
        )
        world_content = replace_frontmatter_line(
            world_content,
            "npc_profile_id",
            "character_profile:world_npc",
        )
        store.write_document(
            "world.md",
            world_content,
            expected_revision=world_document.revision,
        )
        updated_world = store.read_document("world.md")
        assert updated_world.metadata is not None
        assert str(getattr(updated_world.metadata, "pc_profile_id", "") or "").strip() == (
            "character_profile:pc"
        )
        assert str(getattr(updated_world.metadata, "npc_profile_id", "") or "").strip() == (
            "character_profile:world_npc"
        )

        scenario_root = world_root / "scenarios" / "demo"
        (scenario_root / "characters").mkdir(parents=True, exist_ok=True)
        scenario_document = render_wiki_template(
            "scenario.md",
            {
                "DOCUMENT_ID": "scenario:demo_world:demo",
                "WORLD_ID": "demo_world",
                "CREATED_AT": created_at,
            },
        )
        scenario_document = insert_frontmatter_lines(scenario_document, scenario_extra)
        created_scenario = store.create_document("scenarios/demo/scenario.md", scenario_document)
        if scenario_extra:
            for line in scenario_extra.splitlines():
                assert line in created_scenario.content

        start_state = render_wiki_template(
            "scenario_start_state.md",
            {
                "DOCUMENT_ID": "scenario:demo_world:demo:start_state",
                "WORLD_ID": "demo_world",
                "CREATED_AT": created_at,
            },
        )
        start_state = start_state.replace("- Time:", "- Time: 2026년 7월 21일 13시")
        start_state = start_state.replace("- Place:", "- Place: 학생회관 라운지")
        start_state = start_state.replace("- Relationship:", "- Relationship: 첫 대면 직전")
        start_state = start_state.replace("- Immediate background:", "- Immediate background: 오리엔테이션 대기")
        start_state = start_state.replace("- Positions:", "- Positions: 모든 인물이 라운지에 있다.")
        start_state = start_state.replace("- Conditions:", "- Conditions: 다들 긴장했지만 대화 가능하다.")
        start_state = start_state.replace("- Trigger:", "- Trigger: 사회자가 조 편성을 발표한다.")
        store.create_document("scenarios/demo/start_state.md", start_state)

        opening_scene = render_wiki_template(
            "scenario_opening_scene.md",
            {
                "DOCUMENT_ID": "scenario:demo_world:demo:opening",
                "WORLD_ID": "demo_world",
                "CREATED_AT": created_at,
            },
        ).replace("첫 장면 원문을 작성하세요.", "라운지에 처음 모인 학생들이 서로를 살핀다.")
        store.create_document("scenarios/demo/opening_scene.md", opening_scene)

        for relative_path, profile_id, title in (
            ("characters/pc.md", "character_profile:pc", "Player Character"),
            ("characters/world_npc.md", "character_profile:world_npc", "World NPC"),
            ("characters/alt_npc.md", "character_profile:alt_npc", "Scenario NPC"),
            ("characters/bystander.md", "character_profile:bystander", "Bystander"),
        ):
            store.create_document(
                relative_path,
                render_wiki_template(
                    "character_profile.md",
                    {
                        "DOCUMENT_ID": profile_id,
                        "WORLD_ID": "demo_world",
                        "TITLE": title,
                        "CREATED_AT": created_at,
                    },
                ),
            )
        if include_scenario_character:
            store.create_document(
                "scenarios/demo/characters/guest.md",
                render_wiki_template(
                    "character_profile.md",
                    {
                        "DOCUMENT_ID": "character_profile:guest",
                        "WORLD_ID": "demo_world",
                        "TITLE": "Guest Character",
                        "CREATED_AT": created_at,
                    },
                ),
            )
        return vault_root

    override_root = build_case(
        "context_override",
        scenario_extra="npc_profile_id: character_profile:alt_npc\n",
    )
    override_setup = load_wiki_setup(
        override_root,
        "demo_world",
        "demo",
        "thread_override",
    )
    assert override_setup.npc_id == "character_profile:alt_npc"
    assert override_setup.npc_name == "Scenario NPC"

    default_root = build_case("context_default")
    default_setup = load_wiki_setup(
        default_root,
        "demo_world",
        "demo",
        "thread_default",
    )
    assert default_setup.npc_id == "character_profile:world_npc"
    assert default_setup.npc_name == "World NPC"

    allowlist_root = build_case(
        "context_allowlist",
        scenario_extra=(
            "npc_profile_id: character_profile:alt_npc\n"
            "characters:\n"
            "  - character_profile:pc\n"
            "  - character_profile:alt_npc\n"
        ),
    )
    allowlist_setup = initialize_wiki_thread(
        allowlist_root,
        "demo_world",
        "demo",
        "thread_allowlist",
    )
    allowlist_thread = allowlist_root / "threads" / "thread_allowlist" / "characters"
    assert allowlist_setup.npc_id == "character_profile:alt_npc"
    assert (allowlist_thread / "pc.md").is_file()
    assert (allowlist_thread / "alt_npc.md").is_file()
    assert (allowlist_thread / "guest.md").is_file()
    assert not (allowlist_thread / "world_npc.md").exists()
    assert not (allowlist_thread / "bystander.md").exists()

    empty_allowlist_root = build_case(
        "context_empty_allowlist",
        scenario_extra="characters: []\n",
    )
    empty_allowlist_profiles = _profile_documents(
        empty_allowlist_root,
        "demo_world",
        "demo",
    )
    empty_allowlist_ids = {
        profile.metadata.id
        for profile in empty_allowlist_profiles
        if profile.metadata is not None
    }
    assert empty_allowlist_ids == {"character_profile:guest"}

    def assert_context_error(case_name: str, scenario_extra: str) -> None:
        """잘못된 scenario frontmatter가 WikiContextError로 실패하는지 검증합니다."""
        invalid_root = build_case(case_name, scenario_extra=scenario_extra)
        try:
            load_wiki_setup(invalid_root, "demo_world", "demo", "thread_invalid")
        except WikiContextError:
            return
        raise AssertionError(f"{case_name} must raise WikiContextError")

    assert_context_error(
        "context_unknown_character",
        scenario_extra=(
            "characters:\n"
            "  - character_profile:pc\n"
            "  - character_profile:missing\n"
        ),
    )
    assert_context_error(
        "context_invalid_characters_type",
        scenario_extra="characters: character_profile:pc\n",
    )
    assert_context_error(
        "context_invalid_character_item",
        scenario_extra=(
            "characters:\n"
            "  - character_profile:pc\n"
            "  - 17\n"
        ),
    )
    assert_context_error(
        "context_invalid_character_prefix",
        scenario_extra=(
            "characters:\n"
            "  - character:pc\n"
        ),
    )
    assert_context_error(
        "context_invalid_npc_prefix",
        scenario_extra="npc_profile_id: character:alt_npc\n",
    )
    assert_context_error(
        "context_unknown_npc_id",
        scenario_extra="npc_profile_id: character_profile:missing\n",
    )


def main() -> None:
    """임시 vault에서 섹션 선택부터 다음 턴 커밋까지 검증합니다."""
    with TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        _check_scaffolds(root)
        _check_wiki_context_scenario_overrides(root)
        _check_recall()
        _check_migrations()
        _check_diagnostics(root / "scaffold")
        _check_explorer(root / "scaffold")
        store = WikiStore(root)
        store.write_document("characters/character_a.md", _CHARACTER_DOCUMENT)
        store.write_document("scene/current.md", _SCENE_DOCUMENT)
        document = store.read_document("characters/character_a.md")
        scene_document = store.read_document("scene/current.md")
        assert document.metadata is not None
        assert document.metadata.model_extra["description"].startswith(
            "## frontmatter 섹션이 아님"
        )

        sections = parse_markdown_sections(document.content)
        assert ("기본 신상", "나이와 생년월일") in sections
        assert not any("실제 섹션이 아님" in path for path in sections)
        assert ("메모", "들여쓴 실제 섹션") in sections
        assert ("메모", "C#") in sections
        asyncio.run(_check_retry_exhaustion(document))
        asyncio.run(_check_update_policy(document, scene_document))
        _check_accepted_header_sync(scene_document)

        event_pending = asyncio.run(
            _generate_event_creation(document, scene_document)
        )
        assert len(event_pending.creations) == 2
        assert event_pending.creations[0].document == "events/library-power-outage.md"
        assert (
            event_pending.creations[1].document
            == "memories/character-a-remembers-outage.md"
        )
        assert "## 발생 정보" in event_pending.creations[0].content
        assert "## 사건 내용" in event_pending.creations[0].content
        assert "## 진행 상태" in event_pending.creations[0].content
        assert "- 상태: concluded" in event_pending.creations[0].content
        assert "- 진행 경과: The occurrence concluded in this turn." in (
            event_pending.creations[0].content
        )
        assert "- 종료 시각: 2026-07-23 13:10" in (
            event_pending.creations[0].content
        )
        assert "visibility: [actor, updater]" in event_pending.creations[1].content
        assert "owner: \"character_profile:character_a\"" in (
            event_pending.creations[1].content
        )
        assert "- 관련 사건: Library Power Outage" in (
            event_pending.creations[1].content
        )
        assert "event:library-power-outage" not in (
            event_pending.creations[1].content
        )
        assert "source_commit_id" in event_pending.creations[0].content
        assert "source_user_message_id" in event_pending.creations[0].content
        queue = WikiCommitQueue(store)
        queue.queue(event_pending)
        event_applied = queue.apply_pending()
        assert event_applied is not None
        assert len(event_applied.applied_creations) == 2
        event_path = store.resolve_path("events/library-power-outage.md")
        memory_path = store.resolve_path(
            "memories/character-a-remembers-outage.md"
        )
        assert event_path.exists()
        assert memory_path.exists()
        event_inverse = queue.apply_inverse(event_pending.commit_id)
        assert event_inverse.status == "applied"
        assert not event_path.exists()
        assert not memory_path.exists()
        assert event_inverse.inverse_commit_id is not None
        restore_inverse = queue.apply_inverse(event_inverse.inverse_commit_id)
        assert restore_inverse.status == "applied"
        assert event_path.exists()
        assert memory_path.exists()
        restored_event = store.read_document("events/library-power-outage.md")
        edited_event = store.write_document(
            restored_event.path,
            restored_event.content.replace(
                "The presentation deadline remains active",
                "A manually revised deadline remains active",
            ),
            expected_revision=restored_event.revision,
        )
        assert restore_inverse.inverse_commit_id is not None
        creation_conflict = queue.plan_inverse(restore_inverse.inverse_commit_id)
        assert creation_conflict.status == "conflict"
        store.write_document(
            edited_event.path,
            restored_event.content,
            expected_revision=edited_event.revision,
        )

        # goal/item/secret 생성·갱신 권한과 생성 문서 inverse round-trip을 검증한다.
        asyncio.run(_check_postprocess())
        goal_pending = asyncio.run(_check_goal_item_secret(document))
        queue.queue(goal_pending)
        goal_applied = queue.apply_pending()
        assert goal_applied is not None and len(goal_applied.applied_creations) == 1
        goal_path = store.resolve_path("goals/pass-exam.md")
        assert goal_path.exists()
        goal_inverse = queue.apply_inverse(goal_pending.commit_id)
        assert goal_inverse.status == "applied"
        assert not goal_path.exists()

        pending = asyncio.run(_generate_with_one_retry(document))
        assert pending.updater_attempts == 3
        queue.queue(pending)
        assert "신체 상태: 안정" in store.read_document(document.path).content
        assert store.resolve_path("commit.md").exists()

        # 대기 중 다른 섹션을 고쳐도 target section이 그대로면 자동 rebase한다.
        store.resolve_path(document.path).write_text(
            document.content.replace("대학생", "대학원생"),
            encoding="utf-8",
        )

        applied = queue.apply_pending()
        assert applied is not None and applied.status == "applied"
        assert len(applied.applied_changes) == 1
        applied_change = applied.applied_changes[0]
        assert "신체 상태: 안정" in applied_change.before_markdown
        assert "계단을 올라 숨이 차고 피곤하다" in applied_change.after_markdown
        assert applied_change.before_revision != applied_change.after_revision
        assert "계단을 올라 숨이 차고 피곤하다" in store.read_document(document.path).content
        assert "대학원생" in store.read_document(document.path).content
        assert not store.resolve_path("commit.md").exists()
        assert store.resolve_path(f"commits/{pending.commit_id}.md").exists()

        # 같은 section의 다른 줄을 수동 수정해도 inverse는 그 변경을 보존한다.
        manually_edited = store.read_document(document.path)
        store.write_document(
            document.path,
            manually_edited.content.replace(
                "- 감정 상태: 평온",
                "- 감정 상태: 차분\n- 사용자 메모: 유지",
            ),
            expected_revision=manually_edited.revision,
        )
        inverse_plan = queue.plan_inverse(pending.commit_id)
        assert inverse_plan.status == "ready"
        inverse_result = queue.apply_inverse(pending.commit_id)
        assert inverse_result.status == "applied"
        assert inverse_result.inverse_commit_id is not None
        reverted = store.read_document(document.path)
        assert "- 신체 상태: 안정" in reverted.content
        assert "- 감정 상태: 차분" in reverted.content
        assert "- 사용자 메모: 유지" in reverted.content
        inverse_archive = queue.load_archive(inverse_result.inverse_commit_id)
        assert inverse_archive.operation == "inverse"
        assert inverse_archive.source_commit_id == pending.commit_id
        repeated_inverse_plan = queue.plan_inverse(pending.commit_id)
        assert repeated_inverse_plan.status == "already_reverted", (
            repeated_inverse_plan.model_dump_json(indent=2)
        )

        # 같은 줄을 수동 수정하면 충돌만 반환하고 어떤 Markdown도 쓰지 않는다.
        reverted_sections = parse_markdown_sections(reverted.content)
        reverted_state = reverted_sections[("현재 상태", "신체 상태와 감정 상태")]
        conflicting_source = PendingWikiCommit(
            user_input_hash="conflict-user",
            actor_response_hash="conflict-actor",
            updater_model="test",
            patches=[
                SectionPatch(
                    document=reverted.path,
                    base_revision=reverted.revision,
                    base_section_revision=document_revision(reverted_state.markdown),
                    base_markdown=reverted_state.markdown,
                    section_path=("현재 상태", "신체 상태와 감정 상태"),
                    replacement_markdown=reverted_state.markdown.replace(
                        "- 신체 상태: 안정",
                        "- 신체 상태: 매우 피곤",
                    ),
                    evidence="신체 상태가 매우 피곤해졌다.",
                    confidence=1.0,
                )
            ],
        )
        queue.queue(conflicting_source)
        assert queue.apply_pending() is not None
        conflict_current = store.read_document(reverted.path)
        store.write_document(
            conflict_current.path,
            conflict_current.content.replace(
                "- 신체 상태: 매우 피곤",
                "- 신체 상태: 수동으로 회복",
            ),
            expected_revision=conflict_current.revision,
        )
        conflict_before = store.read_document(reverted.path).content
        conflict_plan = queue.plan_inverse(conflicting_source.commit_id)
        assert conflict_plan.status == "conflict"
        assert len(conflict_plan.conflicts) == 1
        assert queue.apply_inverse(conflicting_source.commit_id).status == "conflict"
        assert store.read_document(reverted.path).content == conflict_before
        assert not store.resolve_path("commit.md").exists()

        # 문서 적용 직후 죽고 commit.md가 남은 상황도 중복 적용 없이 복구한다.
        current = store.read_document(document.path)
        current_sections = parse_markdown_sections(current.content)
        job_section = current_sections[("기본 신상", "직업과 소속")]
        crash_recovery = PendingWikiCommit(
            user_input_hash="user",
            actor_response_hash="actor",
            updater_model="test",
            patches=[
                SectionPatch(
                    document=current.path,
                    base_revision=current.revision,
                    base_section_revision=document_revision(job_section.markdown),
                    base_markdown=job_section.markdown,
                    section_path=("기본 신상", "직업과 소속"),
                    replacement_markdown="### 직업과 소속\n\n- 직업: 작가",
                    evidence="직업이 작가로 바뀌었다.",
                    confidence=1.0,
                )
            ],
        )
        queue.queue(crash_recovery)
        store.apply_patches(crash_recovery.patches)
        recovered_crash = queue.apply_pending()
        assert recovered_crash is not None
        assert recovered_crash.applied_changes[0].before_markdown == job_section.markdown
        assert "작가" in recovered_crash.applied_changes[0].after_markdown
        assert "작가" in store.read_document(current.path).content

        current = store.read_document(document.path)
        current_sections = parse_markdown_sections(current.content)
        job_section = current_sections[("기본 신상", "직업과 소속")]
        conflict = PendingWikiCommit(
            user_input_hash="user-2",
            actor_response_hash="actor-2",
            updater_model="test",
            patches=[
                SectionPatch(
                    document=current.path,
                    base_revision=current.revision,
                    base_section_revision=document_revision(job_section.markdown),
                    section_path=("기본 신상", "직업과 소속"),
                    replacement_markdown="### 직업과 소속\n\n- 직업: 개발자",
                    evidence="직업이 개발자로 바뀌었다.",
                    confidence=1.0,
                )
            ],
        )
        queue.queue(conflict)
        store.resolve_path(current.path).write_text(
            current.content.replace("작가", "휴학생"),
            encoding="utf-8",
        )
        try:
            queue.apply_pending()
        except Exception:
            pass
        else:
            raise AssertionError("Manual edit must cause a revision conflict")
        loaded = queue.load()
        assert loaded is not None and loaded.status == "failed"
        assert "휴학생" in store.read_document(current.path).content
        skipped = queue.skip_pending("수동 편집을 유지함")
        assert skipped is not None and skipped.status == "skipped"
        assert skipped.failure_reason
        assert skipped.resolution_reason == "수동 편집을 유지함"
        assert not store.resolve_path("commit.md").exists()
        skipped_archive = store.resolve_path(f"commits/{skipped.commit_id}.md")
        assert skipped_archive.exists()
        assert queue._load_path(skipped_archive).status == "skipped"

        # 서로 다른 Queue 인스턴스가 동시에 저장해도 하나의 pending만 남는다.
        race_store = WikiStore(root / "queue-race")
        race_queues = [WikiCommitQueue(race_store), WikiCommitQueue(race_store)]
        race_commits = [
            PendingWikiCommit(
                user_input_hash=f"user-{index}",
                actor_response_hash=f"actor-{index}",
                updater_model="test",
            )
            for index in range(2)
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(queue_item.queue, commit_item)
                for queue_item, commit_item in zip(race_queues, race_commits)
            ]
            successes = 0
            failures = 0
            for future in futures:
                try:
                    future.result()
                    successes += 1
                except PendingCommitExists:
                    failures += 1
        assert (successes, failures) == (1, 1)
        race_winner = race_queues[0].load()
        assert race_winner is not None
        same_id_different_payload = race_winner.model_copy(
            update={"summary": "different payload"}
        )
        try:
            race_queues[0].queue(same_id_different_payload)
        except PendingCommitExists:
            pass
        else:
            raise AssertionError("Same commit_id with different payload must conflict")

    print("smoke_wiki_v2: ok")


if __name__ == "__main__":
    main()
