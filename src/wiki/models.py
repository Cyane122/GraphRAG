# ================================
# src/wiki/models.py
#
# Wiki V2 문서, 섹션 패치, 지연 커밋 경계 모델을 정의합니다.
#
# Classes
#   - WikiMetadata : Markdown frontmatter의 공통 문서 메타데이터
#   - WikiDocument : revision이 계산된 단일 Markdown 문서
#   - WikiScenePromptAsset : 장면 분류 설명과 Actor용 Markdown을 묶은 정적 씬 프롬프트
#   - WikiScaffoldResult : 새 world/thread vault 스캐폴드 결과
#   - MarkdownSection : 제목 경로와 문자 범위를 가진 Markdown 섹션
#   - SectionPatch : 근거 출처를 포함한 특정 제목 섹션 교체 요청
#   - CreateEventDocument : Updater가 요청하는 새 durable event 문서
#   - CreateMemoryDocument : Updater가 요청하는 owner 전용 주관적 memory 문서
#   - CreateGoalDocument : Updater가 요청하는 owner 전용 durable goal 문서
#   - CreateItemDocument : Updater가 요청하는 owner 전용 item 문서
#   - CreateSecretDocument : Updater가 요청하는 owner 전용 secret 문서(knower-scoped)
#   - CreateDocument : event/memory/goal/item/secret 신규 문서 요청의 discriminated union
#   - DocumentCreation : 검증·렌더링이 끝난 새 Markdown 문서 생성
#   - SeveredCreation : owner 권한 위반으로 검증 단계에서 절단된 독립 creation 하나의 진단 기록
#   - DocumentDeletion : exact revision이 일치할 때만 수행하는 문서 삭제
#   - DocumentReplacement : exact revision 문서를 다른 완성 원문으로 교체
#   - AppliedSectionChange : 적용된 section의 before/after 원문과 hash
#   - AppliedDocumentCreation : 적용된 새 문서의 원문과 revision
#   - AppliedDocumentDeletion : 적용된 삭제 문서의 원문과 revision
#   - AppliedDocumentChange : 적용된 문서 전체 교체의 before/after 원문과 hash
#   - WikiInverseConflict : 자동 inverse가 거부된 section 비교 자료
#   - WikiInversePlan : applied commit의 inverse 가능 여부와 적용 patch
#   - WikiUpdaterResult : Unified Wiki Updater의 구조화 출력
#   - PendingWikiCommit : 다음 사용자 입력까지 보류되거나 처리 이력으로 남는 Wiki 변경 묶음
#   - WikiConversationSetup : Wiki thread 초기화에 필요한 런타임 메타데이터
#   - WikiThreadRuntimeStatus : 현재 런타임에서 생성한 thread인지 판별한 진단
#   - WikiThreadMigrationPlan : 기존 thread 상태 계약의 미리보기·적용 결과
#   - WikiAuditBaselineEntry : 외부 편집 비교용 canonical 문서 snapshot
#   - WikiAuditBaseline : thread canonical 문서 baseline
#   - WikiManualAuditResult : 외부 Markdown 편집 감사 결과
#   - WikiManualAuditPlan : 외부 편집 snapshot과 archive 후보를 묶은 내부 계획
#   - WikiPromptBundle : 기존 PromptBuilder로 조립된 세 구간과 Updater 입력 문서
# ================================

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


WikiDocumentType = Literal[
    "character",
    "character_profile",
    "event",
    "goal",
    "item",
    "location",
    "memory",
    "organization",
    "prose",
    "relationship",
    "scenario",
    "scene",
    "scene_prompt",
    "secret",
    "thread",
    "world",
]
WikiVisibility = Literal["actor", "player", "updater"]
WikiEvidenceSource = Literal["player_input", "actor_response"]


