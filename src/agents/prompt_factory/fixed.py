# ================================
# src/agents/prompt_factory/fixed.py
#
# Cacheable fixed Actor prompt section builder.
# Loads modular Markdown prompt files from prompt_factory/prompt/.
# Falls back to legacy prompt_sections.py constants when prompt files are missing.
# _render_prompt_block, _SafeFormatDict는 renderers.py에서 import.
#
# Functions
#   - build_fixed_section(...) -> str : 고정 프롬프트 세그먼트 조립
#   - build_pre_output_checklist(...) -> str : 출력 직전 체크리스트 조립
#   - _user_impersonation_allowed(pov_mode, world_config) -> bool : 사칭 허용 여부 판단
#   - _format_prompt_vars(text, *, char_name, user_name, world_config) -> str : 프롬프트 변수 치환
# ================================

from pathlib import Path

from src.agents.prompt_factory.renderers import _SafeFormatDict, _render_prompt_block
from src.agents.prompt_factory.prompt_sections import (
    BLACKLIST_SECTION,
    EMOTION_ENGINE,
    NPC_BEHAVIOR_SECTION,
    TOKEN_LIMIT_WARNING,
    _CHECKLIST_1P,
    _CHECKLIST_3P,
    _CORE_1P,
    _CORE_3P,
    _IMPERSONATION_HEADER,
    _OPERATOR_15,
    _OPERATOR_ALL_AGES,
    _OPERATOR_R18,
    _STYLE_1P,
    _STYLE_3P,
)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

VALID_POV_MODES = {"1p_user", "1p_char", "3p_user", "3p_char"}


# ----------------
# Public builders
# ----------------

def build_pre_output_checklist(
    world_config: dict,
    char_name: str,
    user_name: str,
    perspective: int,
) -> str:
    """Render the static checklist template used later by per-turn checklist rendering."""
    pov_mode = _resolve_pov_mode(world_config, perspective)
    checklist_tpl = _select_checklist_template(pov_mode)
    return _format_prompt_vars(
        checklist_tpl,
        char_name=char_name,
        user_name=user_name,
        world_config=world_config,
    )


