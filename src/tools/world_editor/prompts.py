# ================================
# src/tools/world_editor/prompts.py
#
# 월드 prompt/ 디렉터리의 파일 트리 조회와 .md 읽기/쓰기를 담당합니다.
# 경로 탈출(path traversal)은 prompt/ 안으로 강제 제한합니다.
# 씬 타입 키↔파일 동기화 경고와 few_shot/opening_scene 검사를 제공합니다.
#
# Functions
#   - build_prompt_tree(world_id: str, scenario_id: str | None) -> dict : 트리 + 씬키 + 경고
#   - read_prompt(world_id: str, rel_path: str) -> dict : 파일 내용 + kind + checks
#   - create_prompt_path(world_id: str, rel_path: str, is_dir: bool = False, content: str = "") -> dict : 프롬프트 파일/폴더 생성
#   - write_prompt(world_id: str, rel_path: str, content: str) -> dict : 저장(SaveResult dict)
#   - delete_prompt_path(world_id: str, rel_path: str) -> dict : 프롬프트 파일/빈 폴더 삭제
# ================================

from __future__ import annotations

import re
import shutil
from pathlib import Path

from src.tools.world_editor.worlds import load_world, prompt_dir

_GLOBAL_PROMPT_DIR = Path(__file__).resolve().parents[2] / "agents" / "prompt_factory" / "prompts"


def _scene_keys(world_id: str, scenario_id: str | None) -> list[str]:
    """선택 시나리오의 SCENE_TYPES 키 목록을 반환합니다."""
    world, _ = load_world(world_id, scenario_id)
    return list(getattr(world, "SCENE_TYPES", {}) or {})


def _safe_path(world_id: str, rel_path: str) -> Path:
    """rel_path를 prompt/ 기준으로 안전하게 해석합니다. 디렉터리 밖이면 ValueError."""
    root = prompt_dir(world_id).resolve()
    target = (root / rel_path).resolve()
    # 경로 탈출 차단: target은 반드시 root와 같거나 그 하위여야 한다.
    if target != root and root not in target.parents:
        raise ValueError(f"경로가 prompt/ 밖을 가리킵니다: {rel_path}")
    return target


def _validate_rel_path(rel_path: str, *, allow_dir: bool = True) -> None:
    """프롬프트 상대경로가 편집기에서 생성/삭제 가능한 형태인지 검증합니다."""
    if not rel_path or rel_path.startswith(("/", "\\")):
        raise ValueError("상대 경로가 필요합니다.")
    parts = Path(rel_path).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"유효하지 않은 경로입니다: {rel_path}")
    if not allow_dir and not rel_path.endswith(".md"):
        raise ValueError("프롬프트 파일은 .md 확장자여야 합니다.")


def _kind(rel_path: str) -> str:
    """prompt/ 기준 상대경로로 파일 종류를 분류합니다 (프롬프트 내 역할)."""
    parts = rel_path.split("/")
    name = parts[-1]
    # 시나리오 하위
    if parts[0] == "scenarios":
        if name == "scenario.md":
            return "scenario"
        if name == "opening_scene.md":
            return "opening_scene"
        if name == "cot_append.md":
            return "cot_append"
        if "scenes" in parts:
            return "scene_cot" if name.endswith((".cot_append.md", ".checklist_append.md")) else "scene"
        return "other"
    if parts[0] == "scenes":
        return "scene_cot" if name.endswith((".cot_append.md", ".checklist_append.md")) else "scene"
    if parts[0] == "few_shot":
        return "few_shot"
    if parts[0] == "characters":
        return "character_cot" if name.endswith(".cot_append.md") else "character"
    # 최상위 단일 파일들
    return {
        "world.md": "world",
        "prose.md": "prose",
        "cot_append.md": "cot_append",
        "blacklist.md": "blacklist",
        "opening_scene.md": "opening_scene",
    }.get(name, "other")


