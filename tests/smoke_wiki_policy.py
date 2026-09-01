# ================================
# tests/smoke_wiki_policy.py
#
# Wiki policy smoke checks cover updater retries, rejection paths, and section-level update policy guards.
#
# Functions
#   - _generate_with_one_retry(document: WikiDocument) -> PendingWikiCommit : Return a valid updater result after one failed attempt.
#   - _check_retry_exhaustion(document: WikiDocument) -> None : Validate updater failure after every retry attempt.
#   - _memory_creation_payload(related_event_id: str) -> dict[str, object] : Build a minimal memory-creation Updater payload for one related_event_id.
#   - _check_memory_related_event_authority_messages(character: WikiDocument, scene: WikiDocument) -> None : Validate distinct WikiCommitPlanningError messages for a missing vs. titleless related Event.
#   - _check_memory_creation_authority_invariant() -> None : Validate that passing _validate_memory_creation_authority requires event_titles membership, not just event_ids.
#   - _check_updater_programming_error_not_retried(character: WikiDocument) -> None : Validate that a programming error inside validation propagates without retry.
#   - _check_severable_owner_authority_creation(character: WikiDocument, scene: WikiDocument, event: WikiDocument) -> None : Validate that a valid patch survives alongside a severed third-party Memory creation, with no retry and a diagnostics record.
#   - _check_severed_creation_triggers_fatal_cross_validation(character: WikiDocument, scene: WikiDocument) -> None : Validate that severance stays fatal when it breaks the Event/Memory pairing cross-check.
#   - _check_severed_creation_archive_and_canonical_isolation() -> None : Validate that a severed creation is recorded in the applied commit archive and never leaks into canonical Markdown.
#   - _check_scene_active_third_party_creation_authority(character: WikiDocument) -> None : Validate the scene-active third-party owner Memory rules (named evidence passes, unnamed evidence and wrong evidence_source stay fatal).
#   - _build_policy_documents(character: WikiDocument) -> tuple[WikiDocument, WikiDocument, WikiDocument, WikiDocument, WikiDocument] : Build the event, memory, npc, and relationship fixtures used by the policy suite.
#   - _check_static_and_event_creation_policy(character: WikiDocument, scene: WikiDocument, event: WikiDocument) -> None : Validate static-section and event-creation policy guards.
#   - _check_event_progress_policy(character: WikiDocument, scene: WikiDocument, event: WikiDocument, ongoing_event: WikiDocument) -> None : Validate allowed and rejected event progress updates.
#   - _check_event_progress_format_guards(character: WikiDocument, scene: WikiDocument, event: WikiDocument) -> None : Validate event progress status-line formatting guards.
#   - _check_memory_and_character_patch_policy(character: WikiDocument, scene: WikiDocument, event: WikiDocument, memory: WikiDocument) -> None : Validate memory mutability and player-state guards.
#   - _check_scene_evidence_policy(character: WikiDocument, scene: WikiDocument) -> None : Validate scene patch evidence and mixed-authority handling.
#   - _check_relationship_and_scene_policy(character: WikiDocument, scene: WikiDocument, npc: WikiDocument, relationship: WikiDocument) -> None : Validate relationship ownership and whole-scene patch rules.
#   - _check_scene_active_relationship_patch_authority(character: WikiDocument, scene: WikiDocument, npc: WikiDocument) -> None : Validate relationship-patch authority for a scene-active non-Actor owner (named-evidence pass, unnamed-evidence and scene-inactive-owner reject).
#   - _check_update_policy(character: WikiDocument, scene: WikiDocument) -> None : Run the full updater policy suite.
#   - run_policy_suite(character: WikiDocument, scene: WikiDocument) -> PendingWikiCommit : Run the full updater policy smoke suite and return the retried pending commit.
#   - main() -> None : Run the standalone policy smoke suite.
# ================================

from __future__ import annotations

import asyncio
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
    CreateMemoryDocument,
    PendingWikiCommit,
    WikiCommitPlanningError,
    WikiCommitQueue,
    WikiDocument,
    document_revision,
    parse_frontmatter,
    plan_pending_commit,
)
from src.wiki.commit_policy import _validate_memory_creation_authority  # noqa: E402
from tests.wiki_smoke_fixtures import (  # noqa: E402
    _CHARACTER_DOCUMENT,
    _EVENT_DOCUMENT,
    _ONGOING_EVENT_DOCUMENT,
    _RELATIONSHIP_DOCUMENT,
    _character_b_document,
    _expect_update_rejected,
    _generate_event_creation,
    _plan_update,
    _relationship_b_document,
    _scene_document_with_active_b,
    create_base_store,
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
            model_name="test-updater",
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
                    model_name="test-updater",
                    max_attempts=2,
                    debug_root=debug_root,
                )
            except WikiCommitPlanningError as exc:
                # 이유를 보지 않으면 무관한 버그로 예외가 나도 초록이 된다 - 두 시도
                # 모두 JSON 파싱에 실패했다는 원인이 최종 메시지에 실제로 담기는지 본다.
                assert "No JSON structure found" in str(exc)
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

