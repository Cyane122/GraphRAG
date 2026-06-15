# ================================
# src/tools/world_editor/app.py
#
# world_editor FastAPI 라우트 레이어(오케스트레이터). 세부 로직은 모듈에 위임합니다.
# 동기 핸들러를 쓰며 FastAPI가 스레드풀에서 실행하므로 블로킹 I/O가 이벤트 루프를 막지 않습니다.
# (이 도구는 엔진 파이프라인과 별개의 단독 서버라 async 래핑 없이 단순하게 둡니다.)
#
# Functions
#   - create_app() -> FastAPI : 라우트가 등록된 앱 인스턴스를 만듭니다.
# ================================

from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from src.tools.world_editor import compiler, field_types as ft_module, migrate, prompts, repair, scaffold, schedules, source_create, source_edit, worlds
from src.tools.world_editor.models import (
    AliasEditRequest,
    BlobEditRequest,
    CharacterCfgEditRequest,
    CharacterCreateRequest,
    CharacterMigrateRequest,
    EventCreateRequest,
    ExtraSlotCreateRequest,
    FieldTypeEditRequest,
    PerspectiveEditRequest,
    PromptCreateRequest,
    PromptSaveRequest,
    RelationshipEditRequest,
    RepairApplyRequest,
    ScheduleEditRequest,
    ScheduleTemplatesEditRequest,
    ScenarioCharactersRequest,
    ScenarioCreateRequest,
    ScenarioEditRequest,
    ScenarioRenameRequest,
    SceneTypesEditRequest,
    StateEditRequest,
    SubnodeAddRequest,
    SubnodeEditRequest,
    TupleCreateRequest,
    TupleRowEditRequest,
    WorldCreateRequest,
)

# world_editor.html 은 frontend/ 하위에 위치 (src/tools/world_editor/app.py 기준 parents[3])
_HTML_PATH = Path(__file__).resolve().parents[3] / "frontend" / "world_editor.html"


