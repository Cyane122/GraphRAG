# ================================
# src/wiki/frontmatter.py
#
# Wiki V2 Markdown의 YAML frontmatter를 안전하게 읽고 공통 메타데이터로 검증합니다.
#
# Classes
#   - WikiFrontmatterError : frontmatter 구문 또는 구조 검증 예외
#   - _UniqueKeyLoader : 중복 YAML mapping key를 거부하는 SafeLoader
#
# Functions
#   - _construct_unique_mapping(loader: _UniqueKeyLoader, node: MappingNode, deep: bool = False) -> dict[Any, Any] : 중복 YAML key를 거부합니다.
#   - _frontmatter_source(content: str) -> str | None : 열 0 delimiter 사이의 YAML 원문을 반환합니다.
#   - parse_frontmatter(content: str) -> WikiMetadata | None : 문서 시작 frontmatter를 파싱합니다.
# ================================

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError
from yaml import YAMLError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from src.wiki.models import WikiMetadata


class WikiFrontmatterError(ValueError):
    """Markdown frontmatter가 YAML 매핑이 아니거나 유효하지 않을 때 발생합니다."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """중복 mapping key를 오류로 처리하는 frontmatter 전용 SafeLoader입니다."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    """YAML mapping을 만들면서 같은 key의 두 번째 등장을 거부합니다."""
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _frontmatter_source(content: str) -> str | None:
    """문서 첫 줄의 YAML frontmatter 원문을 반환합니다."""
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line in {"---", "..."}:
            return "\n".join(lines[1:index])
    raise WikiFrontmatterError("Frontmatter opening delimiter has no closing delimiter")


def parse_frontmatter(content: str) -> WikiMetadata | None:
    """문서 시작의 YAML mapping을 공통 Wiki 메타데이터로 반환합니다."""
    source = _frontmatter_source(content)
    if source is None:
        return None
    try:
        loaded: Any = yaml.load(source, Loader=_UniqueKeyLoader)
    except YAMLError as exc:
        raise WikiFrontmatterError(f"Invalid YAML frontmatter: {exc}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
        raise WikiFrontmatterError("Frontmatter must be a mapping with string keys")
    try:
        return WikiMetadata.model_validate(loaded)
    except ValidationError as exc:
        raise WikiFrontmatterError(f"Invalid Wiki metadata: {exc}") from exc
