# ================================
# src/agents/prompt_factory/fixed.py
#
# Cacheable fixed Actor prompt section builder.
# Loads modular Markdown prompt files from prompt_factory/prompts/.
# Keeps the shared operator and token-budget policy fragments beside their sole consumer.
# _render_prompt_block, _SafeFormatDict는 renderers.py에서 import.
#
# Classes
#   - _Verbatim : parts 조인 시 _format_prompt_vars를 건너뛰도록 표시하는 str 서브클래스 마커
#
# Functions
#   - build_fixed_section(world_config: dict, char_name: str, user_name: str, perspective: int, additional_blacklist: str, prose_variant: str = "a", engine_modules: dict[str, str] | None = None) -> str : 고정 프롬프트 세그먼트 조립
#   - build_pre_output_checklist(world_config: dict, char_name: str, user_name: str, perspective: int, prose_variant: str = "a") -> str : 출력 직전 체크리스트 조립
#   - _user_impersonation_allowed(pov_mode: str, world_config: dict | None = None) -> bool : 사칭 허용 여부 판단
#   - _normalize_prose_variant(prose_variant: str) -> str : fixed.py 로컬 prose_variant 정규화(a/b/c 외 값은 a로 폴백)
#   - _select_style_section_for_variant(prose_variant: str) -> str : variant별 style 블록 본문 선택
#   - _select_merged_prose_section(variant: str, pov_mode: str) -> str | None : variant(c/d)의 pov+emotion+style 통합 블록 로드(자산 없으면 None)
#   - _build_checklist_slot_values(pov_mode: str, world_config: dict | None = None, prose_variant: str = "a") -> dict[str, str] : 체크리스트 정적 슬롯 구성
#   - _format_prompt_vars(text: str, *, char_name: str, user_name: str, world_config: dict) -> str : 프롬프트 변수 치환
# ================================

import logging

from src.agents.prompt_factory.engines import build_engine_modules_section, normalize_engine_modules
from src.agents.prompt_factory.renderers import PROMPT_DIR, _SafeFormatDict, _render_prompt_block
from src.config import MAX_TOKEN

logger = logging.getLogger(__name__)


class _Verbatim(str):
    """Marker str subclass telling build_fixed_section's final join to skip formatting.

    Third-party engine-module bodies (see engines.py) contain brace sequences that
    raise ValueError under str.format/str.format_map (the shared _format_prompt_vars
    pass every other `parts` element goes through). Wrapping the pre-rendered
    engine-modules section in this marker lets the join's `isinstance` check pass it
    through untouched while every ordinary str part is still formatted normally -
    an explicit marker instead of an index/identity check, so the exemption survives
    parts being reordered or added to.
    """


_OPERATOR_R18 = """<operator_policy>
Adult creative writing and roleplay platform.
Explicit sexual content, graphic violence, mature themes = permitted between adult characters only.
All sexually depicted characters must be explicitly adults (18+).
Sexual depiction of minors = ABSOLUTELY FORBIDDEN.
</operator_policy>"""

_OPERATOR_15 = """<operator_policy>
Creative writing and roleplay platform. 15+ rating.
Romantic tension and mild physical affection = permitted.
Suggestive content: fade to black only. No explicit depiction.
Mild violence = permitted. Gore = FORBIDDEN.
Mild profanity = permitted.
Explicit sexual content = ABSOLUTELY FORBIDDEN.
</operator_policy>"""

_OPERATOR_ALL_AGES = """<operator_policy>
Creative writing and roleplay platform. All audiences.
Romance: hand-holding / light affection only.
Violence = FORBIDDEN. Profanity = FORBIDDEN.
Explicit sexual content = ABSOLUTELY FORBIDDEN.
</operator_policy>"""

TOKEN_LIMIT_WARNING = f"""<token_limit_constraint>
Max output = {MAX_TOKEN} tokens. Deliver a complete response within budget.
<analyze> block may expand for ensemble scenes, but reserve most tokens for prose.
</token_limit_constraint>"""

