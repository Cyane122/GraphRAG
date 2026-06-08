# ================================
# src/agents/prompt_factory/builder.py
#
# 3-part prompt (Fixed / Genre / Dynamic) assembly module.
# Markdown prompt files are stored tagless; this builder wraps them at assembly time.
# _render_prompt_block, _SafeFormatDict는 renderers.py에서 import.
#
# Classes
#   - PromptBuilder : Fixed / Genre / Dynamic 3-part 프롬프트 조립기
#
# Functions
#   - _read_optional_prompt(relative_path: str) -> str : prompt/ 하위 Markdown 파일 읽기
#   - _render_direction_block(beats: list[dict]) -> str : Director beat 배열을 <direction> 블록으로 렌더링
#   - _format_prompt_vars(text: str, *, char_name: str, user_name: str, for_add: str) -> str : 프롬프트 변수 치환
#   - _label_mixed_input(user_input: str, user_name: str) -> str : *...* 상황설명과 PC 대사를 원문 순서대로 레이블링
# ================================

import re
from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

from src.agents.prompt_factory.checklist import build_turn_checklist
from src.agents.prompt_factory.fixed import (
    build_fixed_section as render_fixed_section,
    build_pre_output_checklist,
)
from src.agents.prompt_factory.renderers import (
    _SafeFormatDict,
    _render_prompt_block,
    build_genre_section,
    join_rendered_context,
    render_active_characters_section,
    render_expressive_characters_section,
    render_character_section,
    render_events_section,
    render_header,
    render_location_context,
    render_npc_section,
    render_recall_events_section,
    render_relationship_section,
    render_world_section,
)

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


# ----------------
# File helpers
# ----------------

def _read_optional_prompt(relative_path: str) -> str:
    """Read a tagless Markdown prompt file from prompt_factory/prompts/."""
    path = PROMPT_DIR / relative_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _render_scene_need_hints(hints: dict[str, str]) -> str:
    """씬 내 캐릭터의 욕구 오버플로우 힌트를 프롬프트 블록으로 렌더링."""
    if not hints:
        return ""
    lines = "\n".join(hints.values())
    return _render_prompt_block("scene_need_hints", lines)


