# ================================
# src/wiki/__init__.py
#
# Wiki V2 Markdown 저장소와 지연 업데이트 공개 API를 제공합니다.
#
# Classes
#   - CreateDocument : Updater의 새 event/memory/goal/item/secret 문서 요청 union
#   - CreateEventDocument : durable event 생성 요청
#   - CreateMemoryDocument : owner-private memory 생성 요청
#   - CreateGoalDocument : owner-private durable goal 생성 요청
#   - CreateItemDocument : owner-private item 생성 요청
#   - CreateSecretDocument : owner-private secret 생성 요청(knower-scoped)
#   - WikiDiagnostic : vault 문서 무결성 진단 항목
#   - WikiDocumentSummary : Explorer용 문서 요약 메타데이터
#   - WikiMigrationError : schema_version 마이그레이션 예외
#   - DocumentCreation : 검증된 새 Markdown 문서 생성
#   - DocumentDeletion : exact revision 기반 Markdown 문서 삭제
#   - DocumentReplacement : exact revision 기반 Markdown 문서 전체 교체
#   - AppliedSectionChange : 적용된 section의 before/after 원문과 hash
#   - AppliedDocumentChange : 적용된 문서 전체 교체의 before/after 원문과 hash
#   - MarkdownSection : 제목 경로로 식별되는 Markdown 섹션
#   - MarkdownStructureError : Markdown 구조 또는 섹션 패치 검증 예외
#   - PendingCommitExists : 기존 commit.md 덮어쓰기 방지 예외
#   - PendingWikiCommit : 다음 사용자 입력까지 보류되는 Wiki 변경 묶음
#   - SectionPatch : 한 섹션 교체 요청
#   - SeveredCreation : owner 권한 위반으로 절단된 독립 creation 진단 기록
#   - WikiCommitError : commit.md 직렬화·상태 예외
#   - WikiCommitQueue : commit.md 저장 및 다음 턴 적용 관리자
#   - WikiDocument : revision이 계산된 Markdown 문서
#   - WikiFrontmatterError : YAML frontmatter 구문 또는 구조 예외
#   - WikiMetadata : frontmatter 공통 메타데이터
#   - WikiPathError : vault 밖 경로 접근 예외
#   - WikiRevisionConflict : 수동 편집과 지연 커밋의 revision 충돌 예외
#   - WikiStore : Markdown vault 저장소
#   - WikiStoreError : Wiki 저장소 작업 기본 예외
#   - WikiScaffoldError : world/thread 스캐폴드 입력 또는 템플릿 예외
#   - WikiScaffoldResult : 생성된 vault와 문서 목록
#   - WikiCommitPlanningError : Wiki commit 계획 또는 재시도 소진 예외
#   - WikiUpdaterResult : Unified Updater 구조화 출력
#   - WikiContextError : Wiki 런타임 문서·식별자 계약 예외
#   - WikiConversationSetup : 앱에 전달할 Wiki 대화 초기화 정보
#   - WikiThreadRuntimeStatus : 현재 런타임 생성 thread와 이전 형식 thread 진단
#   - WikiThreadMigrationPlan : 기존 thread 상태 계약 migration 미리보기·적용 결과
#   - WikiAuditBaselineEntry : 외부 편집 비교용 canonical 문서 snapshot
#   - WikiAuditBaseline : thread canonical Markdown baseline
#   - WikiManualAuditResult : 외부 Markdown 편집 감사 결과
#   - WikiPromptBundle : 기존 PromptBuilder가 조립한 Wiki Actor prompt
#   - WikiScenePromptAsset : 장면 분류 설명과 Actor용 Markdown을 묶은 정적 씬 프롬프트
#   - WikiPromptContractError : Actor prompt의 메타데이터·세그먼트 경계 위반 예외
#   - WikiInverseConflict : inverse와 수동 편집이 겹친 section 비교 자료
#   - WikiInversePlan : applied commit의 inverse 계획 또는 적용 결과
#
# Functions
#   - apply_section_patches(document: WikiDocument, patches: list[SectionPatch]) -> str : 섹션 패치를 메모리에서 검증·적용합니다.
#   - document_revision(content: str) -> str : Markdown content revision을 계산합니다.
#   - plan_pending_commit(documents: list[WikiDocument], user_input: str, actor_response: str, model_name: str, max_attempts: int = 3, player_profile_id: str = "", actor_profile_id: str = "", user_message_id: str | None = None, assistant_message_id: str | None = None, thinking_level: str | None = None, debug_root: Path | None = None) -> PendingWikiCommit : 출처 권한을 검증하고 선택적으로 시도별 진단 자료를 남기는 Wiki commit 계획을 생성합니다.
#   - parse_frontmatter(content: str) -> WikiMetadata | None : YAML frontmatter를 검증해 반환합니다.
#   - parse_markdown_sections(content: str) -> dict[tuple[str, ...], MarkdownSection] : Markdown 섹션 경로를 파싱합니다.
#   - render_wiki_template(template_name: str, values: Mapping[str, str]) -> str : Markdown 문서 템플릿을 렌더링합니다.
#   - scaffold_thread(root: Path, thread_id: str, world_id: str, title: str) -> WikiScaffoldResult : thread vault를 생성합니다.
#   - scaffold_world(root: Path, world_id: str, display_name: str) -> WikiScaffoldResult : world vault를 생성합니다.
#   - initialize_wiki_conversation(vault_root: Path, world_id: str, scenario_id: str, thread_id: str) -> WikiConversationSetup : Wiki thread를 초기화합니다.
#   - get_wiki_thread_runtime_status(vault_root: Path, thread_id: str) -> WikiThreadRuntimeStatus : thread 런타임 세대를 진단합니다.
#   - resolve_wiki_opening_scene(vault_root: Path, world_id: str, scenario_id: str) -> str : 첫 장면 원문을 반환합니다.
#   - read_wiki_scene_descriptions(vault_root: Path, world_id: str, scenario_id: str) -> dict[str, str] : 공용 분류 설명에 Wiki 전용 scene key를 합칩니다.
#   - build_wiki_prompt_bundle(vault_root: Path, setup: WikiConversationSetup, user_input: str, recent_story: str = "", turn_ooc_directives: str = "") -> WikiPromptBundle : PromptBuilder로 Wiki prompt를 조립합니다.
#   - validate_wiki_prompt_bundle(bundle: WikiPromptBundle) -> None : 컴파일된 Actor prompt의 메타데이터·세그먼트 계약을 검증합니다.
#   - apply_pending_wiki_commit(vault_root: Path, thread_id: str) -> PendingWikiCommit | None : 다음 입력 직전 Wiki commit을 적용합니다.
#   - describe_wiki_commit_failure(exc: BaseException) -> str : 실패 예외를 compensation_errors까지 포함한 사람이 읽을 문자열로 만듭니다.
#   - diagnose_wiki_scope(vault_root: Path, thread_id: str, world_id: str) -> list[WikiDiagnostic] : 중복 ID·frontmatter·섹션 무결성을 진단합니다.
#   - list_wiki_documents(vault_root: Path, thread_id: str, world_id: str) -> list[WikiDocumentSummary] : Explorer용 문서 요약 목록을 반환합니다.
#   - migrate_document_content(content: str) -> str : 문서를 CURRENT_SCHEMA_VERSION까지 순차 업그레이드합니다.
#   - register_migration(document_type: str, from_version: int, migrate: Callable[[str], str]) -> None : 단계 마이그레이션을 등록합니다.
#   - plan_thread_contract_migration(vault_root: Path, thread_id: str) -> WikiThreadMigrationPlan : 기존 thread 상태 계약을 쓰기 없이 검사합니다.
#   - apply_thread_contract_migration(vault_root: Path, thread_id: str) -> WikiThreadMigrationPlan : 상태 계약을 audited manual commit으로 적용합니다.
#   - plan_manual_edit_audit(store: WikiStore) -> WikiManualAuditPlan : 외부 Markdown 변경을 쓰기 없이 계획합니다.
#   - ensure_audit_baseline(store: WikiStore) -> None : 없는 thread baseline만 초기화합니다.
#   - refresh_audit_baseline(store: WikiStore) -> None : 내부 canonical 변경 뒤 baseline을 갱신합니다.
#   - estimate_recall_tokens(document: WikiDocument) -> int : recall 문서의 보수적인 prompt token 비용을 추정합니다.
# ================================

