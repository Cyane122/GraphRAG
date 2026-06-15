# ================================
# src/apps/world_editor/worlds.py
#
# 월드 패키지를 탐색하고 schema 모듈을 "직접" import 합니다.
# src.agents.manager 경유 로더는 import 시점에 기본 Kuzu DB를 열어 락을 잡으므로 사용하지 않습니다.
# (이 도구는 graph/ 의 라이브 DB를 절대 건드리지 않고 temp DB만 씁니다.)
#
# Functions
#   - worlds_root() -> Path : src/assets/worlds 디렉터리 경로
#   - list_world_ids() -> list[str] : schema.py 를 가진 월드 id 목록
#   - load_world(world_id: str, scenario_id: str | None) -> tuple[World, list] : World 인스턴스와 Scenario 목록
#   - scenario_infos(world_id: str) -> list[dict] : 시나리오별 메타(이름/씬타입/시점/기본위치/기본시각)
#   - default_scene_types(world_id: str) -> dict[str, str] : World 클래스의 기본 씬 타입
#   - world_pkg_dir(world_id: str) -> Path : 월드 패키지 디렉터리
#   - prompt_dir(world_id: str) -> Path : 월드 prompt/ 디렉터리
# ================================

from __future__ import annotations

from datetime import datetime
import importlib
from pathlib import Path

import src.assets.worlds as _worlds_pkg
from src.assets.worlds.base import World, apply_scenario_overrides
from src.apps.world_editor.module_cache import purge_world_modules

# 월드가 아닌 패키지 멤버 (탐색에서 제외)
_NON_WORLD = {"__pycache__", "base", "base_character", "utils"}


def worlds_root() -> Path:
    """src/assets/worlds 디렉터리 경로를 반환합니다."""
    return Path(_worlds_pkg.__path__[0])


def _imports_ok(world_id: str) -> bool:
    """월드 schema 모듈이 import 되는지(=열 수 있는지) 가볍게 확인합니다."""
    try:
        importlib.import_module(f"src.assets.worlds.{world_id}.schema")
        return True
    except Exception:
        # 자체 소스 import 버그(babe_univ/ts 등)는 여기서 False 로 분류된다.
        return False


def list_world_ids() -> list[str]:
    """schema.py 를 가진 월드 id 목록을 반환합니다(이름순, 단 '열리는' 월드를 앞으로).

    UI 는 목록의 첫 항목을 기본 선택하므로, 소스 오류로 컴파일이 안 되는 월드가
    맨 앞에 와서 '세계관이 안 열린다'처럼 보이지 않도록 loadable 월드를 우선 정렬한다.
    """
    root = worlds_root()
    ids = [
        p.name
        for p in root.iterdir()
        if p.is_dir() and p.name not in _NON_WORLD and (p / "schema.py").exists()
    ]
    ids.sort()
    # 안정 정렬: import 되는 월드(0)가 안 되는 월드(1)보다 앞. 그룹 내 이름순 유지.
    ids.sort(key=lambda w: 0 if _imports_ok(w) else 1)
    return ids


def world_pkg_dir(world_id: str) -> Path:
    """월드 패키지 디렉터리 경로를 반환합니다."""
    return worlds_root() / world_id


def prompt_dir(world_id: str) -> Path:
    """월드 prompt/ 디렉터리 경로를 반환합니다 (존재 여부는 호출부 책임)."""
    return world_pkg_dir(world_id) / "prompt"


def load_world(world_id: str, scenario_id: str | None = None) -> tuple[World, list]:
    """월드 schema 모듈을 직접 import 해 World 인스턴스와 Scenario 목록을 반환합니다.

    선택 우선순위는 world_loader.load_world_instance 와 동일하게 맞춥니다.
    1) 모듈 레벨 SCENARIOS: list[Scenario] 에서 scenario_id 매칭(없으면 첫 항목)
    2) 레거시 world_instance
    3) 최후 수단으로 World 서브클래스 직접 인스턴스화
    """
    purge_world_modules(world_id)
    module = importlib.import_module(f"src.assets.worlds.{world_id}.schema")
    scenarios = getattr(module, "SCENARIOS", None)
    scenario_list = scenarios if isinstance(scenarios, list) else []

    if scenario_list:
        if scenario_id:
            for sc in scenario_list:
                if sc.scenario_id == scenario_id and sc.world is not None:
                    return apply_scenario_overrides(sc.world, sc), scenario_list
        # scenario_id 미지정/미매칭 → 첫 시나리오
        if scenario_list[0].world is not None:
            return apply_scenario_overrides(scenario_list[0].world, scenario_list[0]), scenario_list

    legacy = getattr(module, "world_instance", None)
    if isinstance(legacy, World):
        return legacy, scenario_list

    # 최후 수단: World 서브클래스를 직접 인스턴스화 (인자 없이 — 템플릿 __init__ 요건은 best-effort)
    for attr in dir(module):
        obj = getattr(module, attr)
        if isinstance(obj, type) and issubclass(obj, World) and obj is not World:
            try:
                return obj(), scenario_list
            except Exception:
                break

    return World(), scenario_list


