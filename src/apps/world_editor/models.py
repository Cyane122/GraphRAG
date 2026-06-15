# ================================
# src/apps/world_editor/models.py
#
# world_editor HTTP 경계에서 주고받는 Pydantic 모델을 정의합니다.
# 디스크에 쓰는 요청(프롬프트 저장, 데이터 .py 편집)은 검증이 중요하므로 모델로 받습니다.
# 읽기 응답(월드 그래프 등)은 동적 그래프 데이터라 모듈이 조립한 dict를 그대로 반환합니다.
#
# Classes
#   - PromptSaveRequest     : 프롬프트 .md 저장 본문 (content)
#   - PromptCreateRequest   : 프롬프트 파일/폴더 생성 (path, is_dir, content)
#   - RelationshipEditRequest : 관계 엣지 편집 (source, target, rel_type, affinity, trust, current_status)
#   - BlobEditRequest       : 캐릭터 JSON blob 편집/생성 (char_id, role, props)
#   - StateEditRequest      : DynamicState 편집 (char_id, fields)
#   - TupleRowEditRequest   : 위치/규칙 등 튜플-행 편집 (kind, row_id, values)
#   - WorldCreateRequest    : 새 월드 스캐폴딩 (world_id, display_name)
#   - CharacterCreateRequest: 새 캐릭터 생성 (char_id, name, aliases, char_type, gender)
#   - TupleCreateRequest    : 위치/규칙 행 추가 (values)
#   - EventCreateRequest    : 이벤트 추가 (event)
#   - ScenarioCharactersRequest : 시나리오 등장인물 교체 (char_ids)
#   - ScenarioCreateRequest : 새 시나리오 추가 (scenario_id, display_name)
#   - ScenarioEditRequest   : 시나리오 표시 메타 편집 (display_name)
#   - ScenarioRenameRequest : 시나리오 id 변경 (new_scenario_id)
#   - SceneTypesEditRequest : 월드/시나리오 SCENE_TYPES dict 편집 (scene_types)
#   - PerspectiveEditRequest : 월드 DEFAULT_PERSPECTIVE 또는 시나리오 World(...).perspective 편집 (perspective)
#   - SubnodeEditRequest    : item/goal/secret 편집 (node_id, fields)
#   - AliasEditRequest      : 캐릭터 별명 치환 (aliases)
#   - ScheduleEditRequest   : 캐릭터 schedule kwargs 편집 (schedule_id, fields)
#   - CharacterCfgEditRequest : 캐릭터 DEFAULT_CFG / SCENARIO_OVERRIDES 편집 (scope, scenario_id, values)
#   - CharacterMigrateRequest : 손글씨 캐릭터를 cfg 패턴으로 변환 (apply)
#   - ScheduleTemplatesEditRequest : 전역/시나리오 schedule template JSON 저장 (data)
#   - RepairApplyRequest    : repair report 항목의 preview/apply 요청 (issue_type, scope, target, apply)
#   - ExtraSlotCreateRequest : 커스텀 슬롯 추가 요청 (slot_id, label, sub)
#   - FieldTypeEditRequest  : 필드 타입 분류 저장 (section, field, type)
#   - SaveResult            : 저장 결과 (ok, message, backup, formatted)
# ================================

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PromptSaveRequest(BaseModel):
    """프롬프트 .md 파일 저장 본문."""
    content: str


class PromptCreateRequest(BaseModel):
    """프롬프트 파일 또는 폴더 생성 요청."""
    path: str
    is_dir: bool = False
    content: str = ""


class RelationshipEditRequest(BaseModel):
    """관계 엣지(_RELS dict 항목) 편집 요청. None인 필드는 기존 값을 유지합니다."""
    source: str
    target: str
    rel_type: str | None = None
    affinity: int | None = None
    trust: int | None = None
    current_status: str | None = None


class BlobEditRequest(BaseModel):
    """캐릭터 JSON blob 치환/생성 요청. role ∈ {static, personality, info}."""
    char_id: str
    role: str
    props: dict[str, Any]


class StateEditRequest(BaseModel):
    """DynamicState(_state 리터럴 dict) 전체 치환 요청."""
    char_id: str
    fields: dict[str, Any]
    scenario_id: str | None = None


