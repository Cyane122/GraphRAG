# ================================
# src/agents/prompt_factory/builder.py
#
# 2-part prompt (Fixed / Dynamic) assembly module.
# Markdown prompt files are stored tagless; this builder wraps them at assembly time.
# _render_prompt_block, _SafeFormatDict는 renderers.py에서 import.
#
# Classes
#   - PromptBuilder : Fixed / Dynamic 2-part 프롬프트 조립기
#
# Functions
#   - _render_scene_need_hints(hints: dict[str, str]) -> str : 욕구 오버플로우 힌트 블록 렌더링
#   - _read_default_scene_blacklist(scene_type: str) -> str : 기본 씬 블랙리스트 로드
#   - _read_global_blacklist_template() -> str : 전역 블랙리스트 템플릿 로드
#   - _format_prompt_vars(text: str, *, char_name: str, user_name: str, for_add: str) -> str : 프롬프트 변수 치환
#   - _label_mixed_input(user_input: str, user_name: str) -> str : *...* 상황설명과 PC 대사를 원문 순서대로 레이블링
# ================================

import re
from datetime import datetime
from typing import Optional

from src.agents.prompt_factory.engines import normalize_engine_modules
from src.agents.prompt_factory.fixed import (
    build_fixed_section as render_fixed_section,
    resolve_pov_mode,
    user_impersonation_allowed,
)
from src.agents.prompt_factory.profiles import ProseProfile, normalize_prose_profile
from src.agents.prompt_factory.renderers import (
    _SafeFormatDict,
    _read_optional_prompt,
    _render_prompt_block,
    join_rendered_context,
    render_active_characters_section,
    render_current_pov,
    render_header,
    render_location_context,
    render_reproductive_state,
)
from src.agents.context.scene_keys import normalize_prompt_scene_types

_SYSTEM_LOG_CONTRACT = """<output_contract>
Open <analyze> and write only this System_Log, one field per line:

System_Log
Mode: current scene mode and whether the turn stays in it
Input: what the player actually attempted
Cause: what that attempt causes in the world right now
Cast: who is present and active this turn
Talk: who speaks and who holds initiative
Rel: only when a relationship actually changes
Delta: what is different at the end of the turn
Guard: the single constraint most at risk this turn

Close </analyze>, then IMMEDIATELY write the Korean prose scene.
Begin the prose with a Korean date/time/location header in bold.
KEEP the date and location IDENTICAL to the current header above unless the Player Input
(or an OOC directive) explicitly moves time or place; do not invent date jumps or location
changes on your own. Within the same scene you may advance only minutes/hours on the SAME
calendar day, and never move time backward.
The scene is mandatory; do not stop after </analyze>.

Before writing, silently verify player ownership, point of view, established facts,
engine constraints, and the blacklist. Do not print that verification anywhere.
</output_contract>"""


def _render_scene_need_hints(hints: dict[str, str]) -> str:
    """씬 내 캐릭터의 욕구 오버플로우 힌트를 프롬프트 블록으로 렌더링."""
    if not hints:
        return ""
    lines = "\n".join(hints.values())
    return _render_prompt_block("scene_need_hints", lines)


def _read_default_scene_blacklist(scene_type: str) -> str:
    """Read default scene blacklist. World scene blacklist overrides this later."""
    return (
        _read_optional_prompt(f"genre_specific/scenes/{scene_type}.cot_append.md")
        or _read_optional_prompt(f"genre_specific/{scene_type}.cot_append.md")
    )


def _read_global_blacklist_template() -> str:
    """Read tagless global blacklist template from prompt/."""
    return _read_optional_prompt("blacklist/BLACKLIST.md")


