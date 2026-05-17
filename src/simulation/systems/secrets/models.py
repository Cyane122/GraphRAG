# ================================
# src/simulation/systems/secrets/models.py
#
# TypedDict models used by the Secret system.
#
# Classes
#   - SecretHint : Prompt-safe secret hint
#   - SecretRevealUpdate : Secret reveal update record
# ================================
from typing import NotRequired, TypedDict

class SecretHint(TypedDict):
    """Prompt-safe secret hint shape returned by fetch_secret_hints."""

    id: str
    owner_id: str
    title: str
    hint: str
    status: str
    sensitivity: int
    reveal_level: int


class SecretRevealUpdate(TypedDict):
    """Secret reveal update shape used internally by apply_secret_updates."""

    id: str
    title: str
    owner_id: str
    previous_status: str
    new_status: str
    previous_reveal_level: int
    new_reveal_level: int
    matched_response: NotRequired[bool]