from src.wiki.commit import PendingCommitExists, WikiCommitError, WikiCommitQueue
from src.wiki.commit_planner import WikiCommitPlanningError, plan_pending_commit
from src.wiki.context import (
    WikiContextError,
    get_wiki_thread_runtime_status,
    read_wiki_scene_descriptions,
)
from src.wiki.diagnostics import WikiDiagnostic, diagnose_wiki_scope
from src.wiki.explorer import WikiDocumentSummary, list_wiki_documents
from src.wiki.recall import estimate_recall_tokens, select_recall_documents
from src.wiki.frontmatter import WikiFrontmatterError, parse_frontmatter
from src.wiki.migrations import (
    CURRENT_SCHEMA_VERSION,
    WikiMigrationError,
    migrate_document_content,
    register_migration,
)
from src.wiki.markdown import (
    MarkdownStructureError,
    apply_section_patches,
    document_revision,
    parse_markdown_sections,
)
from src.wiki.models import (
    AppliedDocumentChange,
    AppliedSectionChange,
    CreateDocument,
    CreateEventDocument,
    CreateGoalDocument,
    CreateItemDocument,
    CreateMemoryDocument,
    CreateSecretDocument,
    DocumentCreation,
    DocumentDeletion,
    DocumentReplacement,
    MarkdownSection,
    PendingWikiCommit,
    SectionPatch,
    SeveredCreation,
    WikiDocument,
    WikiInverseConflict,
    WikiInversePlan,
    WikiConversationSetup,
    WikiMetadata,
    WikiPromptBundle,
    WikiScenePromptAsset,
    WikiScaffoldResult,
    WikiThreadRuntimeStatus,
    WikiThreadMigrationPlan,
    WikiAuditBaseline,
    WikiAuditBaselineEntry,
    WikiManualAuditResult,
    WikiUpdaterResult,
)
from src.wiki.scaffold import (
    WikiScaffoldError,
    render_wiki_template,
    scaffold_thread,
    scaffold_world,
)
from src.wiki.store import (
    WikiPathError,
    WikiRevisionConflict,
    WikiStore,
    WikiStoreError,
    describe_wiki_commit_failure,
)
from src.wiki.manual_audit import (
    ensure_audit_baseline,
    plan_manual_edit_audit,
    refresh_audit_baseline,
)
from src.wiki.thread_migration import (
    apply_thread_contract_migration,
    plan_thread_contract_migration,
)
from src.wiki.prompt_contract import (
    WikiPromptContractError,
    validate_wiki_prompt_bundle,
)
from src.wiki.runtime import (
    apply_pending_wiki_commit,
    build_wiki_prompt_bundle,
    initialize_wiki_conversation,
    resolve_wiki_opening_scene,
)

