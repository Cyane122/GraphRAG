# ================================
# src/agents/prompt_factory/fixed.py
#
# Cacheable fixed Actor prompt section builder.
# Loads modular Markdown prompt files from prompt_factory/prompts/.
# Keeps the shared operator and token-budget policy fragments beside their sole consumer.
# Style now comes from one axis only: the conversation's ProseProfile (profiles.py).
# _render_prompt_block, _SafeFormatDict, format_prompt_vars_twice, is_unified_blacklist는
# renderers.py에서 import.
#
# Functions
#   - build_fixed_section(world_config: dict, char_name: str, user_name: str, pov_mode: str, prose_profile: ProseProfile, engine_modules: dict[str, str]) -> str : 고정 프롬프트 세그먼트 조립 (prose_profile/engine_modules/pov_mode는 이미 정규화·해석된 값을 받는다)
#   - resolve_pov_mode(world_config: dict, perspective: int) -> str : 4-way POV 모드 판정
#   - user_impersonation_allowed(pov_mode: str, world_config: dict | None = None) -> bool : 사칭 허용 여부 판단
#   - char_focus_configured(world_config: dict) -> bool : 캐릭터 초점 프롬프트가 설정돼 fixed 세그먼트가 이를 소유하는지 판정
#   - _resolve_char_focus(world_config: dict, char_name: str) -> str : 인물 초점 프롬프트 선택
#   - _select_blacklist_section(world_config: dict, char_name: str, user_name: str, additional_blacklist: str) -> str : 블랙리스트 본문 선택
#   - _select_user_impersonation_section(pov_mode: str, world_config: dict | None = None) -> str : 유저 대필 허용/금지 본문 선택
#   - _select_operator(rating: str) -> str : 등급별 operator 정책 선택
#   - _load_prompt(relative_path: str) -> str : prompts/ 하위 Markdown 로드
# ================================

from src.agents.prompt_factory.engines import adult_engine_enabled, build_engine_modules_section
from src.agents.prompt_factory.profiles import ProseProfile, effective_prose_profile, render_prose_profile
from src.agents.prompt_factory.renderers import (
    PROMPT_DIR,
    _render_prompt_block,
    format_prompt_vars_twice,
    is_unified_blacklist,
)
from src.config import MAX_TOKEN

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


# ----------------
# Public builders
# ----------------

def build_fixed_section(
    world_config: dict,
    char_name: str,
    user_name: str,
    pov_mode: str,
    prose_profile: ProseProfile,
    engine_modules: dict[str, str],
) -> str:
    """Render the cacheable fixed prompt section.

    Order: operator policy, user ownership, the always-applied simulation core, the
    conversation's prose profile (base + primary + modifiers), the selected engine
    modules, then world-authored lore, prose, focus, and blacklist material.

    pov_mode, prose_profile, and engine_modules are expected already normalized/
    resolved by the caller (PromptBuilder.__init__ owns that); this function does
    not re-normalize them. Both prose_profile and engine_modules live in this
    FIXED, CACHEABLE segment only - never in effective_input. The rendered prose
    profile is coupled to the adult engine at render time only (via
    effective_prose_profile): the stored profile selection itself is never mutated.
    """
    operator = _select_operator(world_config.get("rating", "r18"))
    prompt_sections = world_config.get("prompt", {}).get("sections", {})
    additional_blacklist = world_config.get("additional_blacklist", "")
    adult_on = adult_engine_enabled(engine_modules)

    def fmt(text: str) -> str:
        return format_prompt_vars_twice(text, char_name=char_name, user_name=user_name)

    engine_modules_section = build_engine_modules_section(
        engine_modules,
        char_name=char_name,
        user_name=user_name,
    )

    parts = [
        fmt(operator),
        fmt(_render_prompt_block("user_impersonation", _select_user_impersonation_section(pov_mode, world_config))),
        fmt(_render_prompt_block("simulation_core", _load_prompt("simulation_core.md"))),
        fmt(
            _render_prompt_block(
                "prose_profile",
                render_prose_profile(effective_prose_profile(prose_profile, adult_engine_enabled=adult_on)),
            )
        ),
        # Raw and unformatted: engine-module bodies contain brace sequences that
        # raise under str.format/format_map (see engines.py), so this section
        # must never pass through format_prompt_vars_twice.
        engine_modules_section,
        fmt(_render_prompt_block("world_lore", prompt_sections.get("world"))),
        fmt(_render_prompt_block("scenario_lore", prompt_sections.get("scenario"))),
        fmt(_render_prompt_block("alteration_lore", world_config.get("alteration_section", ""))),
        fmt(_render_prompt_block("world_specific_prose_prompt", prompt_sections.get("prose") or world_config.get("prose_rules", ""))),
        fmt(_render_prompt_block("character_focus_prompt", _resolve_char_focus(world_config, char_name))),
        fmt(_render_prompt_block("blacklist", _select_blacklist_section(world_config, char_name, user_name, additional_blacklist))),
        fmt(TOKEN_LIMIT_WARNING),
    ]

    return "\n\n".join(part for part in parts if part)


# ----------------
# POV mode
# ----------------

def char_focus_configured(world_config: dict) -> bool:
    """Return whether the world configures a character-focus prompt (the fixed section owns it).

    Shared by _resolve_char_focus (fixed.py, which renders it) and
    builder.build_character_focus_prompt (which must skip it to avoid duplication).
    """
    return bool(
        world_config.get("character_focus_prompt")
        or world_config.get("prompt", {}).get("characters", {}).get("focus")
    )


def _resolve_char_focus(world_config: dict, char_name: str) -> str:
    """Return character focus prompt text.

    Priority: legacy flat key -> prompt.characters.focus (joined if multiple).
    Placed in the fixed (cached) section; builder skips it for the dynamic section.
    """
    if not char_focus_configured(world_config):
        return ""
    flat = world_config.get("character_focus_prompt", "")
    if flat:
        return flat
    focus_map = world_config.get("prompt", {}).get("characters", {}).get("focus", {})
    # If char_name matches a key exactly, prefer it; otherwise join all entries.
    return focus_map.get(char_name, "") or "\n\n".join(focus_map.values())


def resolve_pov_mode(world_config: dict, perspective: int) -> str:
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


def user_impersonation_allowed(pov_mode: str, world_config: dict | None = None) -> bool:
    """Return whether the Actor may narrate the player character this turn."""
    if pov_mode.endswith("_user"):
        return True
    # world_config에 impersonation=True가 명시된 경우 pov anchor와 무관하게 허용
    return bool(world_config and world_config.get("impersonation", False))


# ----------------
# Section selectors
# ----------------

def _select_blacklist_section(
    world_config: dict,
    char_name: str,
    user_name: str,
    additional_blacklist: str,
) -> str:
    """Return the global blacklist body, or empty when the world uses a unified blacklist."""
    if is_unified_blacklist(world_config):
        return ""

    blacklist_tpl = _load_prompt("blacklist/BLACKLIST.md")
    return blacklist_tpl.format(
        for_add=additional_blacklist,
        char=char_name,
        user=user_name,
    )


def _select_user_impersonation_section(pov_mode: str, world_config: dict | None = None) -> str:
    """Select whether the model may narrate {user}, derived from pov_mode and world_config."""
    if user_impersonation_allowed(pov_mode, world_config):
        return _load_prompt("core/USER_IMPERSONATION_ALLOWED.md")
    return _load_prompt("core/USER_IMPERSONATION_FORBIDDEN.md")


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
