# ================================
# src/wiki/character_postprocess.py
#
# Wiki 캐릭터의 동적 성격 변화 원장과 선택적 생식 상태를 revision-safe patch로 계획합니다.
#
# Functions
#   - reproductive_state_fields(markdown: str) -> dict[str, str] : Reproductive State section의 canonical 단일 행 필드를 반환합니다.
#   - authored_cycle_character_titles(documents: list[WikiDocument]) -> list[str] : Menstrual cycle이 enabled인 thread character의 H1 제목을 반환합니다.
#   - plan_personality_drift(documents: list[WikiDocument], actor_response: str, pending: PendingWikiCommit, actor_profile_id: str, model_name: str) -> list[SectionPatch] : durable 변화 뒤 성격 변화 원장 patch를 계획합니다.
#   - plan_organic_state(documents: list[WikiDocument], actor_response: str, pending: PendingWikiCommit, actor_profile_id: str, player_profile_id: str, model_name: str) -> tuple[list[SectionPatch], str | None] : 결정적 주기 tick과 명시적 위험 사건을 생식 상태에 반영합니다.
# ================================

from __future__ import annotations

import hashlib
from pathlib import Path
import re

from src.core.llm import extract_json_from_llm, get_model, get_response_text
from src.simulation.state.apply.time_plan import parse_prose_header_datetime
from src.simulation.systems.world_dynamics.organic_models import (
    calculate_pregnancy_probability,
    normalize_contraception_value,
)
from src.wiki.context import document_body, scene_datetime_and_location
from src.wiki.frontmatter import parse_frontmatter
from src.wiki.markdown import document_revision, parse_markdown_sections
from src.wiki.models import PendingWikiCommit, SectionPatch, WikiDocument

_PROMPT_PATH = Path(__file__).parent / "prompts" / "personality_drift.md"
_CONTRACEPTION_PROMPT_PATH = Path(__file__).parent / "prompts" / "contraception_state.md"
_PERSONALITY_SENTINEL = (
    "- No durable personality change has occurred since the story began."
)
_FIELD_RE = re.compile(r"(?m)^-\s*([^:\n]+):\s*(.*?)\s*$")
_EXPLICIT_INTERNAL_RISK_RE = re.compile(
    r"질내사정"
    r"|(?:피임\s*없이|노콘|콘돔\s*없이).*?(?:안에|속에|질|자궁).*?(?:사정|쌌|쏟)"
    r"|(?:안에|속에|질|자궁).*?(?:피임\s*없이|노콘|콘돔\s*없이).*?(?:사정|쌌|쏟)"
    r"|(?:came|cum(?:med)?|ejaculated)\s+inside.*?(?:without\s+(?:a\s+)?condom|bareback)"
    r"|(?:without\s+(?:a\s+)?condom|bareback).*?(?:came|cum(?:med)?|ejaculated)\s+inside",
    re.IGNORECASE,
)
_CONTRACEPTION_GATE_KEYWORDS = (
    "피임",
    "피임약",
    "경구피임약",
    "먹는 피임약",
    "사후피임약",
    "응급피임약",
    "contraception",
    "birth control",
    "the pill",
    "plan b",
    "morning-after",
    "emergency contraception",
)
_PROTECTION_OOC_VALUES = {"none", "condom", "n/a"}


def _has_explicit_internal_risk(text: str) -> bool:
    """현재 행에 명시적이고 무방비한 질내사정 표현이 있는지 반환합니다."""
    return bool(_EXPLICIT_INTERNAL_RISK_RE.search(text or ""))


def _has_contraception_signal(text: str) -> bool:
    """간단한 부분 문자열 게이트로 피임 관련 언급이 있는지 반환합니다."""
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in _CONTRACEPTION_GATE_KEYWORDS)


def _latest_hidden_block(text: str, tag: str) -> str | None:
    """지정한 태그의 가장 마지막 완전한 hidden block 본문을 반환합니다."""
    source = str(text or "")
    lowered = source.lower()
    open_tag = f"<{tag.lower()}>"
    close_tag = f"</{tag.lower()}>"
    block: str | None = None
    search_from = 0
    while True:
        open_index = lowered.find(open_tag, search_from)
        if open_index < 0:
            return block
        body_start = open_index + len(open_tag)
        close_index = lowered.find(close_tag, body_start)
        if close_index < 0:
            return block
        block = source[body_start:close_index]
        search_from = close_index + len(close_tag)


