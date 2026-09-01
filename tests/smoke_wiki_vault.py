# ================================
# tests/smoke_wiki_vault.py
#
# Wiki vault smoke checks cover scaffolding, scenario context overrides, recall, migrations, diagnostics, and explorer views.
#
# Functions
#   - _check_recall() -> None : Validate recall budget trimming and ranking behavior.
#   - _check_migrations() -> None : Validate the thread migration marker write path.
#   - _check_diagnostics(vault_root: Path) -> None : Validate duplicate document and frontmatter diagnostics.
#   - _check_explorer(vault_root: Path) -> None : Validate explorer output summaries.
#   - _check_world_scaffold_contracts(root: Path, vault_root: Path, world: object, world_store: WikiStore, world_document: WikiDocument) -> None : Validate world scaffold directories and frontmatter contracts.
#   - _check_thread_scaffold_contracts(root: Path, vault_root: Path) -> WikiStore : Validate thread scaffold prerequisites and generated scene metadata.
#   - _check_template_render_contracts(vault_root: Path, world: object, world_store: WikiStore, world_document: WikiDocument, thread_store: WikiStore) -> None : Validate template rendering and scaffold overwrite guards.
#   - _check_scaffold_atomic_resume(root: Path) -> None : Validate scaffold recovery after partial creation failure.
#   - _check_commit_transaction_undo_journal(root: Path) -> None : Validate mixed create/replace/delete/patch commit rollback via WikiStore.transaction().
#   - _check_commit_transaction_rollback_failure(root: Path) -> None : Validate that a failed compensation surfaces in commit.md and describe_wiki_commit_failure(), leaving the vault honestly half-applied.
#   - _check_scaffolds(root: Path) -> None : Run the full scaffold suite.
#   - replace_frontmatter_line(content: str, key: str, value: str) -> str : Replace one scalar frontmatter line by key.
#   - insert_frontmatter_lines(content: str, extra: str) -> str : Insert extra YAML lines before the frontmatter closing marker.
#   - _build_case(root: Path, case_name: str, scenario_extra: str = "", include_scenario_character: bool = True) -> Path : Build a minimal world and scenario bundle for context checks.
#   - _check_wiki_context_scenario_overrides(root: Path) -> None : Validate optional NPC overrides and scenario character allowlists.
#   - _check_scene_active_relationship_materialization(root: Path) -> None : Validate lazy owner->player relationship materialization for a scene-active non-Actor NPC, including idempotent re-materialization.
#   - run_vault_suite(root: Path) -> None : Run the full vault smoke suite.
#   - main() -> None : Run the standalone vault smoke suite.
# ================================

from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wiki import (  # noqa: E402
    DocumentCreation,
    DocumentDeletion,
    DocumentReplacement,
    PendingWikiCommit,
    SectionPatch,
    WikiCommitQueue,
    WikiDocument,
    WikiFrontmatterError,
    WikiScaffoldError,
    WikiStore,
    WikiStoreError,
    describe_wiki_commit_failure,
    diagnose_wiki_scope,
    parse_frontmatter,
    document_revision,
    parse_markdown_sections,
    render_wiki_template,
    scaffold_thread,
    scaffold_world,
)
from src.wiki.context import (  # noqa: E402
    WikiContextError,
    _profile_documents,
    initialize_wiki_thread,
    load_wiki_setup,
)
from src.wiki.manual_audit import ensure_audit_baseline  # noqa: E402

from tests.wiki_smoke_fixtures import _SCENE_DOCUMENT, create_base_store  # noqa: E402

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

def _check_world_scaffold_contracts(
    root: Path,
    vault_root: Path,
    world: object,
    world_store: WikiStore,
    world_document: WikiDocument,
) -> None:
    """Validate world scaffold directories and frontmatter contracts."""
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

def _check_thread_scaffold_contracts(
    root: Path,
    vault_root: Path,
) -> WikiStore:
    """Validate thread scaffold prerequisites and generated scene metadata."""
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
    return thread_store

def _check_template_render_contracts(
    vault_root: Path,
    world: object,
    world_store: WikiStore,
    world_document: WikiDocument,
    thread_store: WikiStore,
) -> None:
    """Validate template rendering and scaffold overwrite guards."""
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

def _check_scaffold_atomic_resume(root: Path) -> None:
    """Validate scaffold recovery after partial creation failure."""
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

