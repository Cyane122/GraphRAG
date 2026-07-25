# ================================
# src/wiki/markdown.py
#
# Markdown 제목 계층을 섹션 주소로 해석하고 섹션 패치를 적용합니다.
#
# Classes
#   - MarkdownStructureError : Markdown 제목 구조 또는 섹션 패치 검증 예외
#
# Functions
#   - document_revision(content: str) -> str : Markdown 본문의 안정적인 revision 해시를 계산합니다.
#   - parse_markdown_sections(content: str) -> dict[tuple[str, ...], MarkdownSection] : H2 이하 제목을 섹션 경로로 파싱합니다.
#   - apply_section_patches(document: WikiDocument, patches: list[SectionPatch]) -> str : 검증된 섹션 교체 결과를 반환합니다.
# ================================

from __future__ import annotations

from hashlib import sha256
import re

from src.wiki.models import MarkdownSection, SectionPatch, WikiDocument


_HEADING_RE = re.compile(
    r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?(?:\r?\n)?$"
)
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*?)(?:\r?\n)?$")


class MarkdownStructureError(ValueError):
    """Markdown 제목 구조나 섹션 패치가 유효하지 않을 때 발생합니다."""


def document_revision(content: str) -> str:
    """Markdown 본문의 안정적인 SHA-256 revision 해시를 반환합니다."""
    return sha256(content.encode("utf-8")).hexdigest()


def _heading_rows(content: str) -> list[tuple[int, str, int, int]]:
    """코드 펜스 밖의 ATX 제목과 문자 범위를 반환합니다."""
    rows: list[tuple[int, str, int, int]] = []
    offset = 0
    active_fence: tuple[str, int] | None = None
    active_html_comment = False
    active_obsidian_comment = False
    active_frontmatter = False

    for line_number, line in enumerate(content.splitlines(keepends=True)):
        stripped_line = line.strip()
        if line_number == 0 and stripped_line == "---":
            active_frontmatter = True
            offset += len(line)
            continue
        if active_frontmatter:
            if stripped_line in {"---", "..."}:
                active_frontmatter = False
            offset += len(line)
            continue

        fence_match = _FENCE_OPEN_RE.match(line)
        if active_fence is not None:
            if fence_match:
                token = fence_match.group(1)
                trailing = fence_match.group(2)
                if (
                    token[0] == active_fence[0]
                    and len(token) >= active_fence[1]
                    and not trailing.strip()
                ):
                    active_fence = None
            offset += len(line)
            continue
        if fence_match:
            token = fence_match.group(1)
            info = fence_match.group(2)
            if token[0] != "`" or "`" not in info:
                active_fence = (token[0], len(token))
                offset += len(line)
                continue

        if active_html_comment:
            if "-->" in line:
                active_html_comment = False
            offset += len(line)
            continue
        if "<!--" in line:
            after_start = line.split("<!--", 1)[1]
            if "-->" not in after_start:
                active_html_comment = True
            offset += len(line)
            continue

        if active_obsidian_comment:
            if "%%" in line:
                active_obsidian_comment = False
            offset += len(line)
            continue
        if "%%" in line:
            if line.count("%%") % 2 == 1:
                active_obsidian_comment = True
            offset += len(line)
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            title = heading_match.group(2).strip()
            rows.append((len(heading_match.group(1)), title, offset, offset + len(line)))
        offset += len(line)
    return rows


def parse_markdown_sections(content: str) -> dict[tuple[str, ...], MarkdownSection]:
    """H2 이하 제목을 유일한 계층 경로로 파싱합니다.

    H1은 문서 정체성으로 취급하고 section_path에서는 제외합니다. 각 섹션은 해당
    제목부터 다음 동급 또는 상위 제목 직전까지를 포함합니다.
    """
    rows = _heading_rows(content)
    sections: dict[tuple[str, ...], MarkdownSection] = {}
    heading_stack: dict[int, str] = {}

    for index, (level, title, start, _line_end) in enumerate(rows):
        if level == 1:
            heading_stack.clear()
            continue

        for stack_level in tuple(heading_stack):
            if stack_level >= level:
                del heading_stack[stack_level]
        heading_stack[level] = title
        path = tuple(heading_stack[key] for key in sorted(heading_stack) if key >= 2)
        if not path:
            continue
        if path in sections:
            joined = " > ".join(path)
            raise MarkdownStructureError(f"Duplicate Markdown section path: {joined}")

        end = len(content)
        for next_level, _next_title, next_start, _next_line_end in rows[index + 1:]:
            if next_level <= level:
                end = next_start
                break
        sections[path] = MarkdownSection(
            path=path,
            level=level,
            start=start,
            end=end,
            markdown=content[start:end],
        )
    return sections


def _validate_replacement(section: MarkdownSection, replacement: str) -> str:
    """교체 Markdown이 같은 제목을 유지하고 섹션 밖으로 넘치지 않는지 검사합니다."""
    normalized = replacement.strip()
    rows = _heading_rows(normalized)
    if not rows:
        raise MarkdownStructureError("Section replacement must start with a Markdown heading")

    first_level, first_title, first_start, _first_end = rows[0]
    if first_start != 0 or first_level != section.level or first_title != section.path[-1]:
        expected = "#" * section.level + " " + section.path[-1]
        raise MarkdownStructureError(f"Section replacement must start with {expected!r}")
    for level, title, _start, _end in rows[1:]:
        if level <= section.level:
            raise MarkdownStructureError(
                f"Section replacement escapes its boundary with heading {title!r}"
            )
    return normalized + "\n\n"


def apply_section_patches(document: WikiDocument, patches: list[SectionPatch]) -> str:
    """문서에 겹치지 않는 섹션 패치를 적용한 새 Markdown 본문을 반환합니다."""
    sections = parse_markdown_sections(document.content)
    replacements: list[tuple[int, int, str]] = []

    for patch in patches:
        if patch.document != document.path:
            raise MarkdownStructureError(
                f"Patch document {patch.document!r} does not match {document.path!r}"
            )
        if patch.base_revision != document.revision:
            raise MarkdownStructureError(f"Stale revision for {document.path}")
        section = sections.get(tuple(patch.section_path))
        if section is None:
            joined = " > ".join(patch.section_path)
            raise MarkdownStructureError(f"Unknown section in {document.path}: {joined}")
        replacement = _validate_replacement(section, patch.replacement_markdown)
        replacements.append((section.start, section.end, replacement))

    ordered = sorted(replacements, key=lambda item: item[0])
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise MarkdownStructureError("Section patches must not target overlapping sections")

    updated = document.content
    for start, end, replacement in reversed(ordered):
        updated = updated[:start] + replacement + updated[end:]
    parse_markdown_sections(updated)
    return updated