def _memory_creation_payload(related_event_id: str) -> dict[str, object]:
    """`related_event_id`만 다른 최소 memory 생성 payload를 반환합니다."""
    evidence = "캐릭터 A는 그 사건을 기억한다."
    return {
        "summary": "memory 생성",
        "patches": [],
        "creations": [{
            "document_type": "memory",
            "document_id": "memory:related-event-probe",
            "title": "Related Event Probe",
            "owner": "character_profile:character_a",
            "related_event_id": related_event_id,
            "formation_trigger": "trigger",
            "formed_at": "2026-07-23 13:10",
            "location": "대학 도서관",
            "remembered_content": "remembered",
            "interpretation": "interpretation",
            "emotion": "emotion",
            "certainty": "certain",
            "distortion_risk": "risk",
            "evidence": evidence,
            "evidence_source": "actor_response",
            "confidence": 0.91,
        }],
    }

async def _check_memory_related_event_authority_messages(
    character: WikiDocument,
    scene: WikiDocument,
) -> None:
    """존재하지 않는 event와, H1 제목이 없는 기존 event를 가리키는 memory가 서로 다른
    WikiCommitPlanningError 메시지로 거부되는지 검증합니다(KeyError가 아니라)."""
    no_title_event_content = (
        "---\nid: event:no-title-event\ntype: event\nschema_version: 1\n"
        "thread_id: thread_001\nvisibility: [actor, updater, player]\n"
        "created_at: 2026-07-21T00:00:00+00:00\n---\n"
        "## Not An H1 Title\n\n## 발생 정보\n\n### 시각과 장소\n\n"
        "- 시각: 2026-07-23 13:00\n- 장소: 대학 도서관\n\n"
        "## 사건 내용\n\n### 객관적으로 발생한 일\n\n"
        "- 발생 내용: Something happened without an H1 heading.\n\n"
        "## 진행 상태\n\n- 상태: concluded\n- 진행 경과: Resolved.\n"
        "- 종료 시각: 2026-07-23 13:00\n"
    )
    no_title_event = WikiDocument(
        path="events/no-title-event.md",
        revision=document_revision(no_title_event_content),
        content=no_title_event_content,
        metadata=parse_frontmatter(no_title_event_content),
    )

    async def _plan_and_capture(
        documents: list[WikiDocument],
        related_event_id: str,
    ) -> str:
        model = Mock()
        model.generate_content_async = AsyncMock(
            return_value=SimpleNamespace(
                text=json.dumps(_memory_creation_payload(related_event_id), ensure_ascii=False)
            )
        )
        with (
            patch("src.wiki.commit_planner.get_model", return_value=model),
            patch("src.wiki.commit_planner.asyncio.sleep", new=AsyncMock()),
        ):
            try:
                await plan_pending_commit(
                    documents=documents,
                    user_input="상황을 지켜본다.",
                    actor_response="캐릭터 A는 그 사건을 기억한다.",
                    model_name="test-updater",
                    actor_profile_id="character_profile:character_a",
                    max_attempts=1,
                )
            except WikiCommitPlanningError as exc:
                return str(exc)
            raise AssertionError("Invalid related_event_id must be rejected")

    does_not_exist_message = await _plan_and_capture(
        [character, scene], "event:totally-made-up"
    )
    assert "does not exist" in does_not_exist_message
    assert "event:totally-made-up" in does_not_exist_message
    assert "KeyError" not in does_not_exist_message

    no_title_message = await _plan_and_capture(
        [character, scene, no_title_event], "event:no-title-event"
    )
    assert "has no title" in no_title_message
    assert "event:no-title-event" in no_title_message
    assert "KeyError" not in no_title_message
    assert "does not exist" not in no_title_message