def _check_commit_transaction_undo_journal(root: Path) -> None:
    """생성·교체·삭제·patch가 섞인 commit이 중간에 실패하면 vault 전체가 적용 전 상태로 되돌아가는지 검증합니다."""
    vault_root = root / "transaction"
    store, character, scene = create_base_store(vault_root)
    old_document = store.write_document(
        "notes/old.md",
        "---\n"
        "id: event:old-note\n"
        "type: event\n"
        "schema_version: 1\n"
        "thread_id: thread_001\n"
        "visibility: [actor, updater, player]\n"
        "created_at: 2026-07-21T00:00:00+00:00\n"
        "---\n"
        "# Old Note\n\n## Content\n\n- 원래 내용\n",
    )
    # 커밋 적용 중 감사(audit) 단계가 이 초기 문서들을 "외부 변경"으로 보고 별도
    # manual commit을 만들지 않도록, patch 대상 commit을 큐에 넣기 전에 baseline을
    # 현재 상태로 먼저 고정한다.
    ensure_audit_baseline(store)

    new_document_path = "notes/new.md"
    assert not store.resolve_path(new_document_path).exists()

    job_section = parse_markdown_sections(character.content)[("기본 신상", "직업과 소속")]
    commit = PendingWikiCommit(
        user_input_hash="test-user-hash",
        actor_response_hash="test-actor-hash",
        updater_model="test-updater",
        patches=[
            SectionPatch(
                document=character.path,
                base_revision=character.revision,
                base_section_revision=document_revision(job_section.markdown),
                base_markdown=job_section.markdown,
                section_path=("기본 신상", "직업과 소속"),
                replacement_markdown="### 직업과 소속\n\n- 직업: 소설가",
                evidence="undo journal smoke test",
                evidence_source="actor_response",
                confidence=1.0,
            )
        ],
        replacements=[
            DocumentReplacement(
                document=scene.path,
                expected_revision=scene.revision,
                expected_content=scene.content,
                replacement_content=scene.content.replace("대학 도서관", "학생회관"),
            )
        ],
        deletions=[
            DocumentDeletion(
                document=old_document.path,
                expected_revision=old_document.revision,
                expected_content=old_document.content,
            )
        ],
        creations=[
            DocumentCreation(
                document=new_document_path,
                content=(
                    "---\n"
                    "id: event:new-note\n"
                    "type: event\n"
                    "schema_version: 1\n"
                    "thread_id: thread_001\n"
                    "visibility: [actor, updater, player]\n"
                    "created_at: 2026-07-21T00:00:00+00:00\n"
                    "---\n"
                    "# New Note\n\n## Content\n\n- 새 내용\n"
                ),
                evidence="undo journal smoke test",
                evidence_source="actor_response",
                confidence=1.0,
            )
        ],
    )

    queue = WikiCommitQueue(store)
    queue.queue(commit)

    # 적용 순서는 _apply_document_operations(deletions -> replacements -> creations)
    # 다음 store.apply_patches(patches)이다. write_document는 replacement 적용에서
    # 1번째로, patch 적용에서 2번째로 호출된다. 2번째 호출을 실패시키면 deletion과
    # replacement, creation은 이미 끝난 뒤에 patch만 적용되지 못한 채 commit
    # 전체가 실패하므로, 세 종류의 완료된 연산과 시작조차 못한 연산이 함께 섞인
    # "중간 실패"를 재현한다.
    original_write_document = WikiStore.write_document
    write_calls = 0

    def fail_second_write(
        target_store: WikiStore,
        relative_path: str,
        content: str,
        expected_revision: str | None = None,
    ) -> WikiDocument:
        """두 번째 write_document 호출(patch 적용)만 실패시켜 undo journal을 검증합니다."""
        nonlocal write_calls
        write_calls += 1
        if write_calls == 2:
            raise OSError("simulated mid-commit failure")
        return original_write_document(target_store, relative_path, content, expected_revision)

    with patch.object(WikiStore, "write_document", new=fail_second_write):
        try:
            queue.apply_pending()
        except WikiStoreError:
            # store.apply_patches always wraps write-loop failures as WikiStoreError,
            # even when nested inside the outer WikiCommitQueue transaction.
            pass
        else:
            raise AssertionError("Mid-commit failure must propagate")

    # 1번째: replacement 적용, 2번째: patch 적용(주입된 실패), 3번째: undo journal이
    # replacement를 되돌리는 호출(deletion 되돌리기는 create_document, creation
    # 되돌리기는 delete_document를 쓰므로 write_document를 거치지 않는다), 4번째:
    # _apply_pending_locked의 except가 commit.md에 실패 기록을 쓰는 호출
    # (transaction 밖이므로 journal에 기록되지 않는다).
    assert write_calls == 4

    restored_character = store.read_document(character.path)
    assert restored_character.content == character.content

    restored_scene = store.read_document(scene.path)
    assert restored_scene.content == scene.content

    restored_old = store.read_document(old_document.path)
    assert restored_old.content == old_document.content

    assert not store.resolve_path(new_document_path).exists()

    pending = queue.load()
    assert pending is not None
    assert pending.status == "failed"
    assert pending.failure_reason is not None
    assert "simulated mid-commit failure" in pending.failure_reason