def _protection_ooc_value(actor_response: str) -> str | None:
    """엄격한 single-line Protection OOC 값이 있으면 canonical 값을 반환합니다."""
    block = _latest_hidden_block(actor_response, "ooc")
    if block is None:
        return None
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    line = lines[0]
    prefix = "- protection:"
    lowered = line.lower()
    if not lowered.startswith(prefix):
        return None
    value = lowered[len(prefix):].strip()
    return value if value in _PROTECTION_OOC_VALUES else None


def _visible_response_evidence(text: str) -> str:
    """숨김 블록 밖 가시 응답의 첫 비어 있지 않은 한 줄을 반환합니다."""
    hidden_tag: str | None = None
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        lowered = stripped.lower()
        if hidden_tag is not None:
            if lowered == f"</{hidden_tag}>":
                hidden_tag = None
            continue
        if lowered in {"<analyze>", "<ooc>"}:
            hidden_tag = lowered.removeprefix("<").removesuffix(">")
            continue
        if stripped:
            return stripped
    return ""


def reproductive_state_fields(markdown: str) -> dict[str, str]:
    """Reproductive State section의 canonical 단일 행 필드를 반환합니다."""
    return {label.strip(): value.strip() for label, value in _FIELD_RE.findall(markdown)}


def authored_cycle_character_titles(documents: list[WikiDocument]) -> list[str]:
    """Menstrual cycle이 enabled인 thread character의 H1 제목을 반환합니다."""
    titles: list[str] = []
    section_path = ("현재 상태", "Reproductive State")
    for document in documents:
        metadata = document.metadata
        if metadata is None or metadata.type != "character":
            continue
        section = parse_markdown_sections(document.content).get(section_path)
        if section is None:
            continue
        fields = reproductive_state_fields(section.markdown)
        if fields.get("Menstrual cycle", "disabled").lower() != "enabled":
            continue
        title = next(
            (
                line.removeprefix("# ").strip()
                for line in document_body(document.content).splitlines()
                if line.startswith("# ")
            ),
            "",
        )
        if title:
            titles.append(title)
    return titles


def _character_title(document: WikiDocument) -> str:
    """캐릭터 문서의 H1 제목을 반환합니다."""
    return next(
        (
            line.removeprefix("# ").strip()
            for line in document_body(document.content).splitlines()
            if line.startswith("# ")
        ),
        "",
    )


def _actor_character(
    documents: list[WikiDocument],
    actor_profile_id: str,
) -> WikiDocument | None:
    """활성 Actor profile에 대응하는 thread character 문서를 반환합니다."""
    return next(
        (
            document
            for document in documents
            if document.metadata is not None
            and document.metadata.type == "character"
            and document.metadata.profile_id == actor_profile_id
        ),
        None,
    )