def _check_memory_creation_authority_invariant() -> None:
    """`_validate_memory_creation_authority`가 event_titles 멤버십으로만 통과시키고
    event_ids만으로는 통과시키지 않는지(검사·조회 집합이 어긋날 수 없는지) 직접 검증합니다."""
    creation = CreateMemoryDocument(
        document_id="memory:invariant-probe",
        title="Invariant Probe",
        owner="character_profile:character_a",
        related_event_id="event:probe-target",
        formation_trigger="trigger",
        formed_at="2026-07-23 13:10",
        location="대학 도서관",
        remembered_content="remembered",
        interpretation="interpretation",
        emotion="emotion",
        certainty="certain",
        distortion_risk="risk",
        evidence="evidence",
        evidence_source="actor_response",
        confidence=0.91,
    )
    available_profile_ids = {"character_profile:character_a"}

    # event_ids에도 event_titles에도 없음 -> "does not exist".
    try:
        _validate_memory_creation_authority(
            creation,
            available_profile_ids=available_profile_ids,
            scene_active_profile_ids=set(),
            owner_document=None,
            event_ids=set(),
            event_titles={},
            player_profile_id="",
            actor_profile_id="character_profile:character_a",
        )
    except WikiCommitPlanningError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("Unknown related event must be rejected")

    # event_ids에는 있지만(예전 existing_ids였다면 통과) event_titles에는 없음 -> "has no title".
    # 이게 바로 "existing_ids가 너무 넓다"는 결함을 재현하는 대목이다: event_ids만
    # 보고 통과시켰다면 나중 event_titles 조회가 KeyError였을 것이다.
    try:
        _validate_memory_creation_authority(
            creation,
            available_profile_ids=available_profile_ids,
            scene_active_profile_ids=set(),
            owner_document=None,
            event_ids={"event:probe-target"},
            event_titles={},
            player_profile_id="",
            actor_profile_id="character_profile:character_a",
        )
    except WikiCommitPlanningError as exc:
        assert "has no title" in str(exc)
    else:
        raise AssertionError("Titleless related event must be rejected")

    # event_titles에 있으면 통과한다(owner/evidence_source 검사까지 정상 진행).
    _validate_memory_creation_authority(
        creation,
        available_profile_ids=available_profile_ids,
        scene_active_profile_ids=set(),
        owner_document=None,
        event_ids={"event:probe-target"},
        event_titles={"event:probe-target": "Probe Target"},
        player_profile_id="",
        actor_profile_id="character_profile:character_a",
    )

async def _check_updater_programming_error_not_retried(character: WikiDocument) -> None:
    """검증 경로의 프로그래밍 오류(KeyError 등)가 재시도 없이 즉시 전파되는지 검증합니다."""
    model = Mock()
    model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps({"summary": "", "patches": []}))
    )
    with (
        patch("src.wiki.commit_planner.get_model", return_value=model),
        patch("src.wiki.commit_planner.asyncio.sleep", new=AsyncMock()),
        patch(
            "src.wiki.commit_planner.validate_updater_result",
            side_effect=KeyError("simulated programming bug"),
        ),
    ):
        try:
            await plan_pending_commit(
                documents=[character],
                user_input="상황을 지켜본다.",
                actor_response="평범한 하루였다.",
                model_name="test-updater",
                max_attempts=3,
            )
        except KeyError:
            pass
        except WikiCommitPlanningError:
            raise AssertionError(
                "A programming error must propagate as itself, not be retried into "
                "WikiCommitPlanningError"
            )
        else:
            raise AssertionError("A programming error inside validation must propagate")
    assert model.generate_content_async.await_count == 1

