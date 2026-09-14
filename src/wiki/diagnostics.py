# ================================
# src/wiki/diagnostics.py
#
# Wiki vault의 문서 무결성(중복 ID, frontmatter, 섹션 구조)을 스캔합니다.
#
# Classes
#   - WikiDiagnostic : 한 문서에서 발견한 진단 항목
#
# Functions
#   - diagnose_wiki_scope(vault_root: Path, thread_id: str, world_id: str) -> list[WikiDiagnostic] : world 자산과 thread 문서에서 중복 ID·frontmatter·섹션 오류를 수집합니다.
#
# Duplicate id 검사는 런타임 해석 범위를 그대로 따른다(`src/wiki/context.py`의
# `_profile_documents`/`read_wiki_scene_prompt_assets`): world 최상위 트리
# (scenarios/ 제외)와 각 scenario 디렉터리는 서로 독립된 id 공간이다. scenario
# 문서가 world 문서와 같은 id·같은 type을 쓰는 것은 의도된 override이므로 오류가
# 아니고(type이 다르면 여전히 오류), 서로 다른 scenario끼리는 id를 자유롭게
# 재사용할 수 있다. thread 문서는 world/scenario id 전체 풀과 비교해 기존과
# 동일하게 중복을 검사한다.
# ================================

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from src.wiki.markdown import parse_markdown_sections
from src.wiki.paths import wiki_thread_root_for_vault
from src.wiki.store import WikiStore


class WikiDiagnostic(BaseModel):
    """Wiki 문서에서 발견한 하나의 무결성 문제입니다."""

    level: Literal["error", "warning"]
    code: Literal["frontmatter", "sections", "duplicate_id"]
    path: str
    message: str


def _document_paths(directory: Path) -> list[Path]:
    """commit 이력을 제외한 Markdown 문서 경로를 정렬된 순서로 반환합니다."""
    if not directory.is_dir():
        return []
    paths: list[Path] = []
    for path in sorted(directory.rglob("*.md")):
        relative_parts = path.relative_to(directory).parts
        if path.name == "commit.md" or "commits" in relative_parts:
            continue
        paths.append(path)
    return paths


def _world_level_paths(world_root: Path) -> list[Path]:
    """scenario 전용 id 공간(scenarios/ 하위)을 제외한 world 최상위 문서 경로를 반환합니다."""
    return [
        path
        for path in _document_paths(world_root)
        if path.relative_to(world_root).parts[0] != "scenarios"
    ]


def _scan_scope(
    store: WikiStore,
    base_root: Path,
    paths: list[Path],
    diagnostics: list[WikiDiagnostic],
    seen_ids: dict[str, tuple[str, str]],
    override_types: dict[str, str] | None = None,
) -> None:
    """paths의 각 Markdown 문서에서 frontmatter/section 오류를 diagnostics에 기록하고,
    seen_ids로 표현되는 이 범위 안에서만 중복 id를 duplicate_id로 보고합니다.

    override_types가 주어지면(scenario 범위 검사), 같은 id가 override_types에 같은
    type으로 이미 존재할 때는 world 문서를 재정의하는 scenario override로 보아 오류로
    보고하지 않는다. type이 다르면 여전히 duplicate_id 오류다.
    """
    for path in paths:
        relative = path.relative_to(base_root).as_posix()
        display = f"{base_root.name}/{relative}"
        try:
            document = store.read_document(relative)
        except Exception as exc:
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="frontmatter",
                    path=display,
                    message=str(exc)[:200],
                )
            )
            continue
        try:
            parse_markdown_sections(document.content)
        except Exception as exc:
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="sections",
                    path=display,
                    message=str(exc)[:200],
                )
            )
        if document.metadata is None:
            continue
        document_id = document.metadata.id
        document_type = str(document.metadata.type)
        if document_id in seen_ids:
            _, existing_display = seen_ids[document_id]
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="duplicate_id",
                    path=display,
                    message=(
                        f"Duplicate document id '{document_id}' also declared in "
                        f"{existing_display}"
                    ),
                )
            )
            continue
        if (
            override_types is not None
            and document_id in override_types
            and override_types[document_id] != document_type
        ):
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="duplicate_id",
                    path=display,
                    message=(
                        f"Document id '{document_id}' reuses a world-level id declared "
                        f"with a different type ('{override_types[document_id]}' vs "
                        f"'{document_type}')"
                    ),
                )
            )
        seen_ids[document_id] = (document_type, display)


def _scan_root(
    root: Path,
    seen_ids: dict[str, str],
    diagnostics: list[WikiDiagnostic],
) -> None:
    """한 vault 루트(thread root)의 Markdown을 읽어 진단 항목을 diagnostics에 누적합니다.

    seen_ids는 호출 전에 채워둔 world/scenario id 풀을 그대로 이어받아, thread 문서가
    world나 scenario의 기존 id를 재사용하면 기존과 동일하게 duplicate_id로 보고한다.
    """
    if not root.is_dir():
        return
    store = WikiStore(root)
    for path in _document_paths(root):
        relative = path.relative_to(root).as_posix()
        display = f"{root.name}/{relative}"
        try:
            document = store.read_document(relative)
        except Exception as exc:
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="frontmatter",
                    path=display,
                    message=str(exc)[:200],
                )
            )
            continue
        try:
            parse_markdown_sections(document.content)
        except Exception as exc:
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="sections",
                    path=display,
                    message=str(exc)[:200],
                )
            )
        if document.metadata is None:
            continue
        document_id = document.metadata.id
        if document_id in seen_ids:
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="duplicate_id",
                    path=display,
                    message=(
                        f"Duplicate document id '{document_id}' also declared in "
                        f"{seen_ids[document_id]}"
                    ),
                )
            )
        else:
            seen_ids[document_id] = display


def diagnose_wiki_scope(
    vault_root: Path,
    thread_id: str,
    world_id: str,
) -> list[WikiDiagnostic]:
    """한 대화가 참조하는 world 자산과 thread 문서의 무결성을 진단합니다."""
    root = vault_root.resolve()
    diagnostics: list[WikiDiagnostic] = []
    world_root = root / "worlds" / world_id
    combined_ids: dict[str, str] = {}
    if world_root.is_dir():
        store = WikiStore(world_root)
        world_seen: dict[str, tuple[str, str]] = {}
        _scan_scope(store, world_root, _world_level_paths(world_root), diagnostics, world_seen)
        world_types = {document_id: kind for document_id, (kind, _display) in world_seen.items()}
        for document_id, (_kind, display) in world_seen.items():
            combined_ids[document_id] = display
        scenarios_root = world_root / "scenarios"
        if scenarios_root.is_dir():
            for scenario_dir in sorted(path for path in scenarios_root.iterdir() if path.is_dir()):
                scenario_seen: dict[str, tuple[str, str]] = {}
                _scan_scope(
                    store,
                    world_root,
                    _document_paths(scenario_dir),
                    diagnostics,
                    scenario_seen,
                    override_types=world_types,
                )
                for document_id, (_kind, display) in scenario_seen.items():
                    combined_ids.setdefault(document_id, display)
    _scan_root(wiki_thread_root_for_vault(root, thread_id), combined_ids, diagnostics)
    return diagnostics