def default_scene_types(world_id: str) -> dict[str, str]:
    """기본 World 인스턴스에 정의된 SCENE_TYPES dict를 반환합니다."""
    purge_world_modules(world_id)
    module = importlib.import_module(f"src.assets.worlds.{world_id}.schema")
    scenarios = getattr(module, "SCENARIOS", None)
    if isinstance(scenarios, list) and scenarios:
        world = getattr(scenarios[0], "world", None)
        raw = getattr(world, "SCENE_TYPES", {}) if world is not None else {}
        if isinstance(raw, dict) and raw:
            return dict(raw)

    legacy = getattr(module, "world_instance", None)
    raw = getattr(legacy, "SCENE_TYPES", {}) if isinstance(legacy, World) else {}
    if isinstance(raw, dict) and raw:
        return dict(raw)

    for attr in dir(module):
        obj = getattr(module, attr)
        if isinstance(obj, type) and issubclass(obj, World) and obj is not World:
            raw = getattr(obj, "SCENE_TYPES", {}) or {}
            return dict(raw) if isinstance(raw, dict) else {}
    return dict(World.SCENE_TYPES)


def _coerce_perspective(value: object) -> object:
    """DEFAULT_PERSPECTIVE 가 int(레거시)이든 tuple(신규)이든 JSON 친화 형태로 변환합니다."""
    if isinstance(value, tuple):
        return list(value)
    return value


def _world_scene_types(world: World) -> dict[str, str]:
    """World 클래스에 정의된 전역 SCENE_TYPES 값을 반환합니다."""
    raw = getattr(type(world), "SCENE_TYPES", None)
    if isinstance(raw, dict) and raw:
        return dict(raw)
    raw = getattr(world, "SCENE_TYPES", {}) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _world_perspective(world: World) -> object:
    """World 클래스에 정의된 전역 DEFAULT_PERSPECTIVE 값을 반환합니다."""
    return _coerce_perspective(getattr(type(world), "DEFAULT_PERSPECTIVE", getattr(world, "DEFAULT_PERSPECTIVE", 3)))


def _scenario_perspective(world: World) -> object:
    """World 인스턴스의 시나리오 perspective 설정을 JSON 친화 값으로 반환합니다."""
    return _coerce_perspective(getattr(world, "perspective_setting", _world_perspective(world)))


def scenario_infos(world_id: str) -> list[dict]:
    """시나리오별 메타데이터 목록을 반환합니다.

    SCENARIOS 리스트가 있으면 각 항목을, 없으면 단일 기본 시나리오를 만들어 반환합니다.
    각 dict: scenario_id, display_name, scene_types, perspective, default_location_id, default_time.
    """
    world, scenario_list = load_world(world_id, None)

    def _info(w: World, sid: str | None, display: str) -> dict:
        try:
            default_time = w.get_default_time()
        except Exception:
            default_time = datetime.now()
        return {
            "scenario_id": sid,
            "display_name": display,
            "scene_types": dict(getattr(w, "SCENE_TYPES", {}) or {}),
            "world_scene_types": _world_scene_types(w),
            "perspective": _scenario_perspective(w),
            "world_perspective": _world_perspective(w),
            "default_location_id": w.get_default_location_id(),
            "default_time": default_time.isoformat() if isinstance(default_time, datetime) else str(default_time),
        }

    extra_slots = list(getattr(world, "EXTRA_SLOTS", None) or [])

    if scenario_list:
        out = []
        for sc in scenario_list:
            w = sc.world if sc.world is not None else world
            w = apply_scenario_overrides(w, sc)
            info = _info(w, sc.scenario_id, sc.display_name or sc.scenario_id)
            info["extra_slots"] = list(getattr(w, "EXTRA_SLOTS", None) or extra_slots)
            out.append(info)
        return out

    # 단일 월드 (SCENARIOS 없음)
    info = _info(world, None, world_id)
    info["extra_slots"] = extra_slots
    return [info]
