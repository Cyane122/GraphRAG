# ================================
# tests/smoke_wiki_creation.py
#
# Wiki creation smoke checks cover accepted-header sync and goal, item, secret, and event creation flows.
#
# Functions
#   - _check_accepted_header_sync(scene: WikiDocument) -> None : Validate accepted-header time and location synchronization guards.
#   - _generate_goal_creation(character: WikiDocument) -> PendingWikiCommit : Plan a validated actor-owned goal creation.
#   - _check_goal_authority_and_progress(character: WikiDocument) -> tuple[PendingWikiCommit, WikiDocument] : Validate goal authority and mutable progress sections.
#   - _check_item_and_secret_visibility(character: WikiDocument) -> WikiDocument : Validate item creation, secret visibility, and leak detection.
#   - _check_secret_runtime_guards(character: WikiDocument, secret_document: WikiDocument) -> None : Validate runtime-owned secret disclosure guards.
#   - _check_goal_wikilink_guards(character: WikiDocument, goal_document: WikiDocument) -> None : Validate wikilink rejection and normal goal creation behavior.
#   - _check_secret_runtime_guards_and_wikilinks(character: WikiDocument, goal_document: WikiDocument, secret_document: WikiDocument) -> None : Run the secret runtime and wikilink guard suite.
#   - _check_goal_item_secret(character: WikiDocument) -> PendingWikiCommit : Validate goal, item, and secret creation and patch policy.
#   - run_creation_suite(character: WikiDocument, scene: WikiDocument) -> tuple[PendingWikiCommit, PendingWikiCommit] : Run the full creation smoke suite and return the event and goal pending commits.
#   - main() -> None : Run the standalone creation smoke suite.
# ================================

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wiki import (  # noqa: E402
    PendingWikiCommit,
    WikiDocument,
    WikiUpdaterResult,
    document_revision,
    parse_frontmatter,
)
from src.wiki.commit_header_sync import synchronize_accepted_header  # noqa: E402
from tests.wiki_smoke_fixtures import (  # noqa: E402
    _expect_update_rejected,
    _generate_event_creation,
    _plan_update,
    create_base_store,
)

def _check_accepted_header_sync(scene: WikiDocument) -> None:
    """Accepted 헤더의 시간 전진, 장소 grounding과 날짜 점프 guard를 검증합니다."""
    forward = WikiUpdaterResult(summary="", patches=[])
    synchronize_accepted_header(
        forward,
        [scene],
        "계속 이야기한다.",
        "**2026년 7월 23일 목요일 13시 05분, 대학 도서관**\n\n대화가 이어졌다.",
    )
    assert len(forward.patches) == 1
    assert "13:05" in forward.patches[0].replacement_markdown
    assert "대학 도서관" in forward.patches[0].replacement_markdown

    ungrounded_move = WikiUpdaterResult(summary="", patches=[])
    synchronize_accepted_header(
        ungrounded_move,
        [scene],
        "계속 이야기한다.",
        "**2026년 7월 23일 목요일 13시 06분, 학생회관**\n\n문이 닫혔다.",
    )
    assert "13:06" in ungrounded_move.patches[0].replacement_markdown
    assert "대학 도서관" in ungrounded_move.patches[0].replacement_markdown
    assert "학생회관" not in ungrounded_move.patches[0].replacement_markdown

    grounded_move = WikiUpdaterResult(summary="", patches=[])
    synchronize_accepted_header(
        grounded_move,
        [scene],
        "학생회관으로 이동한다.",
        "**2026년 7월 23일 목요일 13시 06분, 학생회관**\n\n문이 닫혔다.",
    )
    assert "학생회관" in grounded_move.patches[0].replacement_markdown

    rejected_jump = WikiUpdaterResult(summary="", patches=[])
    synchronize_accepted_header(
        rejected_jump,
        [scene],
        "계속 이야기한다.",
        "**2026년 7월 24일 금요일 09시 00분, 대학 도서관**\n\n아침이었다.",
    )
    assert rejected_jump.patches == []

    explicit_jump = WikiUpdaterResult(summary="", patches=[])
    synchronize_accepted_header(
        explicit_jump,
        [scene],
        "다음날 아침까지 기다린다.",
        "**2026년 7월 24일 금요일 09시 00분, 대학 도서관**\n\n아침이었다.",
    )
    assert "July 24, 2026" in explicit_jump.patches[0].replacement_markdown

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
    return await _plan_update(
        [character],
        payload,
        "상황을 지켜본다.",
        evidence,
        actor_profile_id="character_profile:character_a",
        user_message_id="user-goal",
        assistant_message_id="assistant-goal",
    )

async def _check_goal_authority_and_progress(
    character: WikiDocument,
) -> tuple[PendingWikiCommit, WikiDocument]:
    """Validate goal authority and the mutable progress section."""
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
        "goal owner character_profile:character_a requires actor_response evidence",
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
    progress_pending = await _plan_update(
        [character, goal_document],
        progress_patch,
        "지켜본다.",
        "캐릭터 A는 기출문제 3개년 분량을 모두 끝냈다.",
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
        "goal updates may modify only the '진행 상태' section",
        actor_profile_id="character_profile:character_a",
    )
    return goal_pending, goal_document

