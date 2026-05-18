# ================================
# src/ui/input_routing.py
#
# Define Chainlit user-input routing types and decisions.
#
# Classes
#   - TurnInputType : User input handling type enum
#
# Functions
#   - route_user_input(user_input: str, message: object) -> TurnInputType : Decide how to handle an input
# ================================
import re
from enum import Enum

from src.agents.prompt_factory.ooc_handler import is_ooc

_OOC_SPAN_RE = re.compile(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", re.DOTALL)
_SYSTEM_COMMAND_PREFIXES = ("/", "!")


class TurnInputType(str, Enum):
    """사용자 입력 처리 방식을 구분합니다."""

    ROLEPLAY = "roleplay"
    OOC_PATCH = "ooc_patch"
    REROLL = "reroll"
    EDIT = "edit"
    SYSTEM_COMMAND = "system_command"
    EMPTY = "empty"


def _strip_ooc_spans(text: str) -> str:
    """단일 OOC 구간을 제거한 뒤 남은 RP 텍스트를 반환합니다."""
    without_bold = re.sub(r"\*\*.*?\*\*", "", text, flags=re.DOTALL)
    return _OOC_SPAN_RE.sub("", without_bold).strip()


def route_user_input(user_input: str, message: object) -> TurnInputType:
    """사용자 입력의 처리 경로를 결정합니다."""
    text = user_input.strip()
    elements = getattr(message, "elements", None)
    if not text and not elements:
        return TurnInputType.EMPTY
    if text.startswith("__EDIT__:") or text == "__EDIT_CANCEL__":
        return TurnInputType.EDIT
    if text in {"/reroll", "!reroll", "/retry", "!retry"}:
        return TurnInputType.REROLL
    if text.startswith(_SYSTEM_COMMAND_PREFIXES):
        return TurnInputType.SYSTEM_COMMAND
    if is_ooc(text):
        return TurnInputType.OOC_PATCH if not _strip_ooc_spans(text) else TurnInputType.ROLEPLAY
    return TurnInputType.ROLEPLAY
