# ================================
# src/tools/world_editor/compiler.py
#
# 선택한 월드를 임시 Kuzu DB로 "컴파일"(build_schema)한 뒤 그래프를 읽어
# 읽기 전용 뷰 데이터로 추출합니다. 라이브 graph/ DB는 절대 열지 않습니다.
# 결과는 (world, scenario)별로 메모리 캐시하며 소스 쓰기 후 invalidate 합니다.
#
# Functions
#   - compile_world_graph(world_id: str, scenario_id: str | None, use_cache: bool = True) -> dict
#   - invalidate(world_id: str | None = None) -> None : 캐시 무효화 (None이면 전체)
# ================================

from __future__ import annotations

import contextlib
import gc
import io
import json
import shutil
import tempfile
from pathlib import Path

import kuzu

from src.tools.world_editor.module_cache import purge_world_modules
from src.tools.world_editor.worlds import load_world

# (world_id, scenario_id) → 컴파일된 그래프 dict
_CACHE: dict[tuple[str, str | None], dict] = {}


def invalidate(world_id: str | None = None) -> None:
    """캐시를 무효화합니다. world_id가 주어지면 해당 월드 항목만 지웁니다."""
    if world_id is None:
        _CACHE.clear()
        return
    for key in [k for k in _CACHE if k[0] == world_id]:
        _CACHE.pop(key, None)


def _props(node: dict) -> dict:
    """Kuzu 노드 dict에서 내부 키(_id/_label)를 제거합니다."""
    return {k: v for k, v in node.items() if not k.startswith("_")}


def _non_null(node: dict) -> dict:
    """내부 키를 제거하고 None 값(미설정 컬럼)도 제외합니다. 0/False는 유지."""
    return {k: v for k, v in node.items() if not k.startswith("_") and v is not None}


def _blob(node: dict | None) -> dict:
    """props JSON blob 노드를 파싱해 dict로 반환합니다. 없으면 빈 dict."""
    if not node:
        return {}
    raw = node.get("props")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def _rows(conn: kuzu.Connection, query: str, params: dict | None = None) -> list[list]:
    """쿼리를 실행해 결과 행 리스트를 반환합니다."""
    res = conn.execute(query, params or {})
    out: list[list] = []
    while res.has_next():
        out.append(res.get_next())
    return out