class TupleRowEditRequest(BaseModel):
    """위치/규칙처럼 튜플-행 리터럴 리스트의 한 행을 편집하는 요청.

    kind: "location" | "rule". row_id: 해당 행의 첫 컬럼(id). values: 컬럼명→새 값.
    """
    kind: str
    row_id: str
    values: dict[str, Any]


class WorldCreateRequest(BaseModel):
    """새 월드 스캐폴딩 요청."""
    world_id: str
    display_name: str = ""


class CharacterCreateRequest(BaseModel):
    """새 캐릭터 생성 요청. gender 로 §8 성별 기반 기본 key 구조를 결정합니다."""
    char_id: str
    name: str
    aliases: list[str] = []
    char_type: str = "npc"
    gender: str | None = None
    biological_sex: str | None = None


class TupleCreateRequest(BaseModel):
    """위치/규칙 행 추가 요청 (values: 컬럼명→값)."""
    values: dict[str, Any]


class EventCreateRequest(BaseModel):
    """이벤트 추가 요청 (event dict: id/summary/timestamp/involved/location_id 등)."""
    event: dict[str, Any]


class ScenarioCharactersRequest(BaseModel):
    """시나리오 등장인물 교체 요청 (char_ids)."""
    char_ids: list[str]


class ScenarioCreateRequest(BaseModel):
    """새 시나리오 추가 요청."""
    scenario_id: str
    display_name: str = ""


class ScenarioEditRequest(BaseModel):
    """기존 시나리오 표시 메타 편집 요청."""
    display_name: str


class ScenarioRenameRequest(BaseModel):
    """기존 시나리오 id 변경 요청."""
    new_scenario_id: str


class SceneTypesEditRequest(BaseModel):
    """World 클래스 또는 Scenario.scene_types 리터럴 치환 요청."""
    scene_types: dict[str, str]


class PerspectiveEditRequest(BaseModel):
    """World 클래스 DEFAULT_PERSPECTIVE 또는 시나리오 World(...).perspective 리터럴 치환 요청."""
    perspective: Any


class SubnodeEditRequest(BaseModel):
    """캐릭터 item/goal/secret 노드 편집 요청 (node_id 로 식별, fields 병합)."""
    node_id: str
    fields: dict[str, Any]


class SubnodeAddRequest(BaseModel):
    """캐릭터 item/goal/secret 노드 추가 요청 (kind ∈ item|goal|secret, fields 에 id 포함)."""
    kind: str
    fields: dict[str, Any]


class AliasEditRequest(BaseModel):
    """캐릭터 별명(aliases=[...]) 전체 치환 요청."""
    aliases: list[str]


class ScheduleEditRequest(BaseModel):
    """캐릭터 insert_schedule kwargs 일부 치환 요청."""
    schedule_id: str
    fields: dict[str, Any]


class CharacterCfgEditRequest(BaseModel):
    """캐릭터 DEFAULT_CFG 또는 SCENARIO_OVERRIDES[scenario_id] 치환 요청."""
    scope: str = "default"
    scenario_id: str | None = None
    values: dict[str, Any]


class CharacterMigrateRequest(BaseModel):
    """손글씨 캐릭터를 cfg 패턴으로 변환하는 요청. apply=False 면 검증+diff 만(원복)."""
    apply: bool = False


class ScheduleTemplatesEditRequest(BaseModel):
    """전역/시나리오 schedule template JSON 저장 요청."""
    data: dict[str, Any]


class RepairApplyRequest(BaseModel):
    """repair report 항목을 diff preview 하거나 실제 적용하는 요청."""
    issue_type: str
    scope: str
    target: str
    apply: bool = False


class ExtraSlotCreateRequest(BaseModel):
    """World EXTRA_SLOTS 에 커스텀 슬롯을 추가하는 요청."""
    slot_id: str
    label: str
    sub: str = ""


class FieldTypeEditRequest(BaseModel):
    """필드 타입 분류 저장 요청.

    section ∈ {static, personality, info, state}
    type ∈ {appearance, personality, other}
    """
    section: str
    field: str
    type: str


class SaveResult(BaseModel):
    """디스크 저장 결과. formatted=True면 리터럴이 재포맷되었음을 의미합니다."""
    ok: bool
    message: str = ""
    backup: str | None = None
    formatted: bool = False