def _scene_key_of(rel_path: str) -> str | None:
    """scenes/{k}.md, few_shot/{k}.md 같은 경로에서 씬 키 k를 추출합니다."""
    parts = rel_path.split("/")
    scene_key_dirs = {"scenes", "few_shot"}
    if (parts[-2:-1] and parts[-2] in scene_key_dirs) or parts[0] in scene_key_dirs:
        stem = parts[-1]
        for suffix in (".cot_append.md", ".checklist_append.md", ".md"):
            if stem.endswith(suffix):
                return stem[: -len(suffix)]
    return None


def _blank_node(path: str, *, is_dir: bool, kind: str = "dir", missing: bool = False) -> dict:
    """Prompt tree node의 공통 필드를 채운 dict를 반환합니다."""
    return {
        "name": Path(path).name,
        "path": path,
        "is_dir": is_dir,
        "kind": kind,
        "children": [],
        "scene_key": _scene_key_of(path) if not is_dir else None,
        "missing": missing,
        "inherited_source": None,
        "inherited_path": None,
    }


def _inheritance_for(root: Path, rel_path: str) -> dict:
    """파일이 없을 때 적용될 상속 출처와 내용을 반환합니다."""
    parts = rel_path.split("/")
    kind = _kind(rel_path)
    scene_key = _scene_key_of(rel_path)
    if not scene_key or kind not in {"scene", "scene_cot"}:
        return {"source": "none", "path": None, "content": ""}

    if parts[0] == "scenarios" and len(parts) >= 4:
        scope = parts[2]
    else:
        scope = parts[0]

    if scope == "scenes" and kind in {"scene", "scene_cot"}:
        suffix = ".cot_append.md" if kind == "scene_cot" else ".md"
        world_rel = f"scenes/{scene_key}{suffix}"
        global_rels = [
            f"genre_specific/scenes/{scene_key}{suffix}",
            f"genre_specific/{scene_key}{suffix}",
        ]
    else:
        return {"source": "none", "path": None, "content": ""}

    world_path = root / world_rel
    if world_path.is_file():
        content = world_path.read_text(encoding="utf-8")
        if content:
            return {"source": "world", "path": world_rel, "content": content}

    for global_rel in global_rels:
        global_path = _GLOBAL_PROMPT_DIR / global_rel
        if global_path.is_file():
            content = global_path.read_text(encoding="utf-8")
            if content:
                return {"source": "global", "path": global_rel, "content": content}

    return {"source": "none", "path": None, "content": ""}


def _build_node(path: Path, root: Path) -> dict:
    """파일/디렉터리를 PromptNode dict로 변환합니다 (디렉터리는 재귀)."""
    rel = path.relative_to(root).as_posix()
    if path.is_dir():
        # 디렉터리 먼저, 그 안에서 폴더→파일, 이름순 정렬
        children = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        return {
            "name": path.name, "path": rel, "is_dir": True, "kind": "dir",
            "children": [_build_node(c, root) for c in children],
            "scene_key": None, "missing": False,
            "inherited_source": None, "inherited_path": None,
        }
    return {
        "name": path.name, "path": rel, "is_dir": False, "kind": _kind(rel),
        "children": [], "scene_key": _scene_key_of(rel), "missing": False,
        "inherited_source": None, "inherited_path": None,
    }