def _evidence_quote(text: str, *, pregnancy_signal: bool = False) -> str:
    """Accepted Actor Response에서 후처리 근거로 쓸 정확한 한 행을 반환합니다."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if pregnancy_signal:
        return next(
            (line for line in lines if _has_explicit_internal_risk(line)),
            "",
        )
    return lines[0] if lines else ""


def _patch(
    document: WikiDocument,
    section_path: tuple[str, ...],
    replacement: str,
    evidence: str,
) -> SectionPatch | None:
    """존재하는 section 하나에 대한 revision-safe patch를 반환합니다."""
    section = parse_markdown_sections(document.content).get(section_path)
    if section is None or not evidence:
        return None
    return SectionPatch(
        document=document.path,
        base_revision=document.revision,
        base_section_revision=document_revision(section.markdown),
        base_markdown=section.markdown,
        section_path=section_path,
        replacement_markdown=replacement,
        evidence=evidence,
        evidence_source="actor_response",
        confidence=1.0,
    )


def _has_durable_trigger(
    documents: list[WikiDocument],
    pending: PendingWikiCommit,
) -> bool:
    """현재 pending에 durable relationship 변화나 Event 생성이 있는지 반환합니다."""
    types = {
        document.path: document.metadata.type
        for document in documents
        if document.metadata is not None
    }
    if any(types.get(patch.document) == "relationship" for patch in pending.patches):
        return True
    return any(
        (metadata := parse_frontmatter(creation.content)) is not None
        and metadata.type == "event"
        for creation in pending.creations
    )


async def plan_personality_drift(
    documents: list[WikiDocument],
    actor_response: str,
    pending: PendingWikiCommit,
    actor_profile_id: str,
    model_name: str,
) -> list[SectionPatch]:
    """Durable trigger가 있을 때만 정적 성격을 건드리지 않는 원장 bullet을 계획합니다."""
    character = _actor_character(documents, actor_profile_id)
    evidence = _evidence_quote(actor_response)
    section_path = ("현재 상태", "Personality Change Ledger")
    if character is None or not evidence or not _has_durable_trigger(documents, pending):
        return []
    section = parse_markdown_sections(character.content).get(section_path)
    if section is None:
        return []
    trigger_context = [
        patch.replacement_markdown
        for patch in pending.patches
        if patch.document != character.path
    ]
    trigger_context.extend(
        document_body(creation.content)
        for creation in pending.creations
        if (metadata := parse_frontmatter(creation.content)) is not None
        and metadata.type == "event"
    )
    model = get_model(
        model_name,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
    )
    response = await model.generate_content_async(
        "\n\n".join(
            [
                "## Current Character",
                document_body(character.content),
                "## Durable Trigger",
                "\n\n".join(trigger_context),
                "## Accepted Turn",
                actor_response,
            ]
        ),
        generation_config={
            "temperature": 0.3,
            "max_output_tokens": 512,
            "response_mime_type": "application/json",
            "log_source": "wiki_personality_drift",
        },
    )
    payload = extract_json_from_llm(
        get_response_text(response),
        source="wiki_personality_drift",
        strict=True,
    )
    entry = str(payload.get("ledger_entry") or "").strip() if isinstance(payload, dict) else ""
    if not entry or "\n" in entry or entry.startswith("- "):
        return []
    bullets = [
        line
        for line in section.markdown.splitlines()[1:]
        if line.strip() and line.strip() != _PERSONALITY_SENTINEL
    ]
    new_bullet = f"- {entry}"
    if new_bullet in bullets:
        return []
    replacement = "\n".join(
        ["### Personality Change Ledger", "", *bullets, new_bullet]
    )
    patch = _patch(character, section_path, replacement, evidence)
    return [patch] if patch is not None else []


def _deterministic_roll(pending: PendingWikiCommit, count: int) -> float:
    """같은 pending과 누적 횟수에 항상 같은 0~1 roll을 반환합니다."""
    seed = f"{pending.commit_id}:{count}".encode("utf-8")
    return int(hashlib.sha256(seed).hexdigest()[:16], 16) / float(16**16)


async def _detect_contraception_updates(
    actor_response: str,
    character_name: str,
    current_contraception: str,
    model_name: str,
) -> tuple[str | None, bool, str]:
    """피임 관련 언급이 있을 때만 active character의 상태 변화와 응급피임 이벤트를 판정합니다."""
    if not _has_contraception_signal(actor_response) or not model_name.strip():
        return None, False, ""
    model = get_model(
        model_name,
        system_prompt=_CONTRACEPTION_PROMPT_PATH.read_text(encoding="utf-8"),
    )
    response = await model.generate_content_async(
        "\n\n".join(
            [
                "## Active Character",
                character_name or "The active character",
                "## Current Reproductive State",
                f"- Contraception: {current_contraception}",
                "## Accepted Turn",
                actor_response,
            ]
        ),
        generation_config={
            "temperature": 0.1,
            "max_output_tokens": 256,
            "response_mime_type": "application/json",
            "log_source": "wiki_contraception_state",
        },
    )
    payload = extract_json_from_llm(
        get_response_text(response),
        source="wiki_contraception_state",
        strict=True,
    )
    if not isinstance(payload, dict):
        return None, False, ""
    evidence = str(payload.get("evidence_quote") or "").strip()
    if ("\n" in evidence or "\r" in evidence) or (evidence and evidence not in actor_response):
        evidence = ""
    changed = bool(payload.get("state_change_established") or False)
    emergency = bool(payload.get("emergency_contraception_taken") or False)
    if (changed or emergency) and not evidence:
        return None, False, ""
    new_contraception = (
        normalize_contraception_value(payload.get("new_contraception"))
        if changed
        else None
    )
    return new_contraception, emergency, evidence


async def plan_organic_state(
    documents: list[WikiDocument],
    actor_response: str,
    pending: PendingWikiCommit,
    actor_profile_id: str,
    player_profile_id: str,
    model_name: str,
) -> tuple[list[SectionPatch], str | None]:
    """Author가 cycle을 켠 Actor character에만 날짜 tick과 임신 판정을 적용합니다."""
    character = _actor_character(documents, actor_profile_id)
    section_path = ("현재 상태", "Reproductive State")
    if character is None:
        return [], None
    section = parse_markdown_sections(character.content).get(section_path)
    if section is None:
        return [], None
    fields = reproductive_state_fields(section.markdown)
    if fields.get("Menstrual cycle", "disabled").lower() != "enabled":
        return [], None

    evidence = _evidence_quote(actor_response)
    header_time = parse_prose_header_datetime(actor_response)
    scene = next(
        (
            document
            for document in documents
            if document.metadata is not None and document.metadata.type == "scene"
        ),
        None,
    )
    elapsed_days = 0
    if scene is not None and header_time is not None:
        current_time, _location = scene_datetime_and_location(scene.content)
        elapsed_days = max(0, (header_time.date() - current_time.date()).days)

    cycle_day = max(1, min(28, int(fields.get("Cycle day", "1") or 1)))
    pregnant = fields.get("Pregnant", "no").lower() == "yes"
    pregnancy_day = max(0, int(fields.get("Pregnancy day", "0") or 0))
    count = max(
        0,
        int(fields.get("Internal ejaculation count this cycle", "0") or 0),
    )
    contraception = normalize_contraception_value(fields.get("Contraception"))
    other_parent = fields.get("Other parent", "unknown") or "unknown"
    changed = False
    if elapsed_days:
        changed = True
        if pregnant:
            pregnancy_day += elapsed_days
        else:
            cycle_day = ((cycle_day - 1 + elapsed_days) % 28) + 1
            if elapsed_days >= 28 or cycle_day < int(fields.get("Cycle day", "1") or 1):
                count = 0

    try:
        new_contraception, emergency_contraception, contraception_evidence = (
            await _detect_contraception_updates(
                actor_response,
                _character_title(character),
                contraception,
                model_name,
            )
        )
    except Exception:
        new_contraception, emergency_contraception, contraception_evidence = None, False, ""

    if new_contraception is not None and new_contraception != contraception:
        contraception = new_contraception
        changed = True
        evidence = contraception_evidence or evidence
    if emergency_contraception:
        changed = changed or count != 0
        count = 0
        evidence = contraception_evidence or evidence

    ooc_message: str | None = None
    protection_ooc = _protection_ooc_value(actor_response)
    risk_evidence = _evidence_quote(actor_response, pregnancy_signal=True)
    if protection_ooc == "none":
        risk_this_turn = True
        risk_evidence = (
            risk_evidence
            or _visible_response_evidence(actor_response)
            or _evidence_quote(actor_response)
        )
    elif protection_ooc in {"condom", "n/a"}:
        risk_this_turn = False
    else:
        risk_this_turn = bool(
            risk_evidence and _has_explicit_internal_risk(actor_response)
        )
    if not pregnant and risk_this_turn:
        evidence = risk_evidence
        count += 1
        changed = True
        probability = calculate_pregnancy_probability(
            cycle_day,
            count,
            contraception=contraception,
        )
        if _deterministic_roll(pending, count) < probability:
            pregnant = True
            pregnancy_day = 1
            count = 0
            other_parent = "player character" if player_profile_id else "unknown"
            title = _character_title(character) or "The active character"
            ooc_message = (
                f"*[시스템] {title}의 임신 상태가 Wiki 정본에 반영되었습니다. "
                f"(임신 1일째, 주기 {cycle_day}일째)*"
            )
    if not changed or not evidence:
        return [], None
    replacement = (
        "### Reproductive State\n\n"
        "- Menstrual cycle: enabled\n"
        f"- Contraception: {contraception}\n"
        f"- Cycle day: {cycle_day}\n"
        f"- Pregnant: {'yes' if pregnant else 'no'}\n"
        f"- Pregnancy day: {pregnancy_day}\n"
        f"- Internal ejaculation count this cycle: {count}\n"
        f"- Other parent: {other_parent}"
    )
    patch = _patch(character, section_path, replacement, evidence)
    return ([patch] if patch is not None else []), ooc_message
