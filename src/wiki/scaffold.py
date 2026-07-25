# ================================
# src/wiki/scaffold.py
#
# Markdown 템플릿으로 독립된 Wiki V2 world/thread vault를 생성합니다.
#
# Classes
#   - WikiScaffoldError : 스캐폴드 입력 또는 템플릿 검증 예외
#
# Functions
#   - _validate_identifier(value: str, field_name: str) -> str : 안전한 vault 식별자를 검증합니다.
#   - _validate_display_value(value: str, field_name: str) -> str : 단일 행 표시 값을 검증합니다.
#   - _timestamp() -> str : UTC 생성 시각을 반환합니다.
#   - _scaffold_timestamp(store: WikiStore, manifest_path: str) -> str : 부분 scaffold의 생성 시각을 재사용합니다.
#   - _prepare_directories(root: Path, directories: tuple[str, ...]) -> None : 표준 하위 디렉터리를 생성합니다.
#   - _resolve_scaffold_root(root: Path, category: str, identifier: str) -> Path : symlink/junction을 포함한 경로 탈출을 거부합니다.
#   - _create_scaffold_documents(store: WikiStore, documents: tuple[tuple[str, str], ...]) -> list[WikiDocument] : 동일한 부분 생성을 이어서 완료합니다.
#   - _validate_world(root: Path, world_id: str) -> None : thread가 참조할 world manifest를 검증합니다.
#   - render_wiki_template(template_name: str, values: Mapping[str, str]) -> str : 템플릿을 안전한 단일 행 값으로 렌더링합니다.
#   - scaffold_world(root: Path, world_id: str, display_name: str) -> WikiScaffoldResult : world vault 기본 문서와 디렉터리를 생성합니다.
#   - scaffold_thread(root: Path, thread_id: str, world_id: str, title: str) -> WikiScaffoldResult : thread vault 기본 문서와 디렉터리를 생성합니다.
# ================================

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Mapping

from src.wiki.frontmatter import WikiFrontmatterError, parse_frontmatter
from src.wiki.markdown import (
    MarkdownStructureError,
    document_revision,
    parse_markdown_sections,
)
from src.wiki.models import WikiDocument, WikiScaffoldResult
from src.wiki.store import WikiStore


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*?)(_YAML)?\}\}")
_VALUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TEMPLATE_ROOT = Path(__file__).with_name("templates")
_WORLD_DIRECTORIES = (
    "scenarios",
    "characters",
    "locations",
    "organizations",
)
_THREAD_DIRECTORIES = (
    "scene",
    "characters",
    "relationships",
    "events",
    "memories",
    "goals",
    "items",
    "secrets",
    "commits",
)


class WikiScaffoldError(ValueError):
    """스캐폴드 식별자, 표시 문자열, 또는 템플릿이 안전하지 않을 때 발생합니다."""


def _validate_identifier(value: str, field_name: str) -> str:
    """경로 구성에 사용할 제한된 ASCII 식별자를 반환합니다."""
    if not _IDENTIFIER_RE.fullmatch(value):
        raise WikiScaffoldError(
            f"{field_name} must match {_IDENTIFIER_RE.pattern!r}: {value!r}"
        )
    return value


def _validate_display_value(value: str, field_name: str) -> str:
    """Markdown 제목과 YAML scalar에 넣을 단일 행 표시 문자열을 반환합니다."""
    normalized = value.strip()
    if not normalized or "\n" in normalized or "\r" in normalized:
        raise WikiScaffoldError(f"{field_name} must be a non-empty single line")
    return normalized


def _timestamp() -> str:
    """스캐폴드 문서에 기록할 UTC ISO-8601 시각을 반환합니다."""
    return datetime.now(timezone.utc).isoformat()


def _scaffold_timestamp(store: WikiStore, manifest_path: str) -> str:
    """기존 manifest가 있으면 최초 생성 시각을 재사용해 resume 내용을 고정합니다."""
    path = store.resolve_path(manifest_path)
    if not path.exists():
        return _timestamp()
    document = store.read_document(manifest_path)
    if document.metadata is None:
        raise WikiScaffoldError(f"Existing scaffold manifest lacks metadata: {manifest_path}")
    return document.metadata.created_at.isoformat()