def _extract(conn: kuzu.Connection, world_id: str, scenario_id: str | None, extra_slots: list | None = None) -> dict:
    """컴파일된 Kuzu 그래프에서 읽기 뷰 데이터를 추출합니다."""

    # 1. 위치 — 노드 + PART_OF 상위 연결
    part_of: dict[str, list[str]] = {}
    for a, b in _rows(conn, "MATCH (a:Location)-[:PART_OF]->(b:Location) RETURN a.id, b.id"):
        part_of.setdefault(a, []).append(b)
    locations = []
    for (node,) in _rows(conn, "MATCH (l:Location) RETURN l"):
        p = _props(node)
        p["part_of"] = part_of.get(p.get("id"), [])
        locations.append(p)
    locations.sort(key=lambda x: x.get("id", ""))

    # 2. 규칙
    rules = sorted((_props(n) for (n,) in _rows(conn, "MATCH (r:Rule) RETURN r")),
                   key=lambda x: x.get("id", ""))

    # 3. 캐릭터 — 4-tier 프로파일 + 소유 항목/목표/비밀/스케줄
    def _one_blob(cid: str, rel: str, label: str) -> dict | None:
        rows = _rows(conn, f"MATCH (c:Character {{id: $id}})-[:{rel}]->(n:{label}) RETURN n", {"id": cid})
        return rows[0][0] if rows else None

    characters = []
    for (cnode,) in _rows(conn, "MATCH (c:Character) RETURN c"):
        c = _props(cnode)
        cid = c.get("id")
        state_node = _one_blob(cid, "HAS_STATE", "DynamicState")
        char_entry: dict = {
            "id": cid,
            "name": c.get("name"),
            "aliases": c.get("aliases") or [],
            "type": c.get("type"),
            "static": _blob(_one_blob(cid, "HAS_PROFILE", "StaticProfile")),
            "personality": _blob(_one_blob(cid, "HAS_PERSONALITY", "Personality")),
            "info": _blob(_one_blob(cid, "HAS_INFO", "DynamicInformation")),
            "state": _non_null(state_node) if state_node else {},
            "items": [_props(n) for (n,) in _rows(conn, "MATCH (c:Character {id:$id})-[:OWNS]->(i:Item) RETURN i", {"id": cid})],
            "goals": [_props(n) for (n,) in _rows(conn, "MATCH (c:Character {id:$id})-[:PURSUES]->(g:Goal) RETURN g", {"id": cid})],
            "secrets": [_props(n) for (n,) in _rows(conn, "MATCH (c:Character {id:$id})-[:HAS_SECRET]->(s:Secret) RETURN s", {"id": cid})],
            "schedules": [_props(n) for (n,) in _rows(conn, "MATCH (c:Character {id:$id})-[:HAS_SCHEDULE]->(s:Schedule) RETURN s", {"id": cid})],
        }
        # 커스텀 슬롯 — EXTRA_SLOTS 에 정의된 blob 노드를 slot_id 키로 추가한다.
        for slot in (extra_slots or []):
            sid, lbl = slot.get("id"), slot.get("label")
            if not sid or not lbl:
                continue
            rel_name = f"HAS_{lbl.upper()}"
            char_entry[sid] = _blob(_one_blob(cid, rel_name, lbl))
        characters.append(char_entry)
    characters.sort(key=lambda x: x.get("id", ""))

    # 4. 관계 엣지 (방향성 유지: A→B 와 B→A 는 별개)
    relationships = [
        {"source": a, "target": b, "type": t, "affinity": af, "trust": tr, "current_status": st}
        for a, b, t, af, tr, st in _rows(
            conn,
            "MATCH (a:Character)-[e:RELATIONSHIP]->(b:Character) "
            "RETURN a.id, b.id, e.type, e.affinity, e.trust, e.current_status",
        )
    ]

    # 5. 이벤트 — 참여자/장소 연결 포함
    involved: dict[str, list[str]] = {}
    for cid, eid in _rows(conn, "MATCH (c:Character)-[:INVOLVED_IN]->(e:Event) RETURN c.id, e.id"):
        involved.setdefault(eid, []).append(cid)
    ev_loc: dict[str, str] = dict(_rows(conn, "MATCH (e:Event)-[:OCCURRED_AT]->(l:Location) RETURN e.id, l.id") or [])
    events = []
    scenario_char_ids = {c.get("id") for c in characters if c.get("id")}
    for (node,) in _rows(conn, "MATCH (e:Event) RETURN e"):
        p = _props(node)
        p.pop("embedding", None)  # 벡터는 뷰에 불필요
        p["involved"] = involved.get(p.get("id"), [])
        if scenario_id and p["involved"] and not set(p["involved"]).issubset(scenario_char_ids):
            continue
        p["location_id"] = ev_loc.get(p.get("id"), "")
        events.append(p)
    events.sort(key=lambda x: x.get("timestamp", ""))

    return {
        "world_id": world_id,
        "scenario_id": scenario_id,
        "locations": locations,
        "rules": rules,
        "characters": characters,
        "relationships": relationships,
        "events": events,
    }


def compile_world_graph(world_id: str, scenario_id: str | None, use_cache: bool = True) -> dict:
    """월드를 임시 DB로 빌드한 뒤 그래프를 추출해 반환합니다.

    build_schema가 표준출력에 찍는 로그는 버퍼로 흡수합니다.
    """
    key = (world_id, scenario_id)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    # 캐시 미스(또는 invalidate 직후)에는 디스크의 최신 소스를 반영하도록 모듈 캐시를 비운다.
    purge_world_modules(world_id)
    world, _ = load_world(world_id, scenario_id)
    tmp = Path(tempfile.mkdtemp(prefix="we_compile_"))
    db = conn = None
    try:
        db = kuzu.Database(str(tmp / "db"))
        conn = kuzu.Connection(db)
        # 빌드 로그는 흡수 — 실패 시 호출부로 예외가 전파됨
        with contextlib.redirect_stdout(io.StringIO()):
            world.build_schema(conn, world.scenario_id)
            # 런타임과 동일하게 전역/시나리오 schedule 템플릿도 반영 (편집기 WYSIWYG).
            from src.assets.worlds.base import apply_schedule_templates
            apply_schedule_templates(conn, world_id, world.scenario_id)
        extra_slots = list(getattr(world, "EXTRA_SLOTS", None) or [])
        graph = _extract(conn, world_id, scenario_id, extra_slots)
        graph["extra_slots"] = extra_slots
    finally:
        del conn
        del db
        gc.collect()  # Windows에서 Kuzu 파일 핸들 해제를 보장
        shutil.rmtree(tmp, ignore_errors=True)

    _CACHE[key] = graph
    return graph