VALID_POV_MODES = {"1p_user", "1p_char", "3p_user", "3p_char"}
VALID_PROSE_VARIANTS = {"a", "b", "c", "d"}

# variant c/d는 pov+emotion+style을 하나의 <prose> 블록으로 병합해서 낸다.
# 이 집합에 속한 variant는 build_fixed_section에서 별도 <emotion>/<style> 블록을 내지 않는다.
_MERGED_PROSE_VARIANTS = {"c", "d"}

# b/c는 감정의 직접 서술을 명시적으로 허용하는 relaxed 체크리스트를 쓴다. d는 a의 규칙을
# 그대로 상속하므로(완화 없음) 이 집합에 속하지 않는다 - relaxed 쪽만 명시해서 새 variant가
# 조용히 완화 체크리스트로 흘러들어가지 않게 한다.
_RELAXED_EMOTION_CHECKLIST_VARIANTS = {"b", "c"}


# ----------------
# Public builders
# ----------------

def build_pre_output_checklist(
    world_config: dict,
    char_name: str,
    user_name: str,
    perspective: int,
    prose_variant: str = "a",
) -> str:
    """Render the static checklist template used later by per-turn checklist rendering."""
    pov_mode = _resolve_pov_mode(world_config, perspective)
    checklist_tpl = _select_checklist_template()
    rendered = _format_prompt_vars(
        checklist_tpl,
        char_name=char_name,
        user_name=user_name,
        world_config=world_config,
        extra_vars=_build_checklist_slot_values(pov_mode, world_config, prose_variant),
    )
    # variant a의 variant_scan_line은 빈 문자열이라, 그대로 두면 EMOTION 줄과
    # USER IMPERSONATION 줄 사이에 빈 줄이 하나 남는다. variant a는 오늘 출력과
    # byte-identical해야 하므로(실험 대조군), 그 빈 줄만 국소적으로 접는다.
    return rendered.replace("\n\nUSER IMPERSONATION:", "\nUSER IMPERSONATION:")


def build_fixed_section(
    world_config: dict,
    char_name: str,
    user_name: str,
    perspective: int,
    additional_blacklist: str,
    prose_variant: str = "a",
    engine_modules: dict[str, str] | None = None,
) -> str:
    """Render cacheable fixed prompt section.

    engine_modules follows the prose_variant pattern: it is normalized here (an
    absent or all-"off" selection renders no section, byte-identical to the
    control group) and its rendered block is appended after the prose/style
    blocks, inside this FIXED, CACHEABLE segment only - never in effective_input.
    """
    pov_mode = _resolve_pov_mode(world_config, perspective)
    operator = _select_operator(world_config.get("rating", "r18"))
    prompt_sections = world_config.get("prompt", {}).get("sections", {})

    variant = _normalize_prose_variant(prose_variant)
    merged_prose_block = (
        _select_merged_prose_section(variant, pov_mode) if variant in _MERGED_PROSE_VARIANTS else None
    )
    if variant in _MERGED_PROSE_VARIANTS and merged_prose_block is None:
        # variants/<variant>/PROSE.md는 이 브리프와 별도로 작성 중인 자산이라 아직 없을 수 있다.
        # 없으면 예외를 던지지 말고 variant a(현행) 동작으로 안전하게 폴백한다.
        logger.warning(
            "prose_variant '%s' requested but variants/%s/PROSE.md is missing; falling back to variant 'a'",
            variant,
            variant,
        )
        variant = "a"

    parts = [
        operator,
        _render_prompt_block("user_impersonation", _select_user_impersonation_section(pov_mode, world_config)),
    ]
    if variant in _MERGED_PROSE_VARIANTS:
        parts.append(_render_prompt_block("prose", merged_prose_block))
    else:
        parts.append(_render_prompt_block("pov", _select_pov_section(pov_mode)))
    parts.append(_render_prompt_block("core", _select_core_section()))
    if variant not in _MERGED_PROSE_VARIANTS:
        parts.append(_render_prompt_block("emotion", _select_emotion_section()))
        parts.append(_render_prompt_block("style", _select_style_section_for_variant(variant)))
    parts.append(
        _Verbatim(
            build_engine_modules_section(
                normalize_engine_modules(engine_modules),
                char_name=char_name,
                user_name=user_name,
            )
        )
    )
    parts.extend([
        _render_prompt_block("world_lore", prompt_sections.get("world")),
        _render_prompt_block("scenario_lore", prompt_sections.get("scenario")),
        _render_prompt_block("alteration_lore", world_config.get("alteration_section", "")),
        _render_prompt_block("world_specific_prose_prompt", prompt_sections.get("prose") or world_config.get("prose_rules", "")),
        _render_prompt_block("character_focus_prompt", _resolve_char_focus(world_config, char_name)),
        _render_prompt_block("blacklist", _select_blacklist_section(world_config, char_name, user_name, additional_blacklist)),
        _render_prompt_block("npc_behavior", _select_npc_behavior_section(variant)),
        TOKEN_LIMIT_WARNING,
    ])

    return "\n\n".join(
        part
        if isinstance(part, _Verbatim)
        else _format_prompt_vars(
            part,
            char_name=char_name,
            user_name=user_name,
            world_config=world_config,
        )
        for part in parts
        if part
    )