def render_wiki_template(template_name: str, values: Mapping[str, str]) -> str:
    """패키지 템플릿을 raw Markdown 값과 JSON 호환 YAML 값으로 렌더링합니다."""
    if Path(template_name).name != template_name or not template_name.endswith(".md"):
        raise WikiScaffoldError(f"Invalid template name: {template_name!r}")
    template_path = _TEMPLATE_ROOT / template_name
    if not template_path.is_file():
        raise WikiScaffoldError(f"Unknown Wiki template: {template_name!r}")

    invalid_keys = sorted(
        key for key in values if not _VALUE_KEY_RE.fullmatch(key) or key.endswith("_YAML")
    )
    if invalid_keys:
        raise WikiScaffoldError(f"Invalid template value keys: {invalid_keys}")

    source = template_path.read_text(encoding="utf-8")
    declared_keys = {match.group(1) for match in _PLACEHOLDER_RE.finditer(source)}
    missing = sorted(declared_keys - values.keys())
    unexpected = sorted(values.keys() - declared_keys)
    if missing or unexpected:
        raise WikiScaffoldError(
            f"Template values mismatch; missing={missing}, unexpected={unexpected}"
        )
    normalized_values = {
        key: _validate_display_value(value, key) for key, value in values.items()
    }

    def replace_placeholder(match: re.Match[str]) -> str:
        """원본 template placeholder 하나를 정확히 한 번 치환합니다."""
        value = normalized_values[match.group(1)]
        if match.group(2):
            return json.dumps(value, ensure_ascii=False)
        return value

    rendered = _PLACEHOLDER_RE.sub(replace_placeholder, source)
    rendered = rendered.rstrip() + "\n"
    try:
        metadata = parse_frontmatter(rendered)
        parse_markdown_sections(rendered)
    except (MarkdownStructureError, WikiFrontmatterError) as exc:
        raise WikiScaffoldError(f"Invalid Wiki template {template_name!r}: {exc}") from exc
    if (
        metadata is None
        or metadata.id is None
        or metadata.type is None
        or metadata.schema_version is None
    ):
        raise WikiScaffoldError(
            f"Template {template_name!r} requires id, type, and schema_version"
        )
    return rendered


def _prepare_directories(root: Path, directories: tuple[str, ...]) -> None:
    """표준 vault 하위 디렉터리를 빠짐없이 생성합니다."""
    for directory in directories:
        (root / directory).mkdir(parents=True, exist_ok=True)


def _resolve_scaffold_root(root: Path, category: str, identifier: str) -> Path:
    """resolved world/thread 경로가 지정한 vault category 안에 있을 때만 반환합니다."""
    vault_root = root.resolve()
    category_path = vault_root / category
    resolved_category = category_path.resolve()
    if (
        not resolved_category.is_relative_to(vault_root)
        or resolved_category != category_path
    ):
        raise WikiScaffoldError(f"Wiki {category} directory escapes the vault")
    target = category_path / identifier
    resolved_target = target.resolve()
    expected_target = resolved_category / identifier
    if (
        not resolved_target.is_relative_to(resolved_category)
        or resolved_target != expected_target
    ):
        raise WikiScaffoldError(f"Wiki scaffold path escapes {category}: {identifier!r}")
    return resolved_target


def _create_scaffold_documents(
    store: WikiStore,
    documents: tuple[tuple[str, str], ...],
) -> list[WikiDocument]:
    """동일한 기존 문서는 재사용하고 빠진 핵심 문서만 생성합니다."""
    created: list[WikiDocument] = []
    for path, content in documents:
        expected_revision = document_revision(content)
        resolved = store.resolve_path(path)
        if resolved.exists():
            existing = store.read_document(path)
            if existing.revision != expected_revision:
                raise FileExistsError(f"Wiki scaffold document already differs: {path}")
            created.append(existing)
            continue
        try:
            created.append(store.create_document(path, content))
        except FileExistsError:
            existing = store.read_document(path)
            if existing.revision != expected_revision:
                raise FileExistsError(f"Wiki scaffold document already differs: {path}")
            created.append(existing)
        except OSError as exc:
            raise WikiScaffoldError(f"Wiki scaffold creation failed at {path}: {exc}") from exc
    return created


