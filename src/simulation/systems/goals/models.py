# ================================
# src/simulation/systems/goals/models.py
#
# TypedDict models used by the Goal system.
#
# Classes
#   - GoalRecord : Kuzu Goal row result
#   - GoalUpdate : LLM goal update result
# ================================
from typing import Literal, TypedDict

GoalStatus = Literal["active", "paused", "completed", "failed", "abandoned"]

VALID_STATUSES: set[str] = {"active", "paused", "completed", "failed", "abandoned"}
MAX_HINT_LENGTH = 280


class GoalRecord(TypedDict, total=False):
    """A normalized Goal node row returned from Kuzu."""

    id: str
    owner_id: str
    title: str
    description: str
    status: str
    progress: int
    subtlety: int
    next_hint: str
    trigger_conditions: str
    completion_conditions: str
    last_progressed_at: str


class GoalUpdate(TypedDict, total=False):
    """A normalized goal update extracted from the updater LLM."""

    goal_id: str
    progress_delta: int
    status: GoalStatus
    next_hint: str
    reason: str