def _inject_missing(
    nodes: list[dict],
    dir_name: str,
    scene_keys: list[str],
    root: Path,
    suffix: str = ".md",
) -> list[str]:
    """씬키 기반 prompt 디렉터리에 누락된 씬키용 가상 노드를 주입하고 경고 목록을 반환합니다.

    디렉터리 노드가 없으면 합성해 트리에 추가합니다. 가상 노드는 missing=True (열람 불가, 경고 표시용).
    """
    warnings: list[str] = []
    dir_node = next((n for n in nodes if n["is_dir"] and n["name"] == dir_name), None)
    if dir_node is None:
        dir_node = _blank_node(dir_name, is_dir=True)
        nodes.append(dir_node)
    existing = {c["scene_key"] for c in dir_node["children"] if not c["is_dir"]}
    for key in scene_keys:
        if key in existing:
            continue
        rel = f"{dir_name}/{key}{suffix}"
        inherited = _inheritance_for(root, rel)
        dir_node["children"].append({
            "name": f"{key}{suffix}", "path": rel,
            "is_dir": False, "kind": _kind(rel),
            "children": [], "scene_key": key, "missing": True,
            "inherited_source": inherited["source"],
            "inherited_path": inherited["path"],
        })
        label = {
            "scenes": "scenes",
            "few_shot": "few_shot",
        }.get(dir_name, dir_name)
        if inherited["source"] == "none" or dir_name == "few_shot":
            warnings.append(f"씬 타입 '{key}'에 대응하는 {label}/{key}{suffix} 파일이 없습니다.")
    dir_node["children"].sort(key=lambda c: (c["is_dir"], c["name"].lower()))
    return warnings


def _inject_missing_scenario_files(nodes: list[dict], sid: str, prompt_root: Path) -> list[str]:
    """scenarios/{sid}/ 아래 필수 파일(opening_scene.md, scenario.md)이 없으면 missing 노드를 주입합니다."""
    warnings: list[str] = []

    # scenarios/ 디렉터리 노드 찾기 (없으면 합성)
    scn_node = next((n for n in nodes if n["is_dir"] and n["name"] == "scenarios"), None)
    if scn_node is None:
        scn_node = _blank_node("scenarios", is_dir=True)
        nodes.append(scn_node)

    # scenarios/{sid}/ 디렉터리 노드 찾기 (없으면 합성)
    sid_node = next((c for c in scn_node["children"] if c["is_dir"] and c["name"] == sid), None)
    if sid_node is None:
        sid_node = _blank_node(f"scenarios/{sid}", is_dir=True)
        scn_node["children"].append(sid_node)

    existing = {c["name"] for c in sid_node["children"] if not c["is_dir"]}
    for fname, label in [("opening_scene.md", "오프닝 내레이션"), ("scenario.md", "시나리오 설명")]:
        if fname in existing:
            continue
        rel = f"scenarios/{sid}/{fname}"
        kind = _kind(rel)
        sid_node["children"].append({
            "name": fname, "path": rel, "is_dir": False,
            "kind": kind, "children": [], "scene_key": None, "missing": True,
            "inherited_source": None, "inherited_path": None,
        })
        warnings.append(f"시나리오 '{sid}'에 {label}({fname})이 없습니다.")
    sid_node["children"].sort(key=lambda c: (c["is_dir"], c["name"].lower()))
    return warnings


def _ensure_dir_node(parent: dict, rel_path: str) -> dict:
    """parent 아래에 rel_path 디렉터리 노드를 보장하고 반환합니다."""
    name = Path(rel_path).name
    node = next((c for c in parent["children"] if c["is_dir"] and c["name"] == name), None)
    if node is None:
        node = _blank_node(rel_path, is_dir=True)
        parent["children"].append(node)
    return node


