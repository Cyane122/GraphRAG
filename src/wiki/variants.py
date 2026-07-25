# ================================
# src/wiki/variants.py
#
# 작성용 Markdown 분기를 선택해 Actor용 평탄한 인물 설정으로 변환합니다.
#
# Classes
#   - WikiVariantError : 인물 설정 분기 구조가 모호하거나 불완전할 때의 예외
#
# Functions
#   - resolve_profile_variants(body: str, active_variant: str, known_variants: set[str]) -> str : common과 활성 분기만 남기고 선택기 제목을 제거합니다.
# ================================

from __future__ import annotations

import re


_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
_COMMON_VARIANT = "common"
_DEFAULT_VARIANT = "default"


class WikiVariantError(ValueError):
    """인물 설정의 작성용 분기 구조를 안전하게 평탄화할 수 없을 때 발생합니다."""


def _heading(line: str) -> tuple[int, str] | None:
    """한 줄이 Markdown ATX 제목이면 깊이와 정규화한 제목을 반환합니다."""
    match = _HEADING_RE.match(line)
    if match is None:
        return None
    return len(match.group("marks")), match.group("title").strip()


def _promote_nested_headings(lines: list[str]) -> list[str]:
    """선택기 아래의 실제 제목을 한 단계 올려 원래 문서 계층을 복원합니다."""
    promoted: list[str] = []
    for line in lines:
        parsed = _heading(line)
        if parsed is None or parsed[0] < 4:
            promoted.append(line)
            continue
        depth, title = parsed
        promoted.append(f"{'#' * (depth - 1)} {title}")
    return promoted


def _trim_blank_lines(lines: list[str]) -> list[str]:
    """본문 양끝의 빈 줄만 제거해 블록 사이 결합을 안정화합니다."""
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _resolve_h2_block(
    lines: list[str],
    active_variant: str,
    selector_names: set[str],
) -> list[str]:
    """하나의 H2 안에 있는 작성용 H3 선택기를 해석해 평탄한 블록을 반환합니다."""
    selector_starts: list[tuple[int, str]] = []
    direct_h3_titles: list[str] = []
    for index, line in enumerate(lines[1:], start=1):
        parsed = _heading(line)
        if parsed is None or parsed[0] != 3:
            continue
        title = parsed[1]
        direct_h3_titles.append(title)
        if title in selector_names:
            selector_starts.append((index, title))

    if not selector_starts:
        return lines
    if len(selector_starts) != len(direct_h3_titles):
        raise WikiVariantError(
            f"분기 선택기와 일반 H3를 같은 H2에 섞을 수 없습니다: {lines[0]}"
        )
    if any(line.strip() for line in lines[1:selector_starts[0][0]]):
        raise WikiVariantError(
            f"첫 분기 선택기 앞에는 본문을 둘 수 없습니다: {lines[0]}"
        )

    sections: dict[str, list[str]] = {}
    for position, (start, title) in enumerate(selector_starts):
        if title in sections:
            raise WikiVariantError(f"분기 선택기가 중복되었습니다: {lines[0]} > {title}")
        end = selector_starts[position + 1][0] if position + 1 < len(selector_starts) else len(lines)
        sections[title] = lines[start + 1:end]

    selected_names: list[str] = []
    if _COMMON_VARIANT in sections:
        selected_names.append(_COMMON_VARIANT)
    if active_variant in sections:
        selected_names.append(active_variant)
    elif _DEFAULT_VARIANT in sections:
        selected_names.append(_DEFAULT_VARIANT)
    elif _COMMON_VARIANT not in sections:
        raise WikiVariantError(
            f"활성 분기와 default가 모두 없습니다: {lines[0]} > {active_variant}"
        )

    result = [lines[0]]
    for name in selected_names:
        selected = _trim_blank_lines(_promote_nested_headings(sections[name]))
        if selected:
            result.extend(["", *selected])
    return result


def resolve_profile_variants(
    body: str,
    active_variant: str,
    known_variants: set[str],
) -> str:
    """작성용 H3 분기 중 common과 활성 분기만 선택하고 선택기 이름은 제거합니다."""
    selector_names = set(known_variants) | {_COMMON_VARIANT, _DEFAULT_VARIANT}
    lines = body.splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        parsed = _heading(lines[index])
        if parsed is None or parsed[0] != 2:
            result.append(lines[index])
            index += 1
            continue
        end = index + 1
        while end < len(lines):
            next_heading = _heading(lines[end])
            if next_heading is not None and next_heading[0] <= 2:
                break
            end += 1
        result.extend(_resolve_h2_block(lines[index:end], active_variant, selector_names))
        index = end
    return "\n".join(result).strip()
