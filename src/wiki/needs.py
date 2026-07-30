# ================================
# src/wiki/needs.py
#
# Accepted Wiki 장면 시각 전진을 Actor 소유 캐릭터의 결정적 욕구 상태로 변환합니다.
#
# Functions
#   - plan_needs_decay(documents: list[WikiDocument], actor_response: str, actor_profile_id: str) -> list[SectionPatch] : 경과 시각에 따른 욕구 patch를 계획합니다.
# ================================

from __future__ import annotations

import re

from src.simulation.prose_headers import parse_prose_header_datetime
from src.simulation.systems.needs.models import NEED_BASE_RATES, NEED_DEFAULTS
from src.wiki.context import scene_datetime_and_location
from src.wiki.markdown import document_revision, parse_markdown_sections
from src.wiki.models import SectionPatch, WikiDocument

_NEEDS_ORDER = ("hunger", "rest", "social", "fun", "safety", "libido")
_NEEDS_LINE_RE = re.compile(r"(?m)^-\s*Needs:\s*(.+?)\s*$")
_CONDITION_LINE_RE = re.compile(r"(?m)^-\s*Condition:\s*(.*?)\s*$")
_VALUE_RE = re.compile(
    r"\b(hunger|rest|social|fun|safety|libido)=([01](?:\.\d+)?)\b"
)


def _source_header(actor_response: str) -> str:
    """Accepted Actor 응답에서 시각 근거가 되는 첫 prose header 원문을 반환합니다."""
    for line in actor_response.splitlines():
        stripped = line.strip()
        if stripped and parse_prose_header_datetime(stripped) is not None:
            return stripped
    return ""


def _current_values(section_markdown: str) -> dict[str, float]:
    """Canonical needs 행을 읽고 누락·오류 값은 Graph 기본값으로 보정합니다."""
    values = dict(NEED_DEFAULTS)
    match = _NEEDS_LINE_RE.search(section_markdown)
    if match is None:
        return values
    for name, raw_value in _VALUE_RE.findall(match.group(1)):
        values[name] = min(1.0, max(0.0, float(raw_value)))
    return values


def _render_needs(
    values: dict[str, float],
    section_markdown: str,
) -> str:
    """결정적 욕구·압력을 렌더링하되 별도 상태 판정인 Condition은 보존합니다."""
    serialized = "; ".join(f"{name}={values[name]:.4f}" for name in _NEEDS_ORDER)
    pressures = [name for name in _NEEDS_ORDER if values[name] >= 0.8]
    pressure_text = ", ".join(pressures) if pressures else "none"
    condition_match = _CONDITION_LINE_RE.search(section_markdown)
    condition = (
        condition_match.group(1).strip()
        if condition_match is not None and condition_match.group(1).strip()
        else "stable"
    )
    return (
        "### 욕구와 컨디션\n\n"
        f"- Needs: {serialized}\n"
        f"- Active pressure: {pressure_text}\n"
        f"- Condition: {condition}"
    )


def plan_needs_decay(
    documents: list[WikiDocument],
    actor_response: str,
    actor_profile_id: str,
) -> list[SectionPatch]:
    """경과한 in-world 분만큼 Actor 소유 캐릭터 욕구를 단조 증가시킵니다."""
    header_time = parse_prose_header_datetime(actor_response)
    evidence = _source_header(actor_response)
    if header_time is None or not evidence or not actor_profile_id:
        return []
    scene = next(
        (
            document
            for document in documents
            if document.metadata is not None and document.metadata.type == "scene"
        ),
        None,
    )
    character = next(
        (
            document
            for document in documents
            if document.metadata is not None
            and document.metadata.type == "character"
            and document.metadata.profile_id == actor_profile_id
        ),
        None,
    )
    if scene is None or character is None:
        return []
    current_time, _location = scene_datetime_and_location(scene.content)
    elapsed_minutes = (header_time - current_time).total_seconds() / 60.0
    if elapsed_minutes <= 0:
        return []
    section_path = ("현재 상태", "욕구와 컨디션")
    section = parse_markdown_sections(character.content).get(section_path)
    if section is None:
        return []
    values = _current_values(section.markdown)
    for name in _NEEDS_ORDER:
        if name == "safety":
            continue
        values[name] = round(
            min(1.0, values[name] + NEED_BASE_RATES[name] * elapsed_minutes),
            4,
        )
    replacement = _render_needs(values, section.markdown)
    if replacement == section.markdown.strip():
        return []
    return [
        SectionPatch(
            document=character.path,
            base_revision=character.revision,
            base_section_revision=document_revision(section.markdown),
            base_markdown=section.markdown,
            section_path=section_path,
            replacement_markdown=replacement,
            evidence=evidence,
            evidence_source="actor_response",
            confidence=1.0,
        )
    ]