# ----------------
# POV mode
# ----------------

def _resolve_char_focus(world_config: dict, char_name: str) -> str:
    """Return character focus prompt text.

    Priority: legacy flat key -> prompt.characters.focus (joined if multiple).
    Placed in the fixed (cached) section; builder skips it for the dynamic section.
    """
    flat = world_config.get("character_focus_prompt", "")
    if flat:
        return flat
    focus_map = world_config.get("prompt", {}).get("characters", {}).get("focus", {})
    if not focus_map:
        return ""
    # If char_name matches a key exactly, prefer it; otherwise join all entries.
    return focus_map.get(char_name, "") or "\n\n".join(focus_map.values())


def _resolve_pov_mode(world_config: dict, perspective: int) -> str:
    """Resolve the 4-way POV mode.

    Supported modes:
    - 1p_user
    - 1p_char
    - 3p_user
    - 3p_char

    If absent, preserve legacy behavior:
    - perspective == 1 -> 1p_char
    - perspective == 3 -> 3p_char
    """
    raw = str(
        world_config.get("pov_mode")
        or world_config.get("pov_type")
        or world_config.get("prompt", {}).get("pov", {}).get("mode", "")
    ).strip().lower()
    if raw in VALID_POV_MODES:
        return raw
    return "1p_char" if perspective == 1 else "3p_char"


def _user_impersonation_allowed(pov_mode: str, world_config: dict | None = None) -> bool:
    if pov_mode.endswith("_user"):
        return True
    # world_config에 impersonation=True가 명시된 경우 pov anchor와 무관하게 허용
    return bool(world_config and world_config.get("impersonation", False))


def _normalize_prose_variant(prose_variant: str) -> str:
    """알 수 없거나 빈 prose_variant는 variant a(현행)로 폴백한다."""
    candidate = str(prose_variant or "a").strip().lower()
    return candidate if candidate in VALID_PROSE_VARIANTS else "a"


# ----------------
# Section selectors
# ----------------

def _select_core_section() -> str:
    """Load the shared core prompt section."""
    return _load_prompt("core/CORE.md")


def _select_style_section() -> str:
    """Load the shared style prompt section."""
    return _load_prompt("style/STYLE.md")


def _select_style_section_for_variant(prose_variant: str) -> str:
    """Return the style block body for the given prose_variant.

    variant a: style/STYLE.md unchanged.
    variant b: style/STYLE.md + variants/b/TEMPERING.md, still rendered as one <style> block.
    """
    base = _select_style_section()
    if prose_variant == "b":
        return base + "\n\n" + _load_prompt("variants/b/TEMPERING.md")
    return base