class PromptBuilder:
    """Build the cacheable fixed section and the per-turn dynamic section."""

    def __init__(
        self,
        world_config: dict = None,
        char_name: str = None,
        user_name: str = None,
        perspective: int | None = None,
        prose_profile: ProseProfile | None = None,
        engine_modules: dict[str, str] | None = None,
    ):
        """Initialize a prompt builder for one world and character pair."""
        self.world_config = world_config or {}
        self.char_name = char_name
        self.user_name = user_name
        self.perspective = perspective if perspective is not None else self.world_config.get("perspective", 3)
        self.prose_profile = normalize_prose_profile(prose_profile)
        self.engine_modules = normalize_engine_modules(engine_modules)

        if not char_name:
            raise ValueError("PromptBuilder: char_name cannot be None or empty")
        if not user_name:
            raise ValueError("PromptBuilder: user_name cannot be None or empty")

        self.pov_mode = resolve_pov_mode(self.world_config, self.perspective)
        self.additional_blacklist = self.world_config.get("additional_blacklist", "")

    def build_fixed_section(self) -> str:
        """Build the cacheable fixed prompt section."""
        return render_fixed_section(
            self.world_config,
            self.char_name,
            self.user_name,
            self.perspective,
            self.additional_blacklist,
            self.prose_profile,
            self.engine_modules,
        )

    def build_current_pov(self, current_pov: Optional[dict] = None) -> str:
        """Render the turn's narrator anchor and player-ownership scope."""
        return _format_prompt_vars(
            render_current_pov(
                self.pov_mode,
                user_impersonation_allowed(self.pov_mode, self.world_config),
                current_pov,
            ),
            char_name=self.char_name,
            user_name=self.user_name,
        )

    def build_reproductive_state(self, char_data: dict, npcs: list[dict]) -> str:
        """Render reproductive state, but only while the adult engine is enabled."""
        if self.engine_modules.get("adult", "off") == "off":
            return ""
        return render_reproductive_state(char_data, npcs)

    def build_world_turn_constraints(self) -> str:
        """Render world-authored per-turn constraints previously carried by the checklist."""
        body = _format_prompt_vars(
            str(self.world_config.get("world_cot_append", "")).strip(),
            char_name=self.char_name,
            user_name=self.user_name,
        )
        return _render_prompt_block("world_turn_constraints", body)

    def build_character_focus_prompt(self, char_data: dict) -> str:
        """Render narrator-specific prose focus prompt, if configured.

        Character focus prompt are rendered in the fixed (cached) section by fixed.py.
        This method returns empty to avoid duplicating in the dynamic section.
        """
        if self.world_config.get("character_focus_prompt"):
            return ""
        if self.world_config.get("prompt", {}).get("characters", {}).get("focus"):
            return ""
        prompts = self.world_config.get("character_focus_prompts", {})
        char_id = str(char_data.get("id") or "").strip()
        prompt = prompts.get(char_id) or prompts.get(str(self.char_name or "").strip())
        return _render_prompt_block("character_focus_prompt", prompt)

    def build_unified_blacklist(self, char_data: dict, scene_types: list[str]) -> str:
        """Render one blacklist block containing global, world, character, and scene bans.

        All blacklist md files are tagless. The final <blacklist> tag is added here.
        """
        is_unified = (
            self.world_config.get("unified_blacklist")
            or self.world_config.get("prompt", {}).get("blacklist", {}).get("unified", False)
        )
        if not is_unified:
            return ""

        additions = []
        world_blacklist = self.world_config.get("additional_blacklist", "")
        if world_blacklist:
            additions.append(f"## World-Specific Ban\n{world_blacklist.strip()}")

        character_blacklist = self._lookup_character_blacklist(char_data)
        if character_blacklist:
            additions.append(f"## Character-Specific Ban\n{character_blacklist.strip()}")

        scene_parts = []
        world_scene_blacklists = (
            self.world_config.get("scene_blacklists", {})
            or self.world_config.get("prompt", {}).get("scenes", {}).get("blacklist", {})
        )
        for scene_type in scene_types:
            # World scene blacklist overrides default scene blacklist.
            ban = world_scene_blacklists.get(scene_type) or _read_default_scene_blacklist(scene_type)
            if ban:
                scene_parts.append(f"### {scene_type}\n{ban.strip()}")
        if scene_parts:
            additions.append("## Scene-Specific Ban\n" + "\n\n".join(scene_parts))

        blacklist_tpl = _read_global_blacklist_template()
        if blacklist_tpl:
            body = _format_prompt_vars(
                blacklist_tpl,
                char_name=self.char_name,
                user_name=self.user_name,
                for_add="\n\n".join(additions),
            )
        else:
            body = "\n\n".join(additions)

        return _render_prompt_block("blacklist", body)

    def build_header(self, location: str, dt: Optional[datetime] = None) -> str:
        """Render the current turn's date/location header."""
        if dt is None:
            dt = self.world_config.get("start_time")
        return render_header(location, dt)

    def build(
        self,
        scene_types: list[str],
        char_data: dict,
        recent_story: str,
        user_input: str,
        location: str,
        dt: Optional[datetime] = None,
        npcs: Optional[list[dict]] = None,
        user_data: dict | None = None,
        rendered_context: Optional[dict[str, str]] = None,
        current_pov: Optional[dict] = None,
        location_nodes: Optional[list[dict]] = None,
        scene_need_hints: Optional[dict[str, str]] = None,
        turn_ooc_directives: str = "",
    ) -> tuple[str, str]:
        """Return the fixed and dynamic prompt sections for the current turn."""
        scene_types = normalize_prompt_scene_types(scene_types)
        fixed_prompt = self.build_fixed_section()
        context_block = join_rendered_context(rendered_context or {})
        need_hints_block = _render_scene_need_hints(scene_need_hints or {})
        characters_block = render_active_characters_section(char_data, user_data or {}, npcs or [], scene_types)
        dynamic_prompt = "\n\n".join(
            part
            for part in [
                self.build_header(location, dt),
                render_location_context(location_nodes or []),
                self.build_unified_blacklist(char_data, scene_types),
                characters_block,
                need_hints_block,
                self.build_character_focus_prompt(char_data),
                context_block,
                self.build_current_pov(current_pov),
                self.build_reproductive_state(char_data, npcs or []),
                self.build_world_turn_constraints(),
                self.build_turn_ooc_directives(turn_ooc_directives),
                _render_prompt_block("user_input", _label_mixed_input(user_input, self.user_name)),
                _SYSTEM_LOG_CONTRACT,
            ]
            if part
        )
        return fixed_prompt, dynamic_prompt

    def build_turn_ooc_directives(self, directives: str) -> str:
        """Render per-thread OOC directives as instructions outside Player Input."""
        body = _format_prompt_vars(
            str(directives or "").strip(),
            char_name=self.char_name,
            user_name=self.user_name,
        )
        if not body:
            return ""
        return _render_prompt_block("turn_ooc_directives", body)

    def _lookup_character_blacklist(self, char_data: dict) -> str:
        """Return fixed or keyed character-specific blacklist text."""
        fixed = self.world_config.get("character_blacklist", "")
        if fixed:
            return fixed
        blacklists = (
            self.world_config.get("character_blacklists", {})
            or self.world_config.get("prompt", {}).get("characters", {}).get("blacklist", {})
        )
        char_id = str(char_data.get("id") or "").strip()
        return blacklists.get(char_id) or blacklists.get(str(self.char_name or "").strip()) or ""