async def _check_item_and_secret_visibility(
    character: WikiDocument,
) -> WikiDocument:
    """Validate item creation, secret visibility, and leak detection."""
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
    item_pending = await _plan_update(
        [character],
        item_payload,
        "지켜본다.",
        "캐릭터 A는 복도에서 낡은 황동 열쇠를 주워 주머니에 넣었다.",
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
    secret_pending = await _plan_update(
        [character],
        secret_payload,
        "지켜본다.",
        "캐릭터 A는 아무에게도 말 못 한 빚을 떠올리며 표정을 감췄다.",
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

    # 존재하지 않는 profile을 knower로 넣으면 거부한다.
    unknown_knower_payload = json.loads(json.dumps(secret_payload))
    unknown_knower_payload["creations"][0]["document_id"] = "secret:other"
    unknown_knower_payload["creations"][0]["knowers"] = ["character_profile:ghost"]
    await _expect_update_rejected(
        [character],
        unknown_knower_payload,
        "지켜본다.",
        "캐릭터 A는 아무에게도 말 못 한 빚을 떠올리며 표정을 감췄다.",
        "Secret knowers must be active thread profiles",
        actor_profile_id="character_profile:character_a",
    )
    return secret_document

async def _check_secret_runtime_guards(
    character: WikiDocument,
    secret_document: WikiDocument,
) -> None:
    """Validate runtime-owned secret disclosure guards."""
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
    await _expect_update_rejected(
        [character, secret_document],
        secret_status_patch_payload,
        "지켜본다.",
        "캐릭터 A는 가끔 전화를 급히 피했다.",
        "Runtime-owned secret disclosure status cannot be patched by the "
        "gameplay model: secrets/hidden-debt.md",
        actor_profile_id="character_profile:character_a",
    )

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
    secret_clue_pending = await _plan_update(
        [character, secret_document],
        secret_clue_patch_payload,
        "지켜본다.",
        "캐릭터 A는 가끔 전화를 급히 피하고 독촉장을 가방 깊숙이 숨겼다.",
        actor_profile_id="character_profile:character_a",
    )
    assert secret_clue_pending.patches[0].document == "secrets/hidden-debt.md"
    assert secret_clue_pending.patches[0].section_path == ("공개 상태", "공개 단서와 오해")

async def _check_goal_wikilink_guards(
    character: WikiDocument,
    goal_document: WikiDocument,
) -> None:
    """Validate wikilink rejection and normal goal creation behavior."""
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
    await _expect_update_rejected(
        [character, goal_document],
        wikilink_patch_payload,
        "지켜본다.",
        "캐릭터 A는 참고 메모를 확인했다.",
        "Wiki document 'goals/pass-exam.md' contains wikilink",
        actor_profile_id="character_profile:character_a",
    )

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
    await _expect_update_rejected(
        [character],
        wikilink_creation_payload,
        "지켜본다.",
        "캐릭터 A는 오늘부터 장학금 신청 준비를 시작하겠다고 마음먹었다.",
        "Wiki document 'goals/linked-note.md' contains wikilink",
        actor_profile_id="character_profile:character_a",
    )

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
    normal_creation_pending = await _plan_update(
        [character],
        normal_creation_payload,
        "지켜본다.",
        "캐릭터 A는 오늘부터 장학금 신청 준비를 시작하겠다고 마음먹었다.",
        actor_profile_id="character_profile:character_a",
    )
    assert normal_creation_pending.creations[0].document == "goals/scholarship-plan.md"

async def _check_secret_runtime_guards_and_wikilinks(
    character: WikiDocument,
    goal_document: WikiDocument,
    secret_document: WikiDocument,
) -> None:
    """Run the secret runtime and wikilink guard suite."""
    await _check_secret_runtime_guards(character, secret_document)
    await _check_goal_wikilink_guards(character, goal_document)

async def _check_goal_item_secret(character: WikiDocument) -> PendingWikiCommit:
    """Validate goal, item, and secret creation and patch policy."""
    goal_pending, goal_document = await _check_goal_authority_and_progress(character)
    secret_document = await _check_item_and_secret_visibility(character)
    await _check_secret_runtime_guards_and_wikilinks(
        character,
        goal_document,
        secret_document,
    )
    return goal_pending

async def run_creation_suite(
    character: WikiDocument,
    scene: WikiDocument,
) -> tuple[PendingWikiCommit, PendingWikiCommit]:
    """Run the full creation smoke suite and return the event and goal pending commits."""
    _check_accepted_header_sync(scene)
    event_pending = await _generate_event_creation(character, scene)
    goal_pending = await _check_goal_item_secret(character)
    return event_pending, goal_pending

def main() -> None:
    """Run the standalone creation smoke suite."""
    with TemporaryDirectory() as temporary_directory:
        _store, character, scene = create_base_store(Path(temporary_directory))
        asyncio.run(run_creation_suite(character, scene))

    print("smoke_wiki_creation: ok")

if __name__ == "__main__":
    main()
