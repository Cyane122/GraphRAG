# ================================
# src/wiki/migrations.py
#
# Wiki 문서의 schema_version 업그레이드 계약과 순차 마이그레이션 레지스트리입니다.
#
# 계약: 각 문서 frontmatter의 `schema_version`는 1부터 시작하는 정수다. 구조가 바뀌면
# CURRENT_SCHEMA_VERSION을 올리고, (from_version -> from_version+1) 마이그레이션 하나를
# register한다. 마이그레이션은 Markdown 원문을 받아 다음 버전의 원문을 반환하는 순수
# 함수이며, frontmatter의 `schema_version` 값도 함께 올려야 한다. 여러 단계는 순차 적용된다.
# 미래(>CURRENT) 버전 문서는 다운그레이드하지 않고 오류로 표시한다.
#
# Classes
#   - WikiMigrationError : 지원하지 않는 버전이나 마이그레이션 누락 예외
#
# Functions
#   - register_migration(document_type: str, from_version: int, migrate: Callable[[str], str]) -> None : 한 단계 마이그레이션을 등록합니다.
#   - migrate_document_content(content: str) -> str : frontmatter 버전을 읽어 CURRENT까지 순차 업그레이드합니다.
# ================================

from __future__ import annotations

from typing import Callable

from src.wiki.frontmatter import parse_frontmatter

CURRENT_SCHEMA_VERSION = 1

# (document_type, from_version) -> 다음 버전으로 올리는 순수 함수
_MIGRATIONS: dict[tuple[str, int], Callable[[str], str]] = {}


class WikiMigrationError(RuntimeError):
    """문서 schema_version이 지원 범위를 벗어나거나 마이그레이션이 없을 때 발생합니다."""


def register_migration(
    document_type: str,
    from_version: int,
    migrate: Callable[[str], str],
) -> None:
    """document_type의 from_version -> from_version+1 단계 마이그레이션을 등록합니다."""
    key = (document_type, from_version)
    if key in _MIGRATIONS:
        raise WikiMigrationError(f"Duplicate migration registered: {key}")
    _MIGRATIONS[key] = migrate


def migrate_document_content(content: str) -> str:
    """문서를 CURRENT_SCHEMA_VERSION까지 순차 업그레이드한 Markdown을 반환합니다."""
    metadata = parse_frontmatter(content)
    if metadata is None:
        raise WikiMigrationError("Cannot migrate a document without frontmatter")
    version = metadata.schema_version
    if version > CURRENT_SCHEMA_VERSION:
        raise WikiMigrationError(
            f"Document schema_version {version} is newer than supported "
            f"{CURRENT_SCHEMA_VERSION}: {metadata.id}"
        )
    document_type = metadata.type
    while version < CURRENT_SCHEMA_VERSION:
        migrate = _MIGRATIONS.get((document_type, version))
        if migrate is None:
            raise WikiMigrationError(
                f"No migration for {document_type} schema_version {version}"
            )
        content = migrate(content)
        upgraded = parse_frontmatter(content)
        if upgraded is None or upgraded.schema_version != version + 1:
            raise WikiMigrationError(
                f"Migration for {document_type} v{version} did not advance schema_version"
            )
        version = upgraded.schema_version
    return content
