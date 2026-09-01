# ================================
# tests/smoke_wiki_postprocess.py
#
# Wiki postprocess smoke checks cover memory distortion, needs decay, personality drift, organic state, and runtime-owned guards.
#
# Functions
#   - _check_memory_drift_and_needs_decay() -> tuple[list[WikiDocument], PendingWikiCommit, str] : Validate memory distortion, patch merging, needs decay, and personality drift setup.
#   - _check_unprotected_organic_state_cases(needs_documents: list[WikiDocument], trigger_pending: PendingWikiCommit) -> None : Validate direct organic-state updates without contraception inference.
#   - _check_protected_and_emergency_organic_state(needs_documents: list[WikiDocument], trigger_pending: PendingWikiCommit, needs_character_content: str) -> None : Validate contraception inference and emergency reset handling.
#   - _check_runtime_section_protection(needs_documents: list[WikiDocument]) -> None : Validate rejection of runtime-owned section patches.
#   - run_postprocess_suite() -> None : Run the full Wiki postprocess smoke suite.
#   - main() -> None : Run the standalone postprocess smoke suite.
# ================================

from __future__ import annotations

import asyncio
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
    SectionPatch,
    WikiDocument,
    document_revision,
    parse_frontmatter,
    parse_markdown_sections,
)
from src.wiki.character_postprocess import (  # noqa: E402
    plan_organic_state,
    plan_personality_drift,
)
from src.wiki.needs import plan_needs_decay  # noqa: E402
from src.wiki.postprocess import _merge_patches, plan_memory_distortion  # noqa: E402
from tests.wiki_smoke_fixtures import _expect_update_rejected  # noqa: E402

async def _check_memory_drift_and_needs_decay() -> tuple[list[WikiDocument], PendingWikiCommit, str]:
    """Validate memory distortion, patch merging, needs decay, and personality drift setup."""
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
    return needs_documents, trigger_pending, needs_character_content

async def _check_unprotected_organic_state_cases(
    needs_documents: list[WikiDocument],
    trigger_pending: PendingWikiCommit,
) -> None:
    """Validate direct organic-state updates without contraception inference."""
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

async def _check_protected_and_emergency_organic_state(
    needs_documents: list[WikiDocument],
    trigger_pending: PendingWikiCommit,
    needs_character_content: str,
) -> None:
    """Validate contraception inference and emergency reset handling."""
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

async def _check_runtime_section_protection(
    needs_documents: list[WikiDocument],
) -> None:
    """Validate rejection of runtime-owned section patches."""
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
        "Runtime-owned character section cannot be patched by the gameplay model: "
        "욕구와 컨디션",
        actor_profile_id="character_profile:character_a",
    )

async def run_postprocess_suite() -> None:
    """Run the full Wiki postprocess smoke suite."""
    needs_documents, trigger_pending, needs_character_content = (
        await _check_memory_drift_and_needs_decay()
    )
    await _check_unprotected_organic_state_cases(needs_documents, trigger_pending)
    await _check_protected_and_emergency_organic_state(
        needs_documents,
        trigger_pending,
        needs_character_content,
    )
    await _check_runtime_section_protection(needs_documents)

def main() -> None:
    """Run the standalone postprocess smoke suite."""
    asyncio.run(run_postprocess_suite())

    print("smoke_wiki_postprocess: ok")

if __name__ == "__main__":
    main()