def _check_commit_transaction_rollback_failure(root: Path) -> None:
    """원본 실패에 더해 그 보상(undo journal의 되돌리기) 자체도 실패하면, commit.md의
    failure_reason과 describe_wiki_commit_failure(exc)가 둘 다 원인과 보상 실패를
    함께 담고, 되돌리지 못한 문서가 절반만 적용된 상태로 정확히 남는지 검증합니다."""
    vault_root = root / "transaction_rollback_failure"
    store, character, scene = create_base_store(vault_root)
    ensure_audit_baseline(store)

    job_section = parse_markdown_sections(character.content)[("기본 신상", "직업과 소속")]
    commit = PendingWikiCommit(
        user_input_hash="test-user-hash-2",
        actor_response_hash="test-actor-hash-2",
        updater_model="test-updater",
        patches=[
            SectionPatch(
                document=character.path,
                base_revision=character.revision,
                base_section_revision=document_revision(job_section.markdown),
                base_markdown=job_section.markdown,
                section_path=("기본 신상", "직업과 소속"),
                replacement_markdown="### 직업과 소속\n\n- 직업: 소설가",
                evidence="undo journal rollback-failure smoke test",
                evidence_source="actor_response",
                confidence=1.0,
            )
        ],
        replacements=[
            DocumentReplacement(
                document=scene.path,
                expected_revision=scene.revision,
                expected_content=scene.content,
                replacement_content=scene.content.replace("대학 도서관", "학생회관"),
            )
        ],
    )

    queue = WikiCommitQueue(store)
    queue.queue(commit)

    # 1번째: replacement 적용(성공). 2번째: patch 적용 - 원본 실패를 주입한다.
    # 3번째: undo journal이 replacement를 되돌리려는 시도 - 이것도 실패시켜서
    # "보상 자체가 실패한" 경로(_rollback_journal의 except 분기)를 재현한다.
    # 4번째는 _apply_pending_locked의 except가 commit.md에 실패 기록을 쓰는
    # 정상 호출이므로 실패시키지 않는다.
    original_write_document = WikiStore.write_document
    write_calls = 0

    def fail_second_and_rollback_write(
        target_store: WikiStore,
        relative_path: str,
        content: str,
        expected_revision: str | None = None,
    ) -> WikiDocument:
        """patch 적용과 그 보상(replacement 되돌리기)을 모두 실패시킵니다."""
        nonlocal write_calls
        write_calls += 1
        if write_calls == 2:
            raise OSError("simulated original failure")
        if write_calls == 3:
            raise OSError("simulated rollback failure")
        return original_write_document(target_store, relative_path, content, expected_revision)

    with patch.object(WikiStore, "write_document", new=fail_second_and_rollback_write):
        try:
            queue.apply_pending()
        except WikiStoreError as exc:
            message = describe_wiki_commit_failure(exc)
            assert "simulated original failure" in message
            assert "document rollback failed" in message
            assert "simulated rollback failure" in message
        else:
            raise AssertionError("Original failure plus rollback failure must propagate")

    assert write_calls == 4

    pending = queue.load()
    assert pending is not None
    assert pending.status == "failed"
    assert pending.failure_reason is not None
    assert "simulated original failure" in pending.failure_reason
    assert "document rollback failed" in pending.failure_reason
    assert "simulated rollback failure" in pending.failure_reason

    # 보상 자체가 실패했으므로 scene/current.md는 원본으로 되돌아가지 못하고
    # forward 쓰기(replacement) 결과인 "학생회관"에 그대로 남아 있어야 한다 -
    # 이것이 이 경로의 실제 계약이다: 절반만 적용된 상태가 정직하게 드러난다.
    partially_applied_scene = store.read_document(scene.path)
    assert partially_applied_scene.content != scene.content
    assert "학생회관" in partially_applied_scene.content

    # patch는 애초에 쓰기가 시작되지도 못했으므로 character_a.md는 원본 그대로다.
    untouched_character = store.read_document(character.path)
    assert untouched_character.content == character.content