def _validate_world(root: Path, world_id: str) -> None:
    """thread가 참조하는 world manifest의 존재와 정체성을 검증합니다."""
    world_root = _resolve_scaffold_root(root, "worlds", world_id)
    if not world_root.is_dir():
        raise WikiScaffoldError(f"Referenced world does not exist: {world_id!r}")
    world_store = WikiStore(world_root)
    try:
        document = world_store.read_document("world.md")
    except (FileNotFoundError, WikiFrontmatterError) as exc:
        raise WikiScaffoldError(f"Referenced world is not valid: {world_id!r}") from exc
    expected_id = f"world:{world_id}"
    if (
        document.metadata is None
        or document.metadata.id != expected_id
        or document.metadata.type != "world"
    ):
        raise WikiScaffoldError(
            f"Referenced world manifest must be type='world' and id={expected_id!r}"
        )


def scaffold_world(root: Path, world_id: str, display_name: str) -> WikiScaffoldResult:
    """기존 문서를 덮지 않고 world.md와 prose.md가 있는 world vault를 만듭니다."""
    safe_world_id = _validate_identifier(world_id, "world_id")
    safe_display_name = _validate_display_value(display_name, "display_name")
    world_root = _resolve_scaffold_root(root, "worlds", safe_world_id)
    store = WikiStore(world_root)
    created_at = _scaffold_timestamp(store, "world.md")
    pending_documents = (
        (
            "world.md",
            render_wiki_template(
                "world.md",
                {
                    "DOCUMENT_ID": f"world:{safe_world_id}",
                    "DISPLAY_NAME": safe_display_name,
                    "CREATED_AT": created_at,
                },
            ),
        ),
        (
            "prose.md",
            render_wiki_template(
                "prose.md",
                {
                    "DOCUMENT_ID": f"world:{safe_world_id}:prose",
                    "WORLD_ID": safe_world_id,
                    "DISPLAY_NAME": safe_display_name,
                    "CREATED_AT": created_at,
                },
            ),
        ),
    )
    _prepare_directories(world_root, _WORLD_DIRECTORIES)
    documents = _create_scaffold_documents(store, pending_documents)
    return WikiScaffoldResult(root=world_root, documents=documents)


def scaffold_thread(
    root: Path,
    thread_id: str,
    world_id: str,
    title: str,
) -> WikiScaffoldResult:
    """기존 문서를 덮지 않고 thread manifest와 현재 장면 문서를 만듭니다."""
    safe_thread_id = _validate_identifier(thread_id, "thread_id")
    safe_world_id = _validate_identifier(world_id, "world_id")
    safe_title = _validate_display_value(title, "title")
    _validate_world(root, safe_world_id)
    thread_root = _resolve_scaffold_root(root, "threads", safe_thread_id)
    store = WikiStore(thread_root)
    created_at = _scaffold_timestamp(store, "thread.md")
    pending_documents = (
        (
            "thread.md",
            render_wiki_template(
                "thread.md",
                {
                    "DOCUMENT_ID": f"thread:{safe_thread_id}",
                    "WORLD_ID": safe_world_id,
                    "TITLE": safe_title,
                    "CREATED_AT": created_at,
                },
            ),
        ),
        (
            "scene/current.md",
            render_wiki_template(
                "scene.md",
                {
                    "DOCUMENT_ID": f"thread:{safe_thread_id}:scene:current",
                    "THREAD_ID": safe_thread_id,
                    "WORLD_ID": safe_world_id,
                    "TITLE": safe_title,
                    "CREATED_AT": created_at,
                },
            ),
        ),
    )
    _prepare_directories(thread_root, _THREAD_DIRECTORIES)
    documents = _create_scaffold_documents(store, pending_documents)
    return WikiScaffoldResult(root=thread_root, documents=documents)
