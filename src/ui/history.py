# ================================
# src/ui/history.py
#
# Conversation history reconstruction helpers for Chainlit thread resumes.
#
# Functions
#   - build_history_from_steps(steps: list[dict], max_history_turns: int, recent_story_turns: int) -> tuple[list[dict], list[str]] : Restore prompt history and recent assistant responses from persisted steps.
# ================================


def build_history_from_steps(
    steps: list[dict],
    max_history_turns: int,
    recent_story_turns: int,
) -> tuple[list[dict], list[str]]:
    """Restore conversation history and recent assistant responses from Chainlit steps."""
    history: list[dict] = []
    recents: list[str] = []
    for step in steps:
        stype = step.get("type", "")
        output = step.get("output") or ""
        sid = step.get("id", "")
        if stype == "user_message":
            history.append({"role": "user", "content": output, "msg_id": sid})
        elif stype == "assistant_message":
            history.append({"role": "assistant", "content": output, "msg_id": sid})
            recents.append(output[:1500])
    return history[-max_history_turns * 2 :], recents[-recent_story_turns:]