def _check_scaffolds(root: Path) -> None:
    """Run the full scaffold suite."""
    vault_root = root / "scaffold"
    world = scaffold_world(vault_root, "demo_world", "데모 월드")
    world_store = WikiStore(world.root)
    world_document = world_store.read_document("world.md")
    _check_world_scaffold_contracts(root, vault_root, world, world_store, world_document)
    thread_store = _check_thread_scaffold_contracts(root, vault_root)
    _check_template_render_contracts(
        vault_root,
        world,
        world_store,
        world_document,
        thread_store,
    )
    _check_scaffold_atomic_resume(root)

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

def _build_case(
    root: Path,
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

def _check_wiki_context_scenario_overrides(root: Path) -> None:
    """Validate optional NPC overrides and scenario character allowlists."""
    override_root = _build_case(root, 
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
    default_root = _build_case(root, "context_default")
    default_setup = load_wiki_setup(
            default_root,
            "demo_world",
            "demo",
            "thread_default",
        )
    assert default_setup.npc_id == "character_profile:world_npc"
    assert default_setup.npc_name == "World NPC"
    allowlist_root = _build_case(root, 
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
    empty_allowlist_root = _build_case(root, 
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
            invalid_root = _build_case(root, case_name, scenario_extra=scenario_extra)
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


def _check_scene_active_relationship_materialization(root: Path) -> None:
    """Validate lazy owner->player relationship materialization for a
    scene-active NPC who is neither the player nor the current Actor.

    Mirrors `_check_wiki_context_scenario_overrides`'s use of `_build_case`
    and `initialize_wiki_thread`. Thread init already materializes the
    Actor's (`world_npc`) relationship-to-player document; `Bystander` gets
    no relationship document until scene/current.md actually names them.
    Once named, `materialize_scene_active_relationships` must create exactly
    one new `bystander--pc.md` document and skip the player's own profile;
    a second call with the document already on disk must be a no-op
    (idempotent, no duplicate creation).
    """
    from datetime import datetime, timezone

    from src.wiki.context import materialize_scene_active_relationships, read_wiki_thread_documents
    from src.wiki.paths import wiki_thread_root_for_vault

    vault_root = _build_case(root, "materialize_scene_active")
    setup = initialize_wiki_thread(vault_root, "demo_world", "demo", "thread_materialize")
    thread_root = wiki_thread_root_for_vault(vault_root, setup.thread_id)
    store = WikiStore(thread_root)

    primary_relationship_path = store.resolve_path(
        f"relationships/{setup.npc_id.rsplit(':', 1)[-1]}--{setup.pc_id.rsplit(':', 1)[-1]}.md"
    )
    assert primary_relationship_path.exists(), "thread init must still materialize the Actor's ledger"
    bystander_relationship_path = store.resolve_path("relationships/bystander--pc.md")
    assert not bystander_relationship_path.exists(), "Bystander is not yet scene-active"

    scene = store.read_document("scene/current.md")
    store.write_document(
        "scene/current.md",
        scene.content + "\nBystander도 라운지에 있다.\n",
        expected_revision=scene.revision,
    )

    created_at = datetime.now(timezone.utc).isoformat()
    documents = read_wiki_thread_documents(vault_root, setup.thread_id)
    created = materialize_scene_active_relationships(
        store, documents, setup.thread_id, setup.pc_id, created_at
    )
    created_paths = {document.path for document in created}
    assert "relationships/bystander--pc.md" in created_paths
    assert not any(path.startswith("relationships/pc--") for path in created_paths), (
        "the player's own profile must never own a relationship-to-self ledger"
    )
    assert bystander_relationship_path.exists()

    rematerialized = materialize_scene_active_relationships(
        store, documents, setup.thread_id, setup.pc_id, created_at
    )
    assert rematerialized == [], "an existing relationship document must not be recreated"


def run_vault_suite(root: Path) -> None:
    """Run the full vault smoke suite."""
    _check_scaffolds(root)
    _check_commit_transaction_undo_journal(root)
    _check_commit_transaction_rollback_failure(root)
    _check_wiki_context_scenario_overrides(root)
    _check_scene_active_relationship_materialization(root)
    _check_recall()
    _check_migrations()
    _check_diagnostics(root / "scaffold")
    _check_explorer(root / "scaffold")

def main() -> None:
    """Run the standalone vault smoke suite."""
    with TemporaryDirectory() as temporary_directory:
        run_vault_suite(Path(temporary_directory))

    print("smoke_wiki_vault: ok")


if __name__ == "__main__":
    main()