def _format_prompt_vars(text: str, *, char_name: str, user_name: str, for_add: str = "") -> str:
    """Apply common prompt variables while preserving unknown placeholders."""
    if not text:
        return ""
    return text.format_map(
        _SafeFormatDict(
            char=char_name,
            user=user_name,
            for_add=for_add,
        )
    )


_OOC_SPAN_RE = re.compile(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", re.DOTALL)


def _label_mixed_input(user_input: str, user_name: str) -> str:
    """*...* 상황설명과 PC 대사가 섞인 입력을 원문 순서대로 레이블링합니다.

    *PC가 손을 잡는다* 왜 그래요?
    →
    [상황] PC가 손을 잡는다
    [PC] 왜 그래요?

    OOC 없이 순수 대사만 있으면 원본 반환.
    """
    if not _OOC_SPAN_RE.search(user_input):
        return user_input

    parts: list[str] = []
    cursor = 0
    has_rp_text = False

    for match in _OOC_SPAN_RE.finditer(user_input):
        rp_text = user_input[cursor:match.start()].strip()
        if rp_text:
            parts.append(f"[{user_name}] {rp_text}")
            has_rp_text = True

        situation = match.group(1).strip()
        if situation:
            parts.append(f"[상황] {situation}")

        cursor = match.end()

    rp_text = user_input[cursor:].strip()
    if rp_text:
        parts.append(f"[{user_name}] {rp_text}")
        has_rp_text = True

    if not has_rp_text:
        return user_input
    return "\n".join(parts)