class WikiMetadata(BaseModel):
    """Markdown frontmatter의 공통 필드와 문서별 확장 필드를 보존합니다."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*(?::[A-Za-z0-9_-]+)*$")
    type: WikiDocumentType
    schema_version: Annotated[int, Field(strict=True, gt=0)]
    visibility: list[WikiVisibility] = Field(min_length=1)
    created_at: datetime
    world_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    thread_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    profile_id: str | None = None
    owner: str | None = None
    participants: list[str] | None = None

    @field_validator("visibility")
    @classmethod
    def _visibility_must_be_unique(
        cls,
        value: list[WikiVisibility],
    ) -> list[WikiVisibility]:
        """중복 없는 visibility 목록만 허용합니다."""
        if len(value) != len(set(value)):
            raise ValueError("visibility entries must be unique")
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_aware(cls, value: datetime) -> datetime:
        """UTC offset이 있는 생성 시각만 허용합니다."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value

    @model_validator(mode="after")
    def _validate_type_contract(self) -> "WikiMetadata":
        """문서 type별 ID namespace와 world/thread 소유 경계를 검증합니다."""
        world_scoped = {
            "character_profile",
            "location",
            "organization",
            "prose",
            "scenario",
            "scene_prompt",
        }
        thread_scoped = {"event", "goal", "item", "memory", "relationship", "secret"}
        expected_prefix = {
            "prose": "world:",
            "scene": "thread:",
        }.get(self.type, f"{self.type}:")
        if not self.id.startswith(expected_prefix):
            raise ValueError(f"{self.type} document id must start with {expected_prefix!r}")
        if self.type == "prose" and self.id != f"world:{self.world_id}:prose":
            raise ValueError("prose id must match world_id")
        if self.type == "scene" and not self.id.startswith(
            f"thread:{self.thread_id}:scene:"
        ):
            raise ValueError("scene id must match thread_id")
        if self.type == "scene_prompt":
            scene_type = str((self.model_extra or {}).get("scene_type") or "")
            description = str((self.model_extra or {}).get("description") or "")
            if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", scene_type) is None:
                raise ValueError("scene_prompt requires a lowercase scene_type")
            if not description.strip():
                raise ValueError("scene_prompt requires a classifier description")
        if self.type in world_scoped and self.world_id is None:
            raise ValueError(f"{self.type} requires world_id")
        if self.type in thread_scoped and self.thread_id is None:
            raise ValueError(f"{self.type} requires thread_id")
        if self.type in {"character", "scene"} and (
            self.world_id is None or self.thread_id is None
        ):
            raise ValueError(f"{self.type} requires world_id and thread_id")
        if self.type == "thread" and self.world_id is None:
            raise ValueError("thread requires world_id")
        if self.type == "character" and self.profile_id is None:
            raise ValueError("character requires profile_id")
        if self.type in {"goal", "item", "memory", "relationship", "secret"} and self.owner is None:
            raise ValueError(f"{self.type} requires owner")
        if self.type == "relationship" and (
            self.participants is None
            or len(self.participants) != 2
            or len(set(self.participants)) != 2
        ):
            raise ValueError("relationship requires two distinct participants")
        return self


class WikiDocument(BaseModel):
    """revision이 계산된 단일 Markdown 문서입니다."""

    path: str
    revision: str
    content: str
    metadata: WikiMetadata | None = None


class WikiScenePromptAsset(BaseModel):
    """분류 key·설명과 저장 원문을 함께 보존하는 정적 씬 프롬프트입니다."""

    scene_type: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str = Field(min_length=1)
    document: WikiDocument


class WikiScaffoldResult(BaseModel):
    """새로 생성된 world 또는 thread vault와 문서 목록입니다."""

    root: Path
    documents: list[WikiDocument] = Field(default_factory=list)


class MarkdownSection(BaseModel):
    """문서 안에서 제목 경로로 식별되는 Markdown 섹션입니다."""

    path: tuple[str, ...]
    level: int
    start: int
    end: int
    markdown: str


class SectionPatch(BaseModel):
    """특정 Markdown 섹션 전체를 교체하는 변경 요청입니다."""

    document: str
    base_revision: str
    base_section_revision: str | None = None
    base_markdown: str | None = None
    section_path: tuple[str, ...]
    replacement_markdown: str
    evidence: str = ""
    evidence_source: WikiEvidenceSource = "actor_response"
    player_evidence: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class _CreateDocumentBase(BaseModel):
    """Updater 신규 문서 요청의 공통 ID·근거 필드를 검증합니다."""

    document_type: Literal["event", "memory", "goal", "item", "secret"]
    document_id: str
    title: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=1)
    evidence_source: WikiEvidenceSource
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("title")
    @classmethod
    def _title_must_be_single_line(cls, value: str) -> str:
        """제목의 빈 값과 줄바꿈 기반 Markdown 주입을 거부합니다."""
        normalized = value.strip()
        if not normalized or "\n" in normalized or "\r" in normalized:
            raise ValueError("CreateDocument title must be a non-empty single line")
        return normalized