def _inject_missing_scenario_scene_assets(nodes: list[dict], sid: str, scene_keys: list[str], root: Path) -> list[str]:
    """시나리오별 scene override 가상 노드와 상속 상태를 주입합니다."""
    warnings: list[str] = []
    scn_node = next((n for n in nodes if n["is_dir"] and n["name"] == "scenarios"), None)
    if scn_node is None:
        scn_node = _blank_node("scenarios", is_dir=True)
        nodes.append(scn_node)
    sid_node = next((c for c in scn_node["children"] if c["is_dir"] and c["name"] == sid), None)
    if sid_node is None:
        sid_node = _blank_node(f"scenarios/{sid}", is_dir=True)
        scn_node["children"].append(sid_node)

    targets = [
        ("scenes", ".md", "scene"),
        ("scenes", ".cot_append.md", "scene_cot"),
    ]
    for dir_name, suffix, kind in targets:
        dir_node = _ensure_dir_node(sid_node, f"scenarios/{sid}/{dir_name}")
        existing = {c["name"] for c in dir_node["children"] if not c["is_dir"]}
        for key in scene_keys:
            fname = f"{key}{suffix}"
            if fname in existing:
                continue
            rel = f"scenarios/{sid}/{dir_name}/{fname}"
            inherited = _inheritance_for(root, rel)
            dir_node["children"].append({
                "name": fname,
                "path": rel,
                "is_dir": False,
                "kind": kind,
                "children": [],
                "scene_key": key,
                "missing": True,
                "inherited_source": inherited["source"],
                "inherited_path": inherited["path"],
            })
            if inherited["source"] == "none" and kind != "scene_cot":
                warnings.append(f"씬 타입 '{key}'에 사용할 {rel} / 월드 공통 / 전체 공통 파일이 없습니다.")
        dir_node["children"].sort(key=lambda c: (c["is_dir"], c["name"].lower()))

    sid_node["children"].sort(key=lambda c: (c["is_dir"], c["name"].lower()))
    scn_node["children"].sort(key=lambda c: (c["is_dir"], c["name"].lower()))
    return warnings


def build_prompt_tree(world_id: str, scenario_id: str | None) -> dict:
    """prompt/ 트리와 씬 키, 누락 경고를 묶어 반환합니다."""
    root = prompt_dir(world_id)
    scene_keys = _scene_keys(world_id, scenario_id)
    if not root.exists():
        return {"world_id": world_id, "scenario_id": scenario_id, "scene_types": scene_keys,
                "nodes": [], "warnings": ["이 월드에는 prompt/ 디렉터리가 없습니다."]}

    nodes = [_build_node(c, root) for c in sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))]

    # §6: scenarios/ 아래는 '현재 시나리오' 디렉터리만 노출하고 다른 시나리오는 숨긴다.
    # (월드 공통 파일 world.md/prose.md/scenes/few_shot/characters 등은 그대로 둔다.)
    _sid = scenario_id or "default"
    for n in nodes:
        if n["is_dir"] and n["name"] == "scenarios":
            n["children"] = [c for c in n["children"] if (not c["is_dir"]) or c["name"] == _sid]

    # 씬 키 ↔ 파일 동기화 경고 (월드 공통 scenes/, few_shot/ 기준)
    warnings: list[str] = []
    warnings += _inject_missing(nodes, "scenes", scene_keys, root)
    warnings += _inject_missing(nodes, "few_shot", scene_keys, root)

    # 시나리오 스코프 필수 파일 주입: scenarios/{sid}/opening_scene.md, scenario.md
    warnings += _inject_missing_scenario_files(nodes, _sid, root)
    warnings += _inject_missing_scenario_scene_assets(nodes, _sid, scene_keys, root)

    nodes.sort(key=lambda c: (c["is_dir"], c["name"].lower()))

    return {"world_id": world_id, "scenario_id": scenario_id,
            "scene_types": scene_keys, "nodes": nodes, "warnings": warnings}


def _checks(kind: str, content: str) -> list[dict]:
    """파일 종류별 구조 검사 결과(경고/안내)를 반환합니다."""
    checks: list[dict] = []
    if kind == "few_shot":
        # parse_few_shot은 '# GOOD' / '# BAD' 헤더를 본다 — 대소문자 무시로 존재만 확인
        if not re.search(r"(?im)^#+\s*good\b", content):
            checks.append({"level": "warn", "message": "'# GOOD' 섹션이 없습니다 (few_shot 파서가 인식 못 함)."})
        if not re.search(r"(?im)^#+\s*bad\b", content):
            checks.append({"level": "warn", "message": "'# BAD' 섹션이 없습니다 (few_shot 파서가 인식 못 함)."})
    if kind == "opening_scene":
        used = [v for v in ("{char}", "{user}") if v in content]
        if used:
            checks.append({"level": "info", "message": f"변수 치환 사용 중: {', '.join(used)} ({{char}}=NPC 이름, {{user}}=PC 이름)."})
        else:
            checks.append({"level": "info", "message": "사용 가능한 변수: {char}=NPC 이름, {user}=PC 이름."})
    return checks