__all__ = [
    "AppliedDocumentChange",
    "AppliedSectionChange",
    "CreateDocument",
    "CreateEventDocument",
    "CreateGoalDocument",
    "CreateItemDocument",
    "CreateMemoryDocument",
    "CreateSecretDocument",
    "DocumentCreation",
    "DocumentDeletion",
    "DocumentReplacement",
    "WikiDiagnostic",
    "WikiDocumentSummary",
    "WikiMigrationError",
    "CURRENT_SCHEMA_VERSION",
    "diagnose_wiki_scope",
    "list_wiki_documents",
    "migrate_document_content",
    "register_migration",
    "estimate_recall_tokens",
    "select_recall_documents",
    "MarkdownSection",
    "MarkdownStructureError",
    "PendingCommitExists",
    "PendingWikiCommit",
    "SectionPatch",
    "SeveredCreation",
    "WikiCommitError",
    "WikiCommitQueue",
    "WikiContextError",
    "WikiConversationSetup",
    "WikiDocument",
    "WikiFrontmatterError",
    "WikiInverseConflict",
    "WikiInversePlan",
    "WikiMetadata",
    "WikiPathError",
    "WikiPromptBundle",
    "WikiScenePromptAsset",
    "WikiPromptContractError",
    "WikiRevisionConflict",
    "WikiScaffoldError",
    "WikiScaffoldResult",
    "WikiStore",
    "WikiStoreError",
    "WikiThreadRuntimeStatus",
    "WikiThreadMigrationPlan",
    "WikiAuditBaseline",
    "WikiAuditBaselineEntry",
    "WikiManualAuditResult",
    "WikiCommitPlanningError",
    "WikiUpdaterResult",
    "apply_section_patches",
    "apply_pending_wiki_commit",
    "apply_thread_contract_migration",
    "build_wiki_prompt_bundle",
    "describe_wiki_commit_failure",
    "document_revision",
    "get_wiki_thread_runtime_status",
    "plan_pending_commit",
    "plan_thread_contract_migration",
    "plan_manual_edit_audit",
    "ensure_audit_baseline",
    "refresh_audit_baseline",
    "read_wiki_scene_descriptions",
    "initialize_wiki_conversation",
    "parse_frontmatter",
    "parse_markdown_sections",
    "render_wiki_template",
    "resolve_wiki_opening_scene",
    "scaffold_thread",
    "scaffold_world",
    "validate_wiki_prompt_bundle",
]