def _read_default_scene_prompt(scene_type: str) -> str:
    """Read default scene prompt. World scene prompt overrides this later."""
    return (
        _read_optional_prompt(f"genre_specific/scenes/{scene_type}.md")
        or _read_optional_prompt(f"genre_specific/{scene_type}.md")
    )


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
    """Build cacheable, genre-specific, and per-turn prompt sections."""

    def __init__(
        self,
        world_config: dict = None,
        char_name: str = None,
        user_name: str = None,
        perspective: int | None = None,
    ):
        """Initialize a prompt builder for one world and character pair."""
        self.world_config = world_config or {}
        self.char_name = char_name
        self.user_name = user_name
        self.perspective = perspective if perspective is not None else self.world_config.get("perspective", 3)

        if not char_name:
            raise ValueError("PromptBuilder: char_name cannot be None or empty")
        if not user_name:
            raise ValueError("PromptBuilder: user_name cannot be None or empty")

        self.pre_output_checklist = build_pre_output_checklist(
            self.world_config,
            self.char_name,
            self.user_name,
            self.perspective,
        )
        self.additional_blacklist = self.world_config.get("additional_blacklist", "")

    def build_fixed_section(self) -> str:
        """Build the cacheable fixed prompt section."""
        return render_fixed_section(
            self.world_config,
            self.char_name,
            self.user_name,
            self.perspective,
            self.additional_blacklist,
        )

    def infer_genres(self, scene_types: list[str]) -> list[str]:
        """Infer additional genre prompt sections from scene types."""
        # genre overlay는 r18 intimate 씬에만 적용; 다른 씬에서 빈 genre 세그먼트는 정상
        if self.world_config.get("rating", "r18") != "r18":
            return []
        genres = []
        if "intimate" in scene_types:
            genres.append("intimate")
        return genres

    def build_dialogue_examples(
        self,
        scene_types: list[str],
        single_type_good: int = 3,
        single_type_bad: int = 2,
        multi_type_good: int = 2,
        multi_type_bad: int = 1,
    ) -> str:
        """Render few-shot dialogue examples for the active scene types."""
        is_multi = len(scene_types) > 1
        good_n = multi_type_good if is_multi else single_type_good
        bad_n = multi_type_bad if is_multi else single_type_bad
        examples_db = {
            **self.world_config.get("few_shot_examples", {}),
            **self.world_config.get("prompt", {}).get("few_shot", {}),
        }

        blocks = []
        for scene_type in scene_types:
            examples = examples_db.get(scene_type)
            if not examples:
                logger.warning("PromptBuilder: no few-shot examples for scene_type '%s'", scene_type)
                continue
            good_lines = "\n".join(f'  - "{line}"' for line in examples["good"][:good_n])
            bad_lines = "\n".join(f'  - "{line}"' for line in examples["bad"][:bad_n])
            structural = f"\n{examples['structural'].strip()}" if examples.get("structural") else ""
            blocks.append(
                f"[{scene_type.upper()}]\nGOOD:\n{good_lines}\nBAD:\n{bad_lines}{structural}"
            )
        return _render_prompt_block("dialogue_examples", "\n\n".join(blocks))

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

    def build_scene_specific_prompt(self, scene_types: list[str]) -> str:
        """Render scene-specific prompt.

        Rule:
        - world scene prompt exists -> use it
        - else default scene prompt exists -> use it
        - else omit

        Prompt md files are tagless. Tags are added here.
        """
        world_prompts = (
            self.world_config.get("scene_specific_prompts", {})
            or self.world_config.get("prompt", {}).get("scenes", {}).get("prompt", {})
        )
        blocks = []

        for scene_type in scene_types:
            prompt = world_prompts.get(scene_type) or _read_default_scene_prompt(scene_type)
            if prompt:
                prompt = _format_prompt_vars(
                    prompt,
                    char_name=self.char_name,
                    user_name=self.user_name,
                )
                blocks.append(_render_prompt_block(f'scene type="{scene_type}"', prompt))

        return _render_prompt_block("scene_specific_prompts", "\n\n".join(blocks))

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
        relationship: dict,
        events: list[dict],
        recent_story: str,
        user_input: str,
        location: str,
        dt: Optional[datetime] = None,
        genres: Optional[list[str]] = None,
        npcs: Optional[list[dict]] = None,
        user_data: dict | None = None,
        recall_events: Optional[list[dict]] = None,
        memory_conflicts: Optional[list[str]] = None,
        world_context: Optional[dict] = None,
        rendered_context: Optional[dict[str, str]] = None,
        current_pov: Optional[dict] = None,
        location_nodes: Optional[list[dict]] = None,
        scene_need_hints: Optional[dict[str, str]] = None,
        direction: Optional[list[dict]] = None,
    ) -> tuple[str, str, str]:
        """Return fixed, genre, and dynamic prompt sections for the current turn."""
        fixed_prompt = self.build_fixed_section()
        genre_prompt = build_genre_section(
            self.infer_genres(scene_types) if genres is None else genres,
            self.world_config,
        )
        checklist = build_turn_checklist(
            self.pre_output_checklist,
            scene_types,
            self.world_config,
            char_data,
            current_pov,
            npcs or [],
            self.char_name,
            self.user_name,
        )
        context_block = self._build_context_block(
            relationship,
            events,
            npcs or [],
            recall_events or [],
            memory_conflicts or [],
            world_context or {},
            rendered_context,
        )
        need_hints_block = _render_scene_need_hints(scene_need_hints or {})
        # direction이 있으면 Actor 캐릭터 블록을 외형/표현 필드만으로 축소
        field_types = self.world_config.get("field_types") or None
        characters_block = (
            render_expressive_characters_section(char_data, user_data or {}, npcs or [], scene_types, field_types)
            if direction is not None
            else render_active_characters_section(char_data, user_data or {}, npcs or [], scene_types)
        )
        dynamic_prompt = "\n\n".join(
            part
            for part in [
                self.build_header(location, dt),
                render_location_context(location_nodes or []),
                self.build_unified_blacklist(char_data, scene_types),
                characters_block,
                _render_direction_block(direction or []),
                need_hints_block,
                self.build_character_focus_prompt(char_data),
                self.build_scene_specific_prompt(scene_types),
                context_block,
                self.build_dialogue_examples(scene_types),
                _render_prompt_block("user_input", _label_mixed_input(user_input, self.user_name)),
                checklist,
                (
                    "Fill out the <analyze> template from the checklist above. "
                    "Close </analyze>, then IMMEDIATELY write the Korean prose scene. "
                    "Begin the final prose with the exact date/time/location header shown above. "
                    "The scene is mandatory; do not stop after </analyze>."
                ),
            ]
            if part
        )
        return fixed_prompt, genre_prompt, dynamic_prompt

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

    def _build_context_block(
        self,
        relationship: dict,
        events: list[dict],
        npcs: list[dict],
        recall_events: list[dict],
        memory_conflicts: list[str],
        world_context: dict,
        rendered_context: dict[str, str] | None,
    ) -> str:
        """Build dynamic context either from the budget renderer or legacy renderers."""
        if rendered_context:
            return join_rendered_context(rendered_context)
        return "\n\n".join(
            part
            for part in (
                render_relationship_section(relationship),
                render_npc_section(npcs, self.char_name),
                render_events_section(events, self.char_name, self.user_name),
                render_recall_events_section(recall_events, memory_conflicts),
                render_world_section(world_context),
            )
            if part
        )


def _render_direction_block(beats: list[dict]) -> str:
    """Director beat 배열을 Actor 입력용 <direction> 블록으로 렌더링한다.

    rationale은 show-then-tell 위반 방지를 위해 제외한다.
    """
    if not beats:
        return ""
    lines = [
        "Director beat plan for this turn.",
        "Use it as scene-flow guidance: preserve sequence, active characters, physical actions, emotional trajectory, and focus details.",
        "Fixed/world/scenario rules, current user input, and current scene context override this plan if they conflict.",
        "Do not explain or mention this plan in prose.",
        "",
        "[beats]",
    ]
    for beat in beats:
        char = beat.get("char", "?")
        action = beat.get("action", "")
        emotion = beat.get("emotion", "")
        expression = beat.get("expression", "")
        focus = beat.get("focus", "")
        lines.append(f"- {char}: action={action} | emotion={emotion} | expression={expression} | focus={focus}")
    return _render_prompt_block('direction type="director_beat_plan"', "\n".join(lines))


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