def read_prompt(world_id: str, rel_path: str) -> dict:
    """프롬프트 .md 파일 내용과 종류, 검사 결과를 반환합니다."""
    path = _safe_path(world_id, rel_path)
    kind = _kind(rel_path)
    if path.is_file():
        content = path.read_text(encoding="utf-8")
        return {
            "path": rel_path,
            "content": content,
            "kind": kind,
            "checks": _checks(kind, content),
            "source": "scenario" if rel_path.startswith("scenarios/") else "world",
            "inherited_source": None,
            "inherited_path": None,
            "_missing": False,
        }

    inherited = _inheritance_for(prompt_dir(world_id), rel_path)
    if inherited["source"] == "none" and kind not in {"scene", "scene_cot"}:
        raise FileNotFoundError(rel_path)
    content = inherited["content"]
    return {
        "path": rel_path,
        "content": content,
        "kind": kind,
        "checks": _checks(kind, content),
        "source": "none",
        "inherited_source": inherited["source"],
        "inherited_path": inherited["path"],
        "_missing": True,
    }


def write_prompt(world_id: str, rel_path: str, content: str) -> dict:
    """프롬프트 .md 파일을 저장합니다. 기존 파일은 .bak으로 백업합니다."""
    _validate_rel_path(rel_path, allow_dir=False)
    path = _safe_path(world_id, rel_path)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    # 덮어쓰기 전 백업 (신규 파일이면 백업 없음)
    backup = None
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copyfile(path, bak)
        backup = str(bak)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "message": "저장됨", "backup": backup, "formatted": False}


def create_prompt_path(world_id: str, rel_path: str, is_dir: bool = False, content: str = "") -> dict:
    """prompt/ 아래에 새 폴더 또는 .md 파일을 생성합니다."""
    _validate_rel_path(rel_path, allow_dir=is_dir)
    path = _safe_path(world_id, rel_path)
    if path.exists():
        return {"ok": False, "message": f"이미 존재합니다: {rel_path}", "backup": None, "formatted": False}
    if is_dir:
        path.mkdir(parents=True, exist_ok=False)
        return {"ok": True, "message": f"폴더 생성됨: {rel_path}", "backup": None, "formatted": False}
    if not path.parent.exists():
        return {"ok": False, "message": f"상위 디렉터리가 없습니다: {rel_path}", "backup": None, "formatted": False}
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "message": f"파일 생성됨: {rel_path}", "backup": None, "formatted": False}


def delete_prompt_path(world_id: str, rel_path: str) -> dict:
    """prompt/ 아래의 파일 또는 빈 폴더를 삭제합니다. 폴더는 비어 있을 때만 허용합니다."""
    _validate_rel_path(rel_path)
    path = _safe_path(world_id, rel_path)
    if not path.exists():
        return {"ok": False, "message": f"대상이 없습니다: {rel_path}", "backup": None, "formatted": False}
    if path.is_dir():
        try:
            path.rmdir()
        except OSError:
            return {"ok": False, "message": "폴더가 비어 있지 않습니다.", "backup": None, "formatted": False}
        return {"ok": True, "message": f"폴더 삭제됨: {rel_path}", "backup": None, "formatted": False}
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copyfile(path, bak)
    path.unlink()
    return {"ok": True, "message": f"파일 삭제됨: {rel_path}", "backup": str(bak), "formatted": False}