def build_fixed_section(
    world_config: dict,
    char_name: str,
    user_name: str,
    perspective: int,
    additional_blacklist: str,
) -> str:
    """Render cacheable fixed prompt section."""
    pov_mode = _resolve_pov_mode(world_config, perspective)
    resolved_perspective = _perspective_from_pov_mode(pov_mode)

    operator = _select_operator(world_config.get("rating", "r18"))
    prompt_sections = world_config.get("prompt", {}).get("sections", {})

    parts = [
        operator,
        _render_prompt_block("user_impersonation", _select_user_impersonation_section(pov_mode, world_config)),
        _render_prompt_block("pov", _select_pov_section(pov_mode, world_config)),
        _render_prompt_block("core", _select_core_section(resolved_perspective)),
        _render_prompt_block("emotion", _select_emotion_section()),
        _render_prompt_block("style", _select_style_section(resolved_perspective)),
        _render_prompt_block("world_lore", prompt_sections.get("world")),
        _render_prompt_block("scenario_lore", prompt_sections.get("scenario")),
        _render_prompt_block("world_specific_prose_prompt", prompt_sections.get("prose") or world_config.get("prose_rules", "")),
        _render_prompt_block("character_focus_prompt", _resolve_char_focus(world_config, char_name)),
        _render_prompt_block("blacklist", _select_blacklist_section(world_config, char_name, user_name, additional_blacklist)),
        _render_prompt_block("npc_behavior", _select_npc_behavior_section()),
        TOKEN_LIMIT_WARNING,
    ]

    return "\n\n".join(
        _format_prompt_vars(
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


def _perspective_from_pov_mode(pov_mode: str) -> int:
    return 1 if pov_mode.startswith("1p_") else 3


def _user_impersonation_allowed(pov_mode: str, world_config: dict | None = None) -> bool:
    if pov_mode.endswith("_user"):
        return True
    # world_config에 impersonation=True가 명시된 경우 pov anchor와 무관하게 허용
    return bool(world_config and world_config.get("impersonation", False))


# ----------------
# Section selectors
# ----------------

def _select_core_section(perspective: int) -> str:
    if perspective == 1:
        return _load_prompt_or_legacy("core/CORE_1P.md", _CORE_1P)
    return _load_prompt_or_legacy("core/CORE_3P.md", _CORE_3P)


def _select_style_section(perspective: int) -> str:
    if perspective == 1:
        return _load_prompt_or_legacy("style/STYLE_1P.md", _STYLE_1P)
    return _load_prompt_or_legacy("style/STYLE_3P.md", _STYLE_3P)


def _select_emotion_section() -> str:
    return _load_prompt_or_legacy("emotion/EMOTION.md", EMOTION_ENGINE)


def _select_npc_behavior_section() -> str:
    # NPC behavior has not been split yet. Keep legacy fallback as the source of truth.
    return _load_prompt_or_legacy("core/NPC_BEHAVIOR.md", NPC_BEHAVIOR_SECTION)


def _select_blacklist_section(
    world_config: dict,
    char_name: str,
    user_name: str,
    additional_blacklist: str,
) -> str:
    is_unified = world_config.get("unified_blacklist") or world_config.get("prompt", {}).get("blacklist", {}).get("unified", False)
    if is_unified:
        return ""

    blacklist_tpl = _load_prompt_or_legacy("blacklist/BLACKLIST.md", BLACKLIST_SECTION)
    return blacklist_tpl.format(
        for_add=additional_blacklist,
        char=char_name,
        user=user_name,
    )


def _select_user_impersonation_section(pov_mode: str, world_config: dict | None = None) -> str:
    """Select whether the model may narrate {user}, derived from pov_mode and world_config."""
    if _user_impersonation_allowed(pov_mode, world_config):
        return _load_prompt_or_legacy("core/USER_IMPERSONATION_ALLOWED.md", "")
    return _load_prompt_or_legacy("core/USER_IMPERSONATION_FORBIDDEN.md", "")


def _select_pov_section(pov_mode: str, world_config: dict) -> str:
    """Select the POV section without exposing file names to the prompt."""
    paths = {
        "1p_user": "pov/POV_1P_USER_NARRATOR.md",
        "1p_char": "pov/POV_1P_CHAR_NARRATOR.md",
        "3p_user": "pov/POV_3P_USER_ANCHOR.md",
        "3p_char": "pov/POV_3P_CHAR_ANCHOR.md",
    }
    return _load_prompt_or_legacy(paths[pov_mode], _legacy_impersonation_section(world_config))


def _select_checklist_template(pov_mode: str) -> str:
    """Select the checklist template for the 4-way POV mode."""
    paths = {
        "1p_user": "checklist/CHECKLIST_1P_USER_NARRATOR.md",
        "1p_char": "checklist/CHECKLIST_1P_CHAR_NARRATOR.md",
        "3p_user": "checklist/CHECKLIST_3P_USER_ANCHOR.md",
        "3p_char": "checklist/CHECKLIST_3P_CHAR_ANCHOR.md",
    }
    legacy = _CHECKLIST_1P if pov_mode.startswith("1p_") else _CHECKLIST_3P
    return _load_prompt_or_legacy(paths[pov_mode], legacy)


# ----------------
# Legacy compatibility
# ----------------

def _legacy_impersonation_section(world_config: dict) -> str:
    """Fallback for old 1P impersonation mode before POV files exist."""
    if world_config.get("impersonation", False):
        return _IMPERSONATION_HEADER
    return ""


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
    """Load one Markdown prompt section from prompt_factory/prompt/."""
    path = PROMPT_DIR / relative_path
    return path.read_text(encoding="utf-8")


def _load_prompt_or_legacy(relative_path: str, legacy: str) -> str:
    """Load a prompt file. If absent, use the legacy prompt_sections.py constant."""
    path = PROMPT_DIR / relative_path
    if path.exists():
        return path.read_text(encoding="utf-8")
    return legacy


# ----------------
# Formatting helpers
# ----------------

def _format_prompt_vars(
    text: str,
    *,
    char_name: str,
    user_name: str,
    world_config: dict,
) -> str:
    """Apply common prompt variables while preserving unresolved checklist placeholders."""
    if not text:
        return ""
    return text.format_map(
        _SafeFormatDict(
            char=char_name,
            user=user_name,
            world_cot_append=world_config.get("world_cot_append", ""),
        )
    )
