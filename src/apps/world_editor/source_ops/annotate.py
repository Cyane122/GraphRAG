# ================================
# src/apps/world_editor/source_ops/annotate.py
#
# Compiled-graph editability annotation.
#
# Functions
#   - annotate_graph(world_id: str, graph: dict) -> None : Add editability metadata to a compiled graph.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.locators import *
from src.apps.world_editor.state_normalize import (
    _coerce_state_bool_value,
    _coerce_state_int_value,
    normalize_cfg_state_values,
    normalize_state_fields,
)

def annotate_graph(world_id: str, graph: dict) -> None:
    """graph dict 를 in-place 로 순회하며 각 항목의 편집 가능 여부를 주입합니다 (절대 raise 안 함).

    relationships/characters/locations/rules 각각에 editable/reason(혹은 edit 서브dict)을
    채웁니다. 어떤 단계에서 예외가 나도 그 항목은 editable=False + 사유로 표시하고 계속 진행합니다.
    """
    pkg = world_pkg_dir(world_id)

    # 캐릭터별로 (소스 파일, 파싱 트리)를 캐시 — 같은 파일을 반복 파싱하지 않기 위함.
    file_cache: dict[str, tuple[Path, ast.Module] | None] = {}

    def _char_tree(cid: str) -> tuple[Path, ast.Module] | None:
        """char_id → (경로, ast.Module). 실패 시 None. 결과를 캐시한다."""
        if cid in file_cache:
            return file_cache[cid]
        result: tuple[Path, ast.Module] | None = None
        try:
            path = find_character_file(world_id, cid)
            if path is not None:
                tree = ast.parse(path.read_text(encoding="utf-8"))
                result = (path, tree)
        except Exception:
            result = None
        file_cache[cid] = result
        return result

    # ── 1. 관계 엣지 ────────────────────────────────────────────────
    for rel in graph.get("relationships", []):
        try:
            source = rel.get("source")
            target = rel.get("target")
            info = _char_tree(source)
            if info is None:
                rel["editable"], rel["reason"] = False, "source character file not found"
                continue
            _, tree = info
            cls = _find_character_class(tree, source)
            method = _find_method(cls, "build_relationship") if cls else None
            if method is None:
                rel["editable"], rel["reason"] = False, "build_relationship not found"
                continue
            dicts = _find_rel_dicts(method)
            node, reason = _rel_value_node_for(dicts, target)
            rel["editable"] = node is not None
            rel["reason"] = "" if node is not None else reason
        except Exception as e:  # 방어적: 어떤 경우에도 그래프 주석은 실패하지 않는다.
            rel["editable"], rel["reason"] = False, f"annotate error: {e}"

    # ── 2. 캐릭터 4-tier blob + state ───────────────────────────────
    for char in graph.get("characters", []):
        # 기본값: 전부 not editable. 아래에서 가능 항목만 True 로 갱신.
        edit = {
            "static": {"editable": False, "reason": "not analyzed"},
            "personality": {"editable": False, "reason": "not analyzed"},
            "info": {"editable": False, "reason": "not analyzed"},
            "state": {"editable": False, "reason": "not analyzed"},
        }
        char["source_file"] = None
        try:
            cid = char.get("id")
            info = _char_tree(cid)
            if info is None:
                for k in edit:
                    edit[k] = {"editable": False, "reason": "character file not found"}
                char["edit"] = edit
                continue
            path, tree = info
            # POSIX 상대 경로 — 월드 패키지 기준 (예: "characters/park_sian.py").
            char["source_file"] = path.relative_to(pkg).as_posix()

            cls = _find_character_class(tree, cid)
            method = _find_method(cls, "build_schema") if cls else None
            if cls is not None:
                char["cfg"] = _character_cfg_meta(cls, graph.get("scenario_id"))
            if method is None:
                for k in edit:
                    edit[k] = {"editable": False, "reason": "build_schema not found"}
                char["edit"] = edit
                continue

            # blob 3종: static/personality/info → 각 label 의 insert_static_inline 검사.
            for role, label in _ROLE_LABEL.items():
                call, reason = _find_blob_call(method, label)
                edit[role] = {"editable": call is not None, "reason": "" if call is not None else reason}

            # 커스텀 슬롯 (EXTRA_SLOTS) 주석.
            try:
                from src.apps.world_editor.worlds import load_world as _lw
                _w, _ = _lw(graph.get("world_id", ""), None)
                _extra_slots = list(getattr(_w, "EXTRA_SLOTS", None) or [])
            except Exception:
                _extra_slots = []
            for _slot in _extra_slots:
                _sid, _lbl = _slot.get("id"), _slot.get("label")
                if _sid and _lbl:
                    edit[_sid] = {"editable": True, "reason": ""}

            # state: build_schema body 직속 무조건 _state dict.
            state_node, state_reason = _find_state_dict(method, graph.get("scenario_id"))
            edit["state"] = {"editable": state_node is not None, "reason": "" if state_node is not None else state_reason}

            # cfg 패턴(DEFAULT_CFG 보유) 캐릭터는 blob/state 가 cfg 로 관리되므로, 편집 불가 사유를
            # 'insert_static_inline 없음' 같은 혼란스러운 메시지 대신 cfg 에디터 안내로 대체한다.
            if (char.get("cfg") or {}).get("default"):
                for role in ("static", "personality", "info", "state"):
                    if role in edit and not edit[role]["editable"]:
                        edit[role] = {"editable": False, "reason": "cfg-managed"}

            # schedules: insert_schedule kwargs 중 정적 리터럴 필드만 편집 가능.
            for schedule in char.get("schedules", []):
                call, reason = _find_schedule_call(method, cid, schedule.get("id", ""))
                if call is None:
                    schedule["edit"] = {"editable": False, "reason": reason, "fields": {}, "locked": {}}
                    continue
                meta = _schedule_edit_meta(call)
                schedule["edit"] = {
                    "editable": bool(meta["fields"]),
                    "reason": "" if meta["fields"] else "no literal editable kwargs",
                    "fields": meta["fields"],
                    "locked": meta["locked"],
                }
        except Exception as e:
            for k in edit:
                edit[k] = {"editable": False, "reason": f"annotate error: {e}"}
            for schedule in char.get("schedules", []):
                schedule["edit"] = {"editable": False, "reason": f"annotate error: {e}", "fields": {}, "locked": {}}
        char["edit"] = edit

    # ── 3. 위치 / 규칙 (schema.py 튜플-행) ──────────────────────────
    schema_tree: ast.Module | None = None
    try:
        schema_path = pkg / "schema.py"
        if schema_path.is_file():
            schema_tree = ast.parse(schema_path.read_text(encoding="utf-8"))
    except Exception:
        schema_tree = None

    for loc in graph.get("locations", []):
        try:
            if schema_tree is None:
                loc["editable"], loc["reason"] = False, "schema.py not found"
                continue
            node, reason = _find_tuple_row(schema_tree, "location", loc.get("id"))
            loc["editable"] = node is not None
            loc["reason"] = "" if node is not None else reason
            # scenarios 는 빌드타임 필터(컴파일된 노드 prop 이 아님) → 소스 튜플에서 읽어 노출한다.
            if node is not None:
                _row = _eval_tuple_columns(node, "location")
                if _row is not None:
                    loc["scenarios"] = _row.get("scenarios", [])
        except Exception as e:
            loc["editable"], loc["reason"] = False, f"annotate error: {e}"

    for rule in graph.get("rules", []):
        try:
            if schema_tree is None:
                rule["editable"], rule["reason"] = False, "schema.py not found"
                continue
            node, reason = _find_tuple_row(schema_tree, "rule", rule.get("id"))
            rule["editable"] = node is not None
            rule["reason"] = "" if node is not None else reason
            if node is not None:
                _row = _eval_tuple_columns(node, "rule")
                if _row is not None:
                    rule["scenarios"] = _row.get("scenarios", [])
        except Exception as e:
            rule["editable"], rule["reason"] = False, f"annotate error: {e}"