class CreateEventDocument(_CreateDocumentBase):
    """Updater가 durable event 하나를 새 문서로 만들기 위해 반환하는 구조입니다."""

    document_type: Literal["event"] = "event"
    document_id: str = Field(
        pattern=r"^event:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    occurred_at: str = Field(min_length=1, max_length=160)
    location: str = Field(min_length=1, max_length=160)
    participants: list[str] = Field(default_factory=list, max_length=20)
    witnesses: list[str] = Field(default_factory=list, max_length=20)
    facts: list[str] = Field(min_length=1, max_length=12)
    direct_results: list[str] = Field(default_factory=list, max_length=12)
    lasting_effects: list[str] = Field(default_factory=list, max_length=12)
    status: Literal["ongoing", "concluded"] = "concluded"
    progress: str = Field(
        default="The occurrence concluded in this turn.",
        min_length=1,
        max_length=240,
    )
    conclusion_time: str = Field(default="", max_length=160)

    @field_validator(
        "occurred_at",
        "location",
        "participants",
        "witnesses",
        "facts",
        "direct_results",
        "lasting_effects",
        "progress",
    )
    @classmethod
    def _values_must_be_single_line(
        cls,
        value: str | list[str],
    ) -> str | list[str]:
        """Event 필드의 빈 값과 줄바꿈 기반 Markdown 주입을 거부합니다."""
        values = [value] if isinstance(value, str) else value
        normalized = [item.strip() for item in values]
        if any(
            not item or "\n" in item or "\r" in item
            for item in normalized
        ):
            raise ValueError("CreateDocument values must be non-empty single lines")
        return normalized[0] if isinstance(value, str) else normalized

    @field_validator("conclusion_time")
    @classmethod
    def _conclusion_time_must_be_single_line(cls, value: str) -> str:
        """Event 종료 시각은 줄바꿈 없는 단일 행으로만 저장합니다."""
        normalized = value.strip()
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("CreateDocument values must be non-empty single lines")
        return normalized

    @model_validator(mode="after")
    def _normalize_progress_fields(self) -> "CreateEventDocument":
        """단일 턴 Event의 기본 진행 상태와 종료 시각을 정규화합니다."""
        if self.status == "ongoing":
            if self.conclusion_time:
                raise ValueError("Ongoing events must leave conclusion_time empty")
            return self
        if not self.conclusion_time:
            self.conclusion_time = self.occurred_at
        return self


class CreateMemoryDocument(_CreateDocumentBase):
    """Updater가 한 profile owner의 주관적 memory를 만들기 위해 반환하는 구조입니다."""

    document_type: Literal["memory"] = "memory"
    document_id: str = Field(
        pattern=r"^memory:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    owner: str = Field(
        pattern=r"^character_profile:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    related_event_id: str = Field(
        pattern=r"^event:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    formation_trigger: str = Field(min_length=1, max_length=240)
    formed_at: str = Field(min_length=1, max_length=160)
    location: str = Field(min_length=1, max_length=160)
    remembered_content: str = Field(min_length=1, max_length=600)
    interpretation: str = Field(min_length=1, max_length=400)
    emotion: str = Field(min_length=1, max_length=200)
    certainty: str = Field(min_length=1, max_length=200)
    distortion_risk: str = Field(min_length=1, max_length=240)

    @field_validator(
        "formation_trigger",
        "formed_at",
        "location",
        "remembered_content",
        "interpretation",
        "emotion",
        "certainty",
        "distortion_risk",
    )
    @classmethod
    def _memory_values_must_be_single_line(cls, value: str) -> str:
        """Memory 필드의 빈 값과 줄바꿈 기반 Markdown 주입을 거부합니다."""
        normalized = value.strip()
        if not normalized or "\n" in normalized or "\r" in normalized:
            raise ValueError("CreateDocument values must be non-empty single lines")
        return normalized


class CreateGoalDocument(_CreateDocumentBase):
    """Updater가 한 profile owner의 durable goal을 만들기 위해 반환하는 구조입니다."""

    document_type: Literal["goal"] = "goal"
    document_id: str = Field(
        pattern=r"^goal:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    owner: str = Field(
        pattern=r"^character_profile:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    desired_outcome: str = Field(min_length=1, max_length=240)
    success_look: str = Field(min_length=1, max_length=240)
    motivation: str = Field(min_length=1, max_length=240)
    priority: str = Field(min_length=1, max_length=120)
    status: Literal["active", "paused", "completed", "failed", "abandoned"] = "active"
    current_step: str = Field(min_length=1, max_length=240)
    next_action: str = Field(min_length=1, max_length=240)
    obstacles: str = Field(min_length=1, max_length=240)
    completion_conditions: str = Field(min_length=1, max_length=240)

    @field_validator(
        "desired_outcome",
        "success_look",
        "motivation",
        "priority",
        "current_step",
        "next_action",
        "obstacles",
        "completion_conditions",
    )
    @classmethod
    def _goal_values_must_be_single_line(cls, value: str) -> str:
        """Goal 필드의 빈 값과 줄바꿈 기반 Markdown 주입을 거부합니다."""
        normalized = value.strip()
        if not normalized or "\n" in normalized or "\r" in normalized:
            raise ValueError("CreateDocument values must be non-empty single lines")
        return normalized


class CreateItemDocument(_CreateDocumentBase):
    """Updater가 한 profile owner의 의미 있는 item을 만들기 위해 반환하는 구조입니다."""

    document_type: Literal["item"] = "item"
    document_id: str = Field(
        pattern=r"^item:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    owner: str = Field(
        pattern=r"^character_profile:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    kind: str = Field(min_length=1, max_length=160)
    appearance: str = Field(min_length=1, max_length=240)
    function: str = Field(min_length=1, max_length=240)
    constraint: str = Field(min_length=1, max_length=240)
    storage_location: str = Field(min_length=1, max_length=160)
    access_state: str = Field(min_length=1, max_length=160)
    status: Literal["available", "lost", "transferred", "consumed", "hidden"] = "available"
    recent_change: str = Field(min_length=1, max_length=240)

    @field_validator(
        "kind",
        "appearance",
        "function",
        "constraint",
        "storage_location",
        "access_state",
        "recent_change",
    )
    @classmethod
    def _item_values_must_be_single_line(cls, value: str) -> str:
        """Item 필드의 빈 값과 줄바꿈 기반 Markdown 주입을 거부합니다."""
        normalized = value.strip()
        if not normalized or "\n" in normalized or "\r" in normalized:
            raise ValueError("CreateDocument values must be non-empty single lines")
        return normalized


class CreateSecretDocument(_CreateDocumentBase):
    """Updater가 한 profile owner의 secret을 만들기 위해 반환하는 구조입니다.

    `knowers`는 frontmatter에 저장되는 profile ID 목록으로, Actor 컨텍스트의
    knower-scoping에만 사용된다. `who_knows`는 사람이 읽는 본문 서술이다.
    """

    document_type: Literal["secret"] = "secret"
    document_id: str = Field(
        pattern=r"^secret:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    owner: str = Field(
        pattern=r"^character_profile:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    knowers: list[str] = Field(default_factory=list, max_length=20)
    actual_content: str = Field(min_length=1, max_length=600)
    who_knows: str = Field(min_length=1, max_length=240)
    concealment: str = Field(min_length=1, max_length=240)
    status: Literal["hidden", "suspected", "revealed"] = "hidden"
    public_clue: str = Field(min_length=1, max_length=240)
    misunderstanding: str = Field(min_length=1, max_length=240)
    exposure_condition: str = Field(min_length=1, max_length=240)
    exposure_result: str = Field(min_length=1, max_length=240)

    @field_validator("knowers")
    @classmethod
    def _knowers_must_be_profile_ids(cls, value: list[str]) -> list[str]:
        """knower 목록이 중복 없는 character_profile ID인지 검증합니다."""
        for entry in value:
            if not re.fullmatch(r"character_profile:[A-Za-z0-9][A-Za-z0-9_-]{0,63}", entry):
                raise ValueError("secret knowers must be character_profile ids")
        if len(value) != len(set(value)):
            raise ValueError("secret knowers must be unique")
        return value

    @field_validator(
        "actual_content",
        "who_knows",
        "concealment",
        "public_clue",
        "misunderstanding",
        "exposure_condition",
        "exposure_result",
    )
    @classmethod
    def _secret_values_must_be_single_line(cls, value: str) -> str:
        """Secret 필드의 빈 값과 줄바꿈 기반 Markdown 주입을 거부합니다."""
        normalized = value.strip()
        if not normalized or "\n" in normalized or "\r" in normalized:
            raise ValueError("CreateDocument values must be non-empty single lines")
        return normalized


CreateDocument = Annotated[
    CreateEventDocument
    | CreateMemoryDocument
    | CreateGoalDocument
    | CreateItemDocument
    | CreateSecretDocument,
    Field(discriminator="document_type"),
]


class DocumentCreation(BaseModel):
    """검증된 commit이 배타적으로 생성할 완성된 Markdown 문서입니다."""

    document: str
    content: str
    evidence: str
    evidence_source: WikiEvidenceSource
    confidence: float = Field(ge=0.0, le=1.0)


class SeveredCreation(BaseModel):
    """검증 단계에서 owner 권한 위반으로 제거된 독립 creation 하나의 진단 기록입니다.

    치명(fatal) 위반과 달리 attempt 전체를 기각하지 않고 이 creation 하나만
    결과에서 빼는(sever) 처리를 했다는 증거다. 진단 폴더의 attempt별 파일과
    `PendingWikiCommit.severed_creations`(commit archive 메타데이터)에만
    쓰이며, Actor 프롬프트나 canonical Markdown 본문에는 절대 노출되지 않는다.
    """

    document_id: str
    document_type: Literal["event", "memory", "goal", "item", "secret"]
    owner: str
    reason: str


class DocumentDeletion(BaseModel):
    """현재 원문 revision이 일치할 때만 수행하는 audited 문서 삭제입니다."""

    document: str
    expected_revision: str
    expected_content: str


class DocumentReplacement(BaseModel):
    """현재 원문 revision이 일치할 때만 수행하는 audited 문서 전체 교체입니다."""

    document: str
    expected_revision: str
    expected_content: str
    replacement_content: str


class AppliedSectionChange(BaseModel):
    """적용된 section의 되돌리기·3-way merge 기준 원문과 hash를 보존합니다."""

    document: str
    section_path: tuple[str, ...]
    before_revision: str
    after_revision: str
    before_markdown: str
    after_markdown: str


class AppliedDocumentCreation(BaseModel):
    """적용된 새 문서의 inverse 기준 원문과 revision을 보존합니다."""

    document: str
    revision: str
    content: str


class AppliedDocumentDeletion(BaseModel):
    """적용된 삭제 문서의 보상 복구 기준 원문과 revision을 보존합니다."""

    document: str
    revision: str
    content: str


class AppliedDocumentChange(BaseModel):
    """적용된 문서 전체 교체의 inverse 기준 before/after 원문과 revision입니다."""

    document: str
    before_revision: str
    after_revision: str
    before_content: str
    after_content: str


class WikiInverseConflict(BaseModel):
    """수동 편집과 inverse 변경이 겹친 section의 비교 자료를 보존합니다."""

    document: str
    section_path: tuple[str, ...]
    reason: str
    before_markdown: str
    after_markdown: str
    current_markdown: str


class WikiInversePlan(BaseModel):
    """Applied commit을 되돌릴 수 있는지와 안전한 patch 후보를 전달합니다."""

    source_commit_id: str
    status: Literal[
        "ready",
        "applied",
        "already_reverted",
        "conflict",
        "unsupported",
    ]
    message: str
    patches: list[SectionPatch] = Field(default_factory=list)
    creations: list[DocumentCreation] = Field(default_factory=list)
    deletions: list[DocumentDeletion] = Field(default_factory=list)
    replacements: list[DocumentReplacement] = Field(default_factory=list)
    conflicts: list[WikiInverseConflict] = Field(default_factory=list)
    inverse_commit_id: str | None = None


class WikiUpdaterResult(BaseModel):
    """Unified Wiki Updater가 반환하는 변경 섹션 목록입니다."""

    summary: str = ""
    patches: list[SectionPatch] = Field(default_factory=list)
    creations: list[CreateDocument] = Field(default_factory=list)


class PendingWikiCommit(BaseModel):
    """다음 사용자 입력 시 적용하거나 명시적으로 처리할 Wiki 커밋입니다."""

    commit_id: str = Field(default_factory=lambda: uuid4().hex)
    status: Literal["pending", "failed", "applied", "skipped"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    applied_at: datetime | None = None
    user_input_hash: str
    actor_response_hash: str
    updater_model: str
    updater_attempts: int = 1
    operation: Literal["update", "inverse", "manual"] = "update"
    source_commit_id: str | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    summary: str = ""
    patches: list[SectionPatch] = Field(default_factory=list)
    creations: list[DocumentCreation] = Field(default_factory=list)
    severed_creations: list[SeveredCreation] = Field(default_factory=list)
    deletions: list[DocumentDeletion] = Field(default_factory=list)
    replacements: list[DocumentReplacement] = Field(default_factory=list)
    applied_changes: list[AppliedSectionChange] = Field(default_factory=list)
    applied_creations: list[AppliedDocumentCreation] = Field(default_factory=list)
    applied_deletions: list[AppliedDocumentDeletion] = Field(default_factory=list)
    applied_replacements: list[AppliedDocumentChange] = Field(default_factory=list)
    failure_reason: str | None = None
    resolution_reason: str | None = None


class WikiConversationSetup(BaseModel):
    """Wiki 대화 생성 시 앱 상태에 복사할 정적 런타임 정보입니다."""

    world_id: str
    scenario_id: str
    thread_id: str
    pc_id: str
    pc_name: str
    npc_id: str
    npc_name: str
    pov_mode: Literal["1p_user", "1p_char", "3p_user", "3p_char"]
    perspective: Literal[1, 3]
    rating: Literal["all_ages", "15", "r18"]
    opening_scene: str


class WikiThreadRuntimeStatus(BaseModel):
    """Thread의 현재 Wiki 런타임 표식과 호환성 진단을 전달합니다."""

    generation: Literal["current", "legacy", "missing"]
    format_version: int | None = None
    message: str


class WikiThreadMigrationPlan(BaseModel):
    """기존 thread의 런타임 상태 섹션을 보강할 안전한 수동 migration 계획입니다."""

    status: Literal["up_to_date", "ready", "applied", "conflict"]
    message: str
    changed_documents: list[str] = Field(default_factory=list)
    patches: list[SectionPatch] = Field(default_factory=list)
    migration_commit_id: str | None = None


class WikiAuditBaselineEntry(BaseModel):
    """외부 편집 비교를 위해 보관하는 한 canonical Markdown snapshot입니다."""

    revision: str
    content: str


class WikiAuditBaseline(BaseModel):
    """Thread canonical Markdown의 마지막 내부 인지 상태를 보관합니다."""

    version: Literal[1] = 1
    documents: dict[str, WikiAuditBaselineEntry] = Field(default_factory=dict)


class WikiManualAuditResult(BaseModel):
    """외부 Markdown 변경의 미리보기 또는 manual archive 기록 결과입니다."""

    status: Literal["initialized", "clean", "ready", "recorded"]
    message: str
    changed_documents: list[str] = Field(default_factory=list)
    manual_commit_id: str | None = None


class WikiManualAuditPlan(BaseModel):
    """외부 편집 비교 결과와 다음 baseline 및 manual commit 후보를 묶습니다."""

    result: WikiManualAuditResult
    baseline: WikiAuditBaseline
    commit: PendingWikiCommit | None = None


class WikiPromptBundle(BaseModel):
    """한 Wiki 턴의 Actor 프롬프트와 Updater 입력 문서를 묶습니다."""

    fixed_prompt: str
    genre_prompt: str
    dynamic_prompt: str
    scene_types: list[str]
    updater_documents: list[WikiDocument]