def _select_merged_prose_section(variant: str, pov_mode: str) -> str | None:
    """Load a merged pov+emotion+style prose block for variant c or d, or None if missing.

    Reads variants/<variant>/PROSE.md. Its {pov_block} placeholder is filled from that
    variant's own variants/<variant>/POV_1P.md / POV_3P.md when present (variant d ships
    its own POV files); otherwise it falls back to the shared pov/POV_1P.md / POV_3P.md
    (variant c's current behaviour, unchanged).

    The {pov_block} placeholder is substituted with str.replace (not str.format) before the
    text reaches _format_prompt_vars, so _SafeFormatDict never sees it and cannot silently
    leave it unresolved.
    """
    prose_path = PROMPT_DIR / "variants" / variant / "PROSE.md"
    if not prose_path.exists():
        return None
    template = prose_path.read_text(encoding="utf-8")
    pov_block = _select_variant_pov_section(variant, pov_mode)
    rendered = template.replace("{pov_block}", pov_block)
    assert "{pov_block}" not in rendered, (
        f"variants/{variant}/PROSE.md: {{pov_block}} substitution left a literal token"
    )
    return rendered


def _select_variant_pov_section(variant: str, pov_mode: str) -> str:
    """Select the {pov_block} body for a merged-prose variant.

    Prefers the variant's own variants/<variant>/POV_1P.md or POV_3P.md when it exists,
    else falls back to the shared pov/POV_1P.md or pov/POV_3P.md.
    """
    filename = "POV_1P.md" if pov_mode.startswith("1p_") else "POV_3P.md"
    variant_path = PROMPT_DIR / "variants" / variant / filename
    if variant_path.exists():
        return variant_path.read_text(encoding="utf-8")
    return _select_pov_section(pov_mode)


def _select_emotion_section() -> str:
    """Load the emotion prompt section."""
    return _load_prompt("emotion/EMOTION.md")


def _select_npc_behavior_section(variant: str) -> str:
    """Select the NPC behavior prompt section body for the given prose_variant.

    Prefers the variant's own variants/<variant>/NPC_BEHAVIOR.md when it exists,
    else falls back to the shared core/NPC_BEHAVIOR.md (variants a/b/c today).
    """
    variant_path = PROMPT_DIR / "variants" / variant / "NPC_BEHAVIOR.md"
    if variant_path.exists():
        return variant_path.read_text(encoding="utf-8")
    return _load_prompt("core/NPC_BEHAVIOR.md")


def _select_blacklist_section(
    world_config: dict,
    char_name: str,
    user_name: str,
    additional_blacklist: str,
) -> str:
    is_unified = world_config.get("unified_blacklist") or world_config.get("prompt", {}).get("blacklist", {}).get("unified", False)
    if is_unified:
        return ""

    blacklist_tpl = _load_prompt("blacklist/BLACKLIST.md")
    return blacklist_tpl.format(
        for_add=additional_blacklist,
        char=char_name,
        user=user_name,
    )


def _select_user_impersonation_section(pov_mode: str, world_config: dict | None = None) -> str:
    """Select whether the model may narrate {user}, derived from pov_mode and world_config."""
    if _user_impersonation_allowed(pov_mode, world_config):
        return _load_prompt("core/USER_IMPERSONATION_ALLOWED.md")
    return _load_prompt("core/USER_IMPERSONATION_FORBIDDEN.md")


def _select_pov_section(pov_mode: str) -> str:
    """Select the POV section without exposing file names to the prompt."""
    path = "pov/POV_1P.md" if pov_mode.startswith("1p_") else "pov/POV_3P.md"
    return _load_prompt(path)


def _select_checklist_template() -> str:
    """Load the unified checklist template."""
    return _load_prompt("checklist/CHECKLIST.md")