def create_app() -> FastAPI:
    """world_editor API/정적 페이지 라우트가 등록된 FastAPI 앱을 생성합니다."""
    app = FastAPI(title="GraphRAG World Editor", docs_url="/api/docs")

    # ── 정적 페이지 ──────────────────────────────────────────
    @app.get("/")
    def index() -> FileResponse:
        """편집기 HTML을 서빙합니다."""
        if not _HTML_PATH.exists():
            raise HTTPException(500, detail=f"world_editor.html 없음: {_HTML_PATH}")
        return FileResponse(_HTML_PATH)

    # ── 월드/시나리오 메타 ───────────────────────────────────
    @app.get("/api/worlds")
    def api_worlds() -> dict:
        """편집 가능한 월드 id 목록을 반환합니다."""
        return {"worlds": worlds.list_world_ids()}

    @app.get("/api/worlds/{world_id}/scenarios")
    def api_scenarios(world_id: str) -> dict:
        """월드의 시나리오 메타 목록을 반환합니다."""
        try:
            return {"world_id": world_id, "scenarios": worlds.scenario_infos(world_id)}
        except Exception as e:
            raise HTTPException(500, detail=f"시나리오 로드 실패: {e}")

    # ── 읽기 뷰: 컴파일된 그래프 + 편집 가능 여부 주석 ───────
    @app.get("/api/worlds/{world_id}/graph")
    def api_graph(world_id: str, scenario: str | None = Query(default=None)) -> dict:
        """월드를 임시 DB로 컴파일해 그래프를 추출하고 소스 편집 가능 여부를 주석합니다."""
        try:
            graph = compiler.compile_world_graph(world_id, scenario or None)
        except Exception as e:
            raise HTTPException(500, detail=f"월드 컴파일 실패: {e}")
        # 소스 .py AST 분석으로 editable/reason 주석 (읽기 전용, 예외를 던지지 않음)
        source_edit.annotate_graph(world_id, graph)
        return graph

    # ── 프롬프트 트리 / 읽기 / 쓰기 ─────────────────────────
    @app.get("/api/worlds/{world_id}/prompts")
    def api_prompts(world_id: str, scenario: str | None = Query(default=None)) -> dict:
        """prompt/ 트리와 씬키, 누락 경고를 반환합니다."""
        try:
            return prompts.build_prompt_tree(world_id, scenario or None)
        except Exception as e:
            raise HTTPException(500, detail=f"프롬프트 트리 로드 실패: {e}")

    @app.get("/api/worlds/{world_id}/prompt")
    def api_prompt_read(world_id: str, path: str = Query(...)) -> dict:
        """단일 프롬프트 .md 파일 내용을 반환합니다."""
        try:
            return prompts.read_prompt(world_id, path)
        except FileNotFoundError:
            raise HTTPException(404, detail=f"파일 없음: {path}")
        except ValueError as e:
            raise HTTPException(400, detail=str(e))

    @app.put("/api/worlds/{world_id}/prompt")
    def api_prompt_write(world_id: str, path: str = Query(...), body: PromptSaveRequest = Body(...)) -> dict:
        """프롬프트 .md 파일을 저장합니다."""
        try:
            return prompts.write_prompt(world_id, path, body.content)
        except ValueError as e:
            raise HTTPException(400, detail=str(e))

    @app.post("/api/worlds/{world_id}/prompt_path")
    def api_prompt_create(world_id: str, body: PromptCreateRequest) -> dict:
        """프롬프트 .md 파일 또는 폴더를 생성합니다."""
        try:
            return prompts.create_prompt_path(world_id, body.path, body.is_dir, body.content)
        except ValueError as e:
            raise HTTPException(400, detail=str(e))

    @app.delete("/api/worlds/{world_id}/prompt_path")
    def api_prompt_delete(world_id: str, path: str = Query(...)) -> dict:
        """프롬프트 파일 또는 빈 폴더를 삭제합니다."""
        try:
            return prompts.delete_prompt_path(world_id, path)
        except ValueError as e:
            raise HTTPException(400, detail=str(e))

    # ── 데이터 .py 쓰기 (AST 기반 안전 치환) ────────────────
    def _after_write(world_id: str, result: dict) -> dict:
        """쓰기 성공 시 컴파일 캐시를 무효화하고 결과를 그대로 반환합니다."""
        if result.get("ok"):
            compiler.invalidate(world_id)
        return result

    @app.post("/api/worlds/{world_id}/relationship")
    def api_edit_relationship(world_id: str, body: RelationshipEditRequest) -> dict:
        """관계 엣지를 upsert 합니다 (있으면 편집, 없으면 _RELS 에 추가)."""
        return _after_write(world_id, source_create.add_relationship(
            world_id, body.source, body.target,
            body.rel_type, body.affinity, body.trust, body.current_status,
        ))

    @app.post("/api/worlds/{world_id}/blob")
    def api_edit_blob(world_id: str, body: BlobEditRequest) -> dict:
        """캐릭터 blob(static/personality/info)을 upsert 합니다 (없으면 build_schema 에 삽입)."""
        return _after_write(world_id, source_create.set_blob(world_id, body.char_id, body.role, body.props))

    @app.post("/api/worlds/{world_id}/state")
    def api_edit_state(world_id: str, body: StateEditRequest) -> dict:
        """DynamicState 를 upsert 합니다. 정적 scenario_id 분기면 해당 branch 를 편집합니다."""
        return _after_write(world_id, source_create.set_state(world_id, body.char_id, body.fields, body.scenario_id))

    @app.post("/api/worlds/{world_id}/characters/{char_id}/subnode")
    def api_edit_subnode(world_id: str, char_id: str, body: SubnodeEditRequest) -> dict:
        """캐릭터 item/goal/secret 노드(파라미터 dict)를 편집합니다."""
        return _after_write(world_id, source_create.edit_subnode(world_id, char_id, body.node_id, body.fields))

    @app.post("/api/worlds/{world_id}/characters/{char_id}/subnode/add")
    def api_add_subnode(world_id: str, char_id: str, body: SubnodeAddRequest) -> dict:
        """캐릭터 build_schema 에 새 item/goal/secret 노드를 추가합니다."""
        return _after_write(world_id, source_create.add_subnode(world_id, char_id, body.kind, body.fields))

    @app.post("/api/worlds/{world_id}/characters/{char_id}/aliases")
    def api_edit_aliases(world_id: str, char_id: str, body: AliasEditRequest) -> dict:
        """캐릭터 별명(aliases=[...])을 전체 치환합니다."""
        return _after_write(world_id, source_create.set_aliases(world_id, char_id, body.aliases))

    @app.post("/api/worlds/{world_id}/characters/{char_id}/schedule")
    def api_edit_schedule(world_id: str, char_id: str, body: ScheduleEditRequest) -> dict:
        """캐릭터 insert_schedule kwargs 중 정적 리터럴 필드를 편집합니다."""
        return _after_write(world_id, source_edit.edit_schedule(world_id, char_id, body.schedule_id, body.fields))

    @app.post("/api/worlds/{world_id}/characters/{char_id}/schedule/rewrite")
    def api_rewrite_schedule(world_id: str, char_id: str, body: ScheduleEditRequest) -> dict:
        """insert_schedule 호출 전체를 새 필드 집합으로 재작성합니다(없던 필드 추가 포함)."""
        return _after_write(world_id, source_edit.rewrite_schedule_call(world_id, char_id, body.schedule_id, body.fields))

    @app.post("/api/worlds/{world_id}/characters/{char_id}/schedule/add")
    def api_add_schedule(world_id: str, char_id: str, body: ScheduleEditRequest) -> dict:
        """캐릭터 build_schema 에 새 insert_schedule 호출을 추가합니다."""
        return _after_write(world_id, source_create.add_schedule(world_id, char_id, body.schedule_id, body.fields))

    @app.post("/api/worlds/{world_id}/characters/{char_id}/cfg")
    def api_edit_character_cfg(world_id: str, char_id: str, body: CharacterCfgEditRequest) -> dict:
        """캐릭터 DEFAULT_CFG 또는 SCENARIO_OVERRIDES[scenario_id] 를 편집합니다."""
        return _after_write(
            world_id,
            source_edit.edit_character_cfg(world_id, char_id, body.scope, body.scenario_id, body.values),
        )

    @app.get("/api/worlds/{world_id}/characters/{char_id}/migrate-cfg")
    def api_analyze_migrate(world_id: str, char_id: str) -> dict:
        """손글씨 캐릭터를 cfg 패턴으로 변환 가능한지 분석합니다 (디스크 무변경)."""
        return migrate.analyze_character(world_id, char_id)

    @app.post("/api/worlds/{world_id}/characters/{char_id}/migrate-cfg")
    def api_migrate_cfg(world_id: str, char_id: str, body: CharacterMigrateRequest) -> dict:
        """캐릭터를 cfg 패턴으로 변환합니다. verify-by-recompile 통과 시에만 적용/preview 합니다."""
        return migrate.migrate_character(world_id, char_id, body.apply)

    @app.post("/api/worlds/{world_id}/tuple_row")
    def api_edit_tuple_row(world_id: str, body: TupleRowEditRequest) -> dict:
        """위치/규칙 튜플-행을 편집합니다 (템플릿 형태일 때만)."""
        return _after_write(world_id, source_edit.edit_tuple_row(world_id, body.kind, body.row_id, body.values))

    # ── 생성 (create) ──────────────────────────────────────────
    @app.post("/api/worlds")
    def api_create_world(body: WorldCreateRequest) -> dict:
        """새 월드 패키지를 스캐폴딩합니다."""
        return scaffold.create_world(body.world_id, body.display_name)

    @app.post("/api/worlds/{world_id}/characters")
    def api_create_character(world_id: str, body: CharacterCreateRequest) -> dict:
        """캐릭터를 생성하고 등록합니다."""
        gender = body.gender or body.biological_sex or "Female"
        return _after_write(world_id, scaffold.create_character(
            world_id, body.char_id, body.name, body.aliases, body.char_type, gender))

    @app.post("/api/worlds/{world_id}/locations")
    def api_create_location(world_id: str, body: TupleCreateRequest) -> dict:
        """위치 행을 추가합니다."""
        return _after_write(world_id, source_create.add_tuple_row(world_id, "location", body.values))

    @app.post("/api/worlds/{world_id}/rules")
    def api_create_rule(world_id: str, body: TupleCreateRequest) -> dict:
        """규칙 행을 추가합니다."""
        return _after_write(world_id, source_create.add_tuple_row(world_id, "rule", body.values))

    @app.post("/api/worlds/{world_id}/events")
    def api_create_event(world_id: str, body: EventCreateRequest) -> dict:
        """이벤트를 추가합니다."""
        return _after_write(world_id, source_create.add_event(world_id, body.event))

    # ── 시나리오 등장인물 관리 ─────────────────────────────────
    @app.get("/api/worlds/{world_id}/all_characters")
    def api_all_characters(world_id: str) -> dict:
        """월드에 정의된 모든 캐릭터 목록을 반환합니다 (시나리오 무관)."""
        return {"characters": source_create.list_all_characters(world_id)}

    @app.get("/api/worlds/{world_id}/scenarios/{scenario_id}/characters")
    def api_get_scenario_chars(world_id: str, scenario_id: str) -> dict:
        """해당 시나리오의 등장인물 char_id 목록을 반환합니다."""
        # scenario_id 가 매칭되면 그 시나리오를, 아니면 _scenario_chars_kw 가 첫 World 로 폴백한다.
        ids = source_create.get_scenario_characters(world_id, scenario_id)
        return {"scenario_id": scenario_id, "char_ids": ids}

    @app.put("/api/worlds/{world_id}/scenarios/{scenario_id}/characters")
    def api_set_scenario_chars(world_id: str, scenario_id: str, body: ScenarioCharactersRequest) -> dict:
        """해당 시나리오의 등장인물 목록을 교체합니다."""
        return _after_write(world_id, source_create.set_scenario_characters(world_id, scenario_id, body.char_ids))

    @app.post("/api/worlds/{world_id}/scenarios")
    def api_create_scenario(world_id: str, body: ScenarioCreateRequest) -> dict:
        """새 시나리오를 SCENARIOS 에 추가합니다."""
        return _after_write(world_id, source_create.create_scenario(world_id, body.scenario_id, body.display_name))

    @app.put("/api/worlds/{world_id}/scenarios/{scenario_id}")
    def api_update_scenario(world_id: str, scenario_id: str, body: ScenarioEditRequest) -> dict:
        """기존 시나리오 표시 메타를 편집합니다."""
        return _after_write(world_id, source_create.update_scenario_meta(world_id, scenario_id, body.display_name))

    @app.put("/api/worlds/{world_id}/scenarios/{scenario_id}/id")
    def api_rename_scenario(world_id: str, scenario_id: str, body: ScenarioRenameRequest) -> dict:
        """기존 시나리오 id 를 schema/prompt/override 참조에서 함께 변경합니다."""
        return _after_write(world_id, source_create.rename_scenario(world_id, scenario_id, body.new_scenario_id))

    @app.put("/api/worlds/{world_id}/scene_types")
    def api_update_scene_types(
        world_id: str,
        body: SceneTypesEditRequest,
        scenario: str | None = Query(default=None),
    ) -> dict:
        """World 클래스 또는 Scenario.scene_types 리터럴을 편집합니다."""
        return _after_write(world_id, source_create.update_scene_types(world_id, body.scene_types, scenario))

    @app.get("/api/worlds/{world_id}/scene_types/default")
    def api_default_scene_types(world_id: str) -> dict:
        """World 클래스의 기본 씬 타입 목록을 반환합니다."""
        return {"world_id": world_id, "scene_types": worlds.default_scene_types(world_id)}

    @app.put("/api/worlds/{world_id}/perspective")
    def api_update_perspective(
        world_id: str,
        body: PerspectiveEditRequest,
        scenario: str | None = Query(default=None),
    ) -> dict:
        """World 클래스 또는 시나리오 World(...)의 perspective 리터럴을 편집합니다."""
        return _after_write(world_id, source_create.update_default_perspective(world_id, body.perspective, scenario))

    @app.post("/api/worlds/{world_id}/extra_slots")
    def api_add_extra_slot(world_id: str, body: ExtraSlotCreateRequest) -> dict:
        """World EXTRA_SLOTS 에 커스텀 캐릭터 슬롯을 추가합니다."""
        return _after_write(world_id, source_create.add_extra_slot(world_id, body.slot_id, body.label, body.sub))

    @app.delete("/api/worlds/{world_id}/extra_slots/{slot_id}")
    def api_delete_extra_slot(world_id: str, slot_id: str) -> dict:
        """World EXTRA_SLOTS 에서 커스텀 슬롯을 제거합니다."""
        return _after_write(world_id, source_create.delete_extra_slot(world_id, slot_id))

    @app.get("/api/worlds/{world_id}/schedule_templates")
    def api_schedule_templates(world_id: str) -> dict:
        """World Editor 전용 전역/시나리오 schedule template JSON을 반환합니다."""
        try:
            return {"world_id": world_id, "data": schedules.read_schedule_templates(world_id)}
        except ValueError as e:
            raise HTTPException(400, detail=str(e))

    @app.put("/api/worlds/{world_id}/schedule_templates")
    def api_update_schedule_templates(world_id: str, body: ScheduleTemplatesEditRequest) -> dict:
        """World Editor 전용 전역/시나리오 schedule template JSON을 저장합니다."""
        try:
            return schedules.write_schedule_templates(world_id, body.data)
        except ValueError as e:
            raise HTTPException(400, detail=str(e))

    # ── 필드 타입 분류 ─────────────────────────────────────────
    @app.get("/api/worlds/{world_id}/field-types")
    def api_get_field_types(world_id: str) -> dict:
        """월드 필드 타입 분류(appearance/personality/other)를 반환합니다."""
        return {"world_id": world_id, "data": ft_module.read_field_types(world_id)}

    @app.patch("/api/worlds/{world_id}/field-types")
    def api_set_field_type(world_id: str, body: FieldTypeEditRequest) -> dict:
        """단일 필드의 타입 분류를 저장합니다."""
        try:
            return ft_module.write_field_type(world_id, body.section, body.field, body.type)
        except ValueError as e:
            raise HTTPException(400, detail=str(e))

    @app.delete("/api/worlds/{world_id}/field-types/{section}/{field}")
    def api_delete_field_type(world_id: str, section: str, field: str) -> dict:
        """필드 타입 분류를 삭제합니다 (섹션 기본값으로 복원됩니다)."""
        return ft_module.delete_field_type(world_id, section, field)

    # ── 삭제 (delete) ──────────────────────────────────────────
    @app.delete("/api/worlds/{world_id}/locations/{row_id}")
    def api_delete_location(world_id: str, row_id: str) -> dict:
        """위치 행을 삭제합니다."""
        return _after_write(world_id, source_create.delete_tuple_row(world_id, "location", row_id))

    @app.delete("/api/worlds/{world_id}/rules/{row_id}")
    def api_delete_rule(world_id: str, row_id: str) -> dict:
        """규칙 행을 삭제합니다."""
        return _after_write(world_id, source_create.delete_tuple_row(world_id, "rule", row_id))

    @app.delete("/api/worlds/{world_id}/events/{event_id}")
    def api_delete_event(world_id: str, event_id: str) -> dict:
        """이벤트를 삭제합니다."""
        return _after_write(world_id, source_create.delete_event(world_id, event_id))

    @app.delete("/api/worlds/{world_id}/relationship")
    def api_delete_relationship(world_id: str, source: str = Query(...), target: str = Query(...)) -> dict:
        """관계 엣지를 삭제합니다."""
        return _after_write(world_id, source_create.delete_relationship(world_id, source, target))

    # ── 진단/복구 리포트 ─────────────────────────────────────
    @app.get("/api/worlds/{world_id}/repair_report")
    def api_repair_report(world_id: str, scenario: str | None = Query(default=None)) -> dict:
        """소스 구조상 자동 편집이 막히는 지점을 진단해 반환합니다."""
        try:
            graph = compiler.compile_world_graph(world_id, scenario or None)
            source_edit.annotate_graph(world_id, graph)
            return repair.build_repair_report(world_id, scenario or None, graph)
        except Exception as e:
            raise HTTPException(500, detail=f"복구 리포트 생성 실패: {e}")

    @app.post("/api/worlds/{world_id}/repair")
    def api_repair_issue(
        world_id: str,
        body: RepairApplyRequest,
        scenario: str | None = Query(default=None),
    ) -> dict:
        """repair report 단일 항목을 diff preview 하거나 실제 적용합니다."""
        try:
            graph = compiler.compile_world_graph(world_id, scenario or None)
            source_edit.annotate_graph(world_id, graph)
            result = repair.repair_issue(
                world_id,
                scenario or None,
                graph,
                body.issue_type,
                body.scope,
                body.target,
                body.apply,
            )
            return _after_write(world_id, result) if body.apply else result
        except ValueError as e:
            raise HTTPException(400, detail=str(e))
        except Exception as e:
            raise HTTPException(500, detail=f"복구 적용 실패: {e}")

    return app


app = create_app()