async def _check_severable_owner_authority_creation(
    character: WikiDocument,
    scene: WikiDocument,
) -> None:
    """유효 patch와, 실재하지만 현재 장면에는 없는 owner의 Memory creation이
    섞이면 patch는 통과하고 creation만 절단되며, 재시도가 없고, attempt별
    진단 파일에 절단 기록이 남는지 검증한다(BUGFIX-multichar-update 수정 1).
    여기서 쓰는 기본 `scene` fixture는 캐릭터 B를 전혀 언급하지 않으므로
    B는 실재하는 활성 thread profile이되 장면 비활성 인물로 남는다 — 수정
    2(scene_active_profile_ids) 이후에도 절단 경로를 계속 검증한다."""
    event = WikiDocument(
        path="events/existing-outage.md",
        revision=document_revision(_EVENT_DOCUMENT),
        content=_EVENT_DOCUMENT,
        metadata=parse_frontmatter(_EVENT_DOCUMENT),
    )
    character_b = _character_b_document()
    # 굵은 시각/장소 헤더로 시작하지 않는다 - synchronize_accepted_header가
    # 장면 문서에 자동으로 patch를 하나 더 추가해 patch 수 단정이 흔들린다.
    actor_response = (
        "캐릭터 A는 계단을 올라 숨이 차고 피곤해졌다. "
        "캐릭터 B는 그 정전을 목격하고 조용히 지켜보았다."
    )
    mixed_payload = {
        "summary": "유효 patch와 비인가 owner Memory 혼합",
        "patches": [{
            "document": character.path,
            "base_revision": character.revision,
            "section_path": ["현재 상태", "신체 상태와 감정 상태"],
            "replacement_markdown": (
                "### 신체 상태와 감정 상태\n\n"
                "- 신체 상태: 계단을 올라 숨이 차고 피곤하다.\n"
                "- 감정 상태: 평온"
            ),
            "evidence": "캐릭터 A는 계단을 올라 숨이 차고 피곤해졌다.",
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
        "creations": [{
            "document_type": "memory",
            "document_id": "memory:character-b-remembers-outage",
            "title": "Character B Remembers the Outage",
            "owner": "character_profile:character_b",
            "related_event_id": "event:existing-outage",
            "formation_trigger": "정전을 목격했다.",
            "formed_at": "2026-07-23 13:10",
            "location": "대학 도서관",
            "remembered_content": "캐릭터 B는 정전이 났던 순간을 기억한다.",
            "interpretation": "특별한 해석은 없다.",
            "emotion": "약간의 불안.",
            "certainty": "높음.",
            "distortion_risk": "낮음.",
            "evidence": "캐릭터 B는 그 정전을 목격하고 조용히 지켜보았다.",
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    model = Mock()
    model.generate_content_async = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(mixed_payload, ensure_ascii=False))
    )
    with TemporaryDirectory() as debug_directory:
        debug_root = Path(debug_directory)
        with (
            patch("src.wiki.commit_planner.get_model", return_value=model),
            patch("src.wiki.commit_planner.asyncio.sleep", new=AsyncMock()),
        ):
            pending = await plan_pending_commit(
                documents=[character, scene, event, character_b],
                user_input="상황을 지켜본다.",
                actor_response=actor_response,
                model_name="test-updater",
                max_attempts=3,
                actor_profile_id="character_profile:character_a",
                debug_root=debug_root,
            )
        # 절단이 재시도를 유발하지 않는다 - 모델 호출은 정확히 한 번이다.
        assert model.generate_content_async.await_count == 1
        assert pending.updater_attempts == 1
        assert len(pending.patches) == 1
        assert pending.patches[0].section_path == ("현재 상태", "신체 상태와 감정 상태")
        assert pending.creations == []
        assert len(pending.severed_creations) == 1
        severed = pending.severed_creations[0]
        assert severed.document_id == "memory:character-b-remembers-outage"
        assert severed.document_type == "memory"
        assert severed.owner == "character_profile:character_b"
        assert "not present in the current scene" in severed.reason

        run_directories = [path for path in debug_root.iterdir() if path.is_dir()]
        assert len(run_directories) == 1
        severed_file = run_directories[0] / "attempt_01_severed.txt"
        assert severed_file.exists()
        severed_text = severed_file.read_text(encoding="utf-8")
        assert "memory:character-b-remembers-outage" in severed_text
        assert "character_profile:character_b" in severed_text
        result = json.loads((run_directories[0] / "result.json").read_text(encoding="utf-8"))
        assert result["status"] == "accepted"
        assert result["attempts"] == 1

async def _check_severed_creation_triggers_fatal_cross_validation(
    character: WikiDocument,
    scene: WikiDocument,
) -> None:
    """절단으로 남은 creation 집합이 Event-Memory 짝 규칙을 어기면 attempt
    전체가 여전히 치명으로 기각되는지 검증한다 - 절단이 교차 검증까지
    관대해지는 연쇄 완화가 아님을 확인한다."""
    character_b = _character_b_document()
    evidence = "캐릭터 B가 도서관에서 프린터 오류를 목격했다."
    payload = {
        "summary": "새 사건과 비인가 owner Memory만 함께 생성",
        "patches": [],
        "creations": [
            {
                "document_type": "event",
                "document_id": "event:library-print-error",
                "title": "Library Print Error",
                "occurred_at": "2026-07-23 13:12",
                "location": "대학 도서관",
                "participants": [],
                "witnesses": ["캐릭터 B"],
                "facts": ["프린터 오류로 자료 출력이 중단됐다."],
                "direct_results": ["임시로 다른 프린터를 찾아야 했다."],
                "lasting_effects": ["출력 지연이 발표 준비에 영향을 남겼다."],
                "evidence": evidence,
                "evidence_source": "actor_response",
                "confidence": 0.92,
            },
            {
                "document_type": "memory",
                "document_id": "memory:character-b-remembers-print-error",
                "title": "Character B Remembers the Print Error",
                "owner": "character_profile:character_b",
                "related_event_id": "event:library-print-error",
                "formation_trigger": "프린터가 갑자기 멈췄다.",
                "formed_at": "2026-07-23 13:12",
                "location": "대학 도서관",
                "remembered_content": "캐릭터 B는 프린터가 멈췄던 순간을 기억한다.",
                "interpretation": "특별한 해석은 없다.",
                "emotion": "약간의 짜증.",
                "certainty": "높음.",
                "distortion_risk": "낮음.",
                "evidence": evidence,
                "evidence_source": "actor_response",
                "confidence": 0.9,
            },
        ],
    }
    await _expect_update_rejected(
        [character, scene, character_b],
        payload,
        "상황을 지켜본다.",
        evidence,
        "Missing matching Memory for created Event(s): event:library-print-error",
        actor_profile_id="character_profile:character_a",
    )

async def _check_severed_creation_archive_and_canonical_isolation() -> None:
    """절단 기록이 적용된 commit archive 메타데이터에는 남고 canonical
    Markdown 본문에는 노출되지 않는지, 독립된 vault에서 적용까지 검증한다."""
    with TemporaryDirectory() as temporary_directory:
        store, character, scene = create_base_store(Path(temporary_directory))
        character_b = _character_b_document()
        evidence = "캐릭터 A는 복도의 소음을 들었고, 캐릭터 B는 그 소리를 목격했다."
        payload = {
            "summary": "정당한 사건/기억과 비인가 owner 기억 혼합",
            "patches": [],
            "creations": [
                {
                    "document_type": "event",
                    "document_id": "event:hallway-noise",
                    "title": "Hallway Noise",
                    "occurred_at": "2026-07-23 13:15",
                    "location": "대학 도서관 복도",
                    "participants": ["캐릭터 A"],
                    "witnesses": ["캐릭터 B"],
                    "facts": ["복도에서 갑작스러운 소음이 발생했다."],
                    "direct_results": ["두 사람 모두 소리 쪽을 돌아봤다."],
                    "lasting_effects": ["복도 소음이 짧게 기억에 남았다."],
                    "evidence": evidence,
                    "evidence_source": "actor_response",
                    "confidence": 0.93,
                },
                {
                    "document_type": "memory",
                    "document_id": "memory:character-a-remembers-noise",
                    "title": "Character A Remembers the Noise",
                    "owner": "character_profile:character_a",
                    "related_event_id": "event:hallway-noise",
                    "formation_trigger": "복도에서 소음이 들렸다.",
                    "formed_at": "2026-07-23 13:15",
                    "location": "대학 도서관 복도",
                    "remembered_content": "캐릭터 A는 복도의 소음을 기억한다.",
                    "interpretation": "별일 아니라고 생각했다.",
                    "emotion": "약간의 놀람.",
                    "certainty": "높음.",
                    "distortion_risk": "낮음.",
                    "evidence": evidence,
                    "evidence_source": "actor_response",
                    "confidence": 0.9,
                },
                {
                    "document_type": "memory",
                    "document_id": "memory:character-b-remembers-noise",
                    "title": "Character B Remembers the Noise",
                    "owner": "character_profile:character_b",
                    "related_event_id": "event:hallway-noise",
                    "formation_trigger": "복도에서 소음이 들렸다.",
                    "formed_at": "2026-07-23 13:15",
                    "location": "대학 도서관 복도",
                    "remembered_content": "캐릭터 B는 복도의 소음을 기억한다.",
                    "interpretation": "별일 아니라고 생각했다.",
                    "emotion": "약간의 놀람.",
                    "certainty": "높음.",
                    "distortion_risk": "낮음.",
                    "evidence": evidence,
                    "evidence_source": "actor_response",
                    "confidence": 0.9,
                },
            ],
        }
        pending = await _plan_update(
            [character, scene, character_b],
            payload,
            "상황을 지켜본다.",
            evidence,
            actor_profile_id="character_profile:character_a",
            user_message_id="user-noise",
            assistant_message_id="assistant-noise",
        )
        assert pending.updater_attempts == 1
        assert {creation.document for creation in pending.creations} == {
            "events/hallway-noise.md",
            "memories/character-a-remembers-noise.md",
        }
        assert len(pending.severed_creations) == 1
        assert (
            pending.severed_creations[0].document_id
            == "memory:character-b-remembers-noise"
        )
        assert pending.severed_creations[0].owner == "character_profile:character_b"

        queue = WikiCommitQueue(store)
        queue.queue(pending)
        applied = queue.apply_pending()
        assert applied is not None
        assert len(applied.severed_creations) == 1

        archive_path = store.resolve_path(f"commits/{applied.commit_id}.md")
        archive_text = archive_path.read_text(encoding="utf-8")
        assert "memory:character-b-remembers-noise" in archive_text
        assert "character_profile:character_b" in archive_text

        for canonical_path in (
            "events/hallway-noise.md",
            "memories/character-a-remembers-noise.md",
            character.path,
        ):
            canonical_text = store.read_document(canonical_path).content
            assert "memory:character-b-remembers-noise" not in canonical_text
            assert "character_profile:character_b" not in canonical_text
            assert (
                "not present in the current scene"
                not in canonical_text
            )
        assert not store.resolve_path(
            "memories/character-b-remembers-noise.md"
        ).exists()

async def _check_scene_active_third_party_creation_authority(
    character: WikiDocument,
) -> None:
    """장면 활성 제3자(B) owner Memory의 새 권한 규칙 3가지를 검증한다
    (BUGFIX-multichar-update 수정 2): B가 언급된 evidence면 통과·commit에
    포함되고, evidence가 B를 언급하지 않거나 evidence_source가
    `player_input`이면 (장면에 없다는 뜻이 아니라 모델이 근거를 잘못 골랐다는
    뜻이므로) 절단이 아니라 치명으로 재시도된다."""
    event = WikiDocument(
        path="events/existing-outage.md",
        revision=document_revision(_EVENT_DOCUMENT),
        content=_EVENT_DOCUMENT,
        metadata=parse_frontmatter(_EVENT_DOCUMENT),
    )
    scene_with_b = _scene_document_with_active_b()
    character_b = _character_b_document()

    # 1) 장면 활성 B + B가 언급된 actor_response exact quote -> 통과, commit에 포함.
    evidence = "캐릭터 B는 정전이 나는 순간을 옆에서 지켜보았다."
    payload = {
        "summary": "장면 활성 B의 Memory 생성",
        "patches": [],
        "creations": [{
            "document_type": "memory",
            "document_id": "memory:character-b-remembers-outage-scene",
            "title": "Character B Remembers the Outage",
            "owner": "character_profile:character_b",
            "related_event_id": "event:existing-outage",
            "formation_trigger": "정전을 옆에서 지켜봤다.",
            "formed_at": "2026-07-23 13:10",
            "location": "대학 도서관",
            "remembered_content": "캐릭터 B는 정전이 났던 순간을 기억한다.",
            "interpretation": "특별한 해석은 없다.",
            "emotion": "약간의 불안.",
            "certainty": "높음.",
            "distortion_risk": "낮음.",
            "evidence": evidence,
            "evidence_source": "actor_response",
            "confidence": 0.9,
        }],
    }
    pending = await _plan_update(
        [character, scene_with_b, event, character_b],
        payload,
        "상황을 지켜본다.",
        evidence,
        actor_profile_id="character_profile:character_a",
    )
    assert pending.severed_creations == []
    assert {creation.document for creation in pending.creations} == {
        "memories/character-b-remembers-outage-scene.md"
    }

    # 2) 장면 활성 B + evidence가 B를 언급하지 않음 -> 치명(재시도), 절단 아님.
    unnamed_evidence = "정전이 갑자기 발생했다."
    unnamed_payload = {
        "summary": "장면 활성 B의 Memory 생성 (근거에 B 언급 없음)",
        "patches": [],
        "creations": [{
            **payload["creations"][0],
            "document_id": "memory:character-b-remembers-outage-unnamed",
            "evidence": unnamed_evidence,
        }],
    }
    await _expect_update_rejected(
        [character, scene_with_b, event, character_b],
        unnamed_payload,
        "상황을 지켜본다.",
        unnamed_evidence,
        "Memory evidence does not name owner character_profile:character_b",
        actor_profile_id="character_profile:character_a",
    )

    # 3) 장면 활성 B + evidence_source가 player_input -> 치명(재시도).
    player_sourced_payload = {
        "summary": "장면 활성 B의 Memory 생성 (evidence_source 오류)",
        "patches": [],
        "creations": [{
            **payload["creations"][0],
            "document_id": "memory:character-b-remembers-outage-wrong-source",
            "evidence": evidence,
            "evidence_source": "player_input",
        }],
    }
    await _expect_update_rejected(
        [character, scene_with_b, event, character_b],
        player_sourced_payload,
        evidence,
        "상황을 지켜본다.",
        "Memory owner character_profile:character_b requires actor_response evidence",
        actor_profile_id="character_profile:character_a",
    )

def _build_policy_documents(
    character: WikiDocument,
) -> tuple[WikiDocument, WikiDocument, WikiDocument, WikiDocument, WikiDocument]:
    """Build the event, memory, npc, and relationship fixtures used by the policy suite."""
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
    return event, ongoing_event, memory, npc, relationship

async def _check_static_and_event_creation_policy(
    character: WikiDocument,
    scene: WikiDocument,
    event: WikiDocument,
) -> None:
    """Validate static-section and event-creation policy guards."""
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
        "Gameplay updater cannot modify static character sections",
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
    await _expect_update_rejected(
        [character, scene],
        event_without_memory_payload,
        "상황을 지켜본다.",
        "NPC는 도서관 복도에서 캐릭터 A에게 오래 숨겨 온 마음을 고백했다.",
        "Missing matching Memory for created Event(s): event:library-confession. "
        "Each created Event requires at least one Memory created in the same "
        "response with `related_event_id` equal to the Event `document_id`.",
        actor_profile_id="character_profile:character_a",
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
    no_event_pending = await _plan_update(
        [character, scene],
        no_event_payload,
        "상황을 지켜본다.",
        "캐릭터 A가 잠시 숨을 골랐다.",
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
        "Memory owner character_profile:character_a requires player_input evidence",
        player_profile_id="character_profile:character_a",
    )

async def _check_event_progress_policy(
    character: WikiDocument,
    scene: WikiDocument,
    event: WikiDocument,
    ongoing_event: WikiDocument,
) -> None:
    """Validate allowed and rejected event progress updates."""
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
    event_progress_pending = await _plan_update(
        [character, scene, ongoing_event],
        event_progress_patch_payload,
        "기록을 본다.",
        "참가자들은 잃어버린 파일을 찾아 수색을 끝냈다.",
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
    await _expect_update_rejected(
        [character, scene, event],
        event_identity_patch_payload,
        "기록을 본다.",
        "도서관 정전은 오후 두 시였다.",
        "event updates may modify only the '진행 상태' section: events/existing-outage.md",
    )

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
    await _expect_update_rejected(
        [character, scene, event],
        event_reopen_patch_payload,
        "기록을 본다.",
        "정전이 다시 이어지는 듯했다.",
        "Event progress cannot reopen a concluded record: events/existing-outage.md",
    )

async def _check_event_progress_format_guards(
    character: WikiDocument,
    scene: WikiDocument,
    event: WikiDocument,
) -> None:
    """Validate event progress status-line formatting guards."""
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
    await _expect_update_rejected(
        [character, scene, event],
        event_missing_status_patch_payload,
        "기록을 본다.",
        "정전 기록을 다시 요약했다.",
        "event progress must include exactly one '- 상태:' line with "
        "'ongoing' or 'concluded': events/existing-outage.md",
    )

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
    await _expect_update_rejected(
        [character, scene, event],
        event_invalid_status_patch_payload,
        "기록을 본다.",
        "정전 기록 상태를 잘못 적었다.",
        "event progress must include exactly one '- 상태:' line with "
        "'ongoing' or 'concluded': events/existing-outage.md",
    )

async def _check_memory_and_character_patch_policy(
    character: WikiDocument,
    scene: WikiDocument,
    event: WikiDocument,
    memory: WikiDocument,
) -> None:
    """Validate memory mutability and player-state guards."""
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
    await _expect_update_rejected(
        [character, scene, event, memory],
        memory_patch_payload,
        "정전 기억이 점점 더 수상하게 느껴진다.",
        "캐릭터 A는 잠시 침묵했다.",
        "Gameplay updater cannot patch memory documents; gated memory distortion "
        "owns their mutable section: memories/existing-outage-memory.md",
        player_profile_id="character_profile:character_a",
    )

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
        "Player character state requires evidence from Player Input",
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
        "Active character location/activity belongs in scene/current.md",
        actor_profile_id="character_profile:character_a",
    )

async def _check_scene_evidence_policy(
    character: WikiDocument,
    scene: WikiDocument,
) -> None:
    """Validate scene patch evidence and mixed-authority handling."""
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
        "scene updates must replace the complete current-scene H2 section",
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
        "Actor-sourced scene patch can add player or shared movement only "
        "when player_evidence is an exact quote from Player Input",
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
    mixed_pending = await _plan_update(
        [character, scene],
        mixed_scene_patch,
        "나는 창밖을 보며 날씨가 좋다고 말했다.",
        "NPC는 캐릭터 A의 시선을 따라 창밖을 바라봤다.",
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
        "Updater evidence is not an exact quote from actor_response: characters/character_a.md",
    )

async def _check_relationship_and_scene_policy(
    character: WikiDocument,
    scene: WikiDocument,
    npc: WikiDocument,
    relationship: WikiDocument,
) -> None:
    """Validate relationship ownership and whole-scene patch rules."""
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
        "Actor-sourced relationship patch cannot establish player action or internal state",
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
        "relationship changes require Actor Response evidence",
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
    relationship_pending = await _plan_update(
        [character, npc, scene, relationship],
        valid_relationship_patch,
        "열쇠를 보관하겠다고 말한다.",
        "NPC는 숨겨 둔 열쇠를 맡기며 처음으로 전적인 신뢰를 보였다.",
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
        "relationship updates must preserve every accepted durable change",
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
    pending = await _plan_update(
        [character, scene],
        valid_scene_patch,
        "밖으로 가자.",
        "13시 5분, NPC는 캐릭터 A에게 인쇄소에 함께 가자고 제안했다.",
        player_profile_id="character_profile:character_a",
    )
    assert len(pending.patches) == 1
    assert pending.patches[0].section_path == ("시작 기준",)

async def _check_scene_active_relationship_patch_authority(
    character: WikiDocument,
    scene: WikiDocument,
    npc: WikiDocument,
) -> None:
    """Validate relationship-patch authority for a scene-active non-Actor owner (B).

    Mirrors the scene-active third-party creation-authority checks
    (`_check_scene_active_third_party_creation_authority`) but for a
    relationship-document patch instead of a Memory/Goal/Item/Secret
    creation: B's own relationship-to-player ledger may be patched only
    while B is scene-active, and only with `actor_response` evidence that
    names B.
    """
    character_b = _character_b_document()
    scene_with_b = _scene_document_with_active_b()
    relationship_b = _relationship_b_document()

    named_evidence = "캐릭터 B는 구조된 뒤 처음으로 전적인 신뢰를 보였다."
    valid_b_relationship_patch = {
        "summary": "B의 관계 변화 기록",
        "patches": [{
            "document": relationship_b.path,
            "base_revision": relationship_b.revision,
            "section_path": ["Relationship Development"],
            "replacement_markdown": (
                "## Relationship Development\n\n"
                "### Accepted Durable Changes\n\n"
                "- 캐릭터 B now trusts the player after the rescue."
            ),
            "evidence": named_evidence,
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    pending = await _plan_update(
        [character, npc, character_b, scene_with_b, relationship_b],
        valid_b_relationship_patch,
        "구조를 돕는다.",
        named_evidence,
        player_profile_id="character_profile:character_a",
        actor_profile_id="character_profile:npc",
    )
    assert pending.patches[0].document == relationship_b.path

    unnamed_evidence = "그는 구조된 뒤 처음으로 전적인 신뢰를 보였다."
    unnamed_b_relationship_patch = {
        "summary": "이름 없는 근거로 B 관계 변경",
        "patches": [{
            "document": relationship_b.path,
            "base_revision": relationship_b.revision,
            "section_path": ["Relationship Development"],
            "replacement_markdown": (
                "## Relationship Development\n\n"
                "### Accepted Durable Changes\n\n"
                "- Trusts the player after the rescue."
            ),
            "evidence": unnamed_evidence,
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        [character, npc, character_b, scene_with_b, relationship_b],
        unnamed_b_relationship_patch,
        "구조를 돕는다.",
        unnamed_evidence,
        "relationship evidence does not name owner",
        player_profile_id="character_profile:character_a",
        actor_profile_id="character_profile:npc",
    )

    inactive_b_relationship_patch = {
        "summary": "비활성 B 관계 변경 시도",
        "patches": [{
            "document": relationship_b.path,
            "base_revision": relationship_b.revision,
            "section_path": ["Relationship Development"],
            "replacement_markdown": (
                "## Relationship Development\n\n"
                "### Accepted Durable Changes\n\n"
                "- 캐릭터 B now trusts the player after the rescue."
            ),
            "evidence": named_evidence,
            "evidence_source": "actor_response",
            "confidence": 0.95,
        }],
    }
    await _expect_update_rejected(
        # 기본 scene은 캐릭터 A만 언급하므로(scene_with_b와 달리) B는 장면 비활성이다.
        [character, npc, character_b, scene, relationship_b],
        inactive_b_relationship_patch,
        "구조를 돕는다.",
        named_evidence,
        "relationship updates are limited to the active Actor or a "
        "scene-active character's own relationship document",
        player_profile_id="character_profile:character_a",
        actor_profile_id="character_profile:npc",
    )

async def _check_update_policy(
    character: WikiDocument,
    scene: WikiDocument,
) -> None:
    """Run the full updater policy suite."""
    event, ongoing_event, memory, npc, relationship = _build_policy_documents(character)
    await _check_static_and_event_creation_policy(character, scene, event)
    await _check_event_progress_policy(character, scene, event, ongoing_event)
    await _check_event_progress_format_guards(character, scene, event)
    await _check_memory_and_character_patch_policy(character, scene, event, memory)
    await _check_scene_evidence_policy(character, scene)
    await _check_relationship_and_scene_policy(
        character,
        scene,
        npc,
        relationship,
    )
    await _check_scene_active_relationship_patch_authority(character, scene, npc)

async def run_policy_suite(
    character: WikiDocument,
    scene: WikiDocument,
) -> PendingWikiCommit:
    """Run the full updater policy smoke suite and return the retried pending commit."""
    await _check_retry_exhaustion(character)
    await _check_memory_related_event_authority_messages(character, scene)
    _check_memory_creation_authority_invariant()
    await _check_updater_programming_error_not_retried(character)
    await _check_severable_owner_authority_creation(character, scene)
    await _check_severed_creation_triggers_fatal_cross_validation(character, scene)
    await _check_severed_creation_archive_and_canonical_isolation()
    await _check_scene_active_third_party_creation_authority(character)
    await _check_update_policy(character, scene)
    return await _generate_with_one_retry(character)

def main() -> None:
    """Run the standalone policy smoke suite."""
    with TemporaryDirectory() as temporary_directory:
        _store, character, scene = create_base_store(Path(temporary_directory))
        asyncio.run(run_policy_suite(character, scene))

    print("smoke_wiki_policy: ok")

if __name__ == "__main__":
    main()