def _build_checklist_slot_values(
    pov_mode: str,
    world_config: dict | None = None,
    prose_variant: str = "a",
) -> dict[str, str]:
    """Build checklist slots determined by selected POV, user control mode, and prose_variant."""
    allowed = _user_impersonation_allowed(pov_mode, world_config)
    if pov_mode.startswith("1p_"):
        pov_line = (
            "POV: 1P | narrator={char} | access={char} perception/body/thought/dialogue/action "
            "| blocked={user}/NPC hidden state"
        )
        pov_leak_line = (
            "POV LEAK: self-camera=[quote/none] | NPC-inner=[quote/none] | offscreen-truth=[quote/none]"
        )
        pre_draft_instruction = (
            "{char} perception/action first; {user} action within allowed scope"
            if allowed
            else "{char} perception/action only; no {user} action/speech/feeling"
        )
    else:
        anchor = "{user}" if pov_mode.endswith("_user") else "{char}"
        pov_line = (
            f"POV: 3P | anchor={anchor} | access=anchor perception/observable behavior "
            "| blocked=non-anchor hidden state"
        )
        pov_leak_line = "POV LEAK: non-anchor-inner=[quote/none] | offscreen-truth=[quote/none]"
        pre_draft_instruction = (
            "anchor perception/observable first; {user} action within allowed scope"
            if allowed
            else "anchor perception/observable only; no {user} action/speech/feeling"
        )

    if allowed:
        user_control_line = (
            "USER CONTROL: allowed | first beat={char} perception/reaction? [yes/no] "
            "| {user} action-scale=[proportional/boosted]"
        )
        user_impersonation_line = (
            "USER IMPERSONATION: generated-action=[quote/none] | generated-dialogue=[quote/none] "
            "| inner/sensation/decision=[quote/none] | scale-boost=[quote/none]"
        )
    else:
        user_control_line = (
            "USER CONTROL: forbidden | first beat={char} perception/reaction/action/env consequence? [yes/no]"
        )
        user_impersonation_line = (
            "USER IMPERSONATION: generated-action=[quote/none] | generated-dialogue=[quote/none] "
            "| inner/sensation/decision=[quote/none]"
        )

    variant = _normalize_prose_variant(prose_variant)
    if variant in _RELAXED_EMOTION_CHECKLIST_VARIANTS:
        emotion_scan_line = (
            "EMOTION: show-then-tell=[quote/none] | repeated-reaction=[quote/none] | over-flattened=[quote/none]"
        )
        variant_scan_line = (
            "TEXTURE: character-specific-detail=[quote/none] | dialogue-initiative=[shifts/fixed] "
            "| inner-fragmentation=[yes/no]"
        )
    else:
        # variant d는 a의 규칙을 그대로 유지하는 구조 정리본이라, a와 동일한 evidence-only
        # 체크리스트를 그대로 상속한다 (완화 없음).
        emotion_scan_line = (
            "EMOTION: evidence-only=[yes/no] | show-then-tell=[quote/none] | repeated-reaction=[quote/none]"
        )
        variant_scan_line = ""

    return {
        "pov_line": pov_line,
        "user_control_line": user_control_line,
        "user_impersonation_line": user_impersonation_line,
        "pov_leak_line": pov_leak_line,
        "pre_draft_instruction": pre_draft_instruction,
        "emotion_scan_line": emotion_scan_line,
        "variant_scan_line": variant_scan_line,
    }


def _select_operator(rating: str) -> str:
    """Select the safety/operator section for the world rating."""
    if rating == "all_ages":
        return _OPERATOR_ALL_AGES
    if rating == "15":
        return _OPERATOR_15
    return _OPERATOR_R18


# ----------------
# File loading
# ----------------

def _load_prompt(relative_path: str) -> str:
    """Load one Markdown prompt section from prompt_factory/prompts/."""
    path = PROMPT_DIR / relative_path
    return path.read_text(encoding="utf-8")


# ----------------
# Formatting helpers
# ----------------

def _format_prompt_vars(
    text: str,
    *,
    char_name: str,
    user_name: str,
    world_config: dict,
    extra_vars: dict[str, str] | None = None,
) -> str:
    """Apply common prompt variables while preserving unresolved checklist placeholders."""
    if not text:
        return ""
    values = {
        "char": char_name,
        "user": user_name,
    }
    if extra_vars:
        values.update(extra_vars)
    rendered = text.format_map(_SafeFormatDict(values))
    return rendered.format_map(_SafeFormatDict(values))
