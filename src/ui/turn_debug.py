# ================================
# src/ui/turn_debug.py
#
# Write prompt and turn-debug snapshots during Actor execution.
#
# Functions
#   - write_turn_debug_snapshot(user_input: str, fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, scene_types: list[str], manager_effects: dict, history: list[dict], world_id: str, pc_id: str, npc_id: str, npc_name: str, logs_dir: Path, turn_debug_dir: Path) -> str | None : Save a turn debug snapshot
# ================================
import json
from datetime import datetime
from pathlib import Path

from src.core.logging.prompt_debug import (
    append_prompt_fingerprint_log,
    build_prompt_fingerprint,
    format_prompt_fingerprint,
)


def write_turn_debug_snapshot(
    user_input: str,
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    scene_types: list[str],
    manager_effects: dict,
    history: list[dict],
    world_id: str,
    pc_id: str,
    npc_id: str,
    npc_name: str,
    logs_dir: Path,
    turn_debug_dir: Path,
) -> str | None:
    """Actor 호출 직전의 프롬프트와 manager 산출물을 디버그 파일로 저장합니다."""
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        turn_dir = turn_debug_dir / stamp
        turn_dir.mkdir(parents=True, exist_ok=True)

        system_text = f"{fixed_prompt}\n\n{genre_prompt}" if genre_prompt else fixed_prompt
        prompt_fingerprint = build_prompt_fingerprint(
            fixed_prompt=fixed_prompt,
            genre_prompt=genre_prompt or "",
            dynamic_prompt=dynamic_prompt,
            history=history,
        )
        prompt_fingerprint.update({
            "world_id": world_id,
            "pc_id": pc_id,
            "npc_id": npc_id,
            "scene_types": scene_types,
        })
        append_prompt_fingerprint_log(prompt_fingerprint, logs_dir)
        print(format_prompt_fingerprint(prompt_fingerprint))

        final_prompt = (
            "[SYSTEM]\n"
            f"{system_text}\n\n"
            "[HISTORY]\n"
            f"{json.dumps(history, ensure_ascii=False, indent=2)}\n\n"
            "[USER_DYNAMIC_PROMPT]\n"
            f"{dynamic_prompt}\n"
        )

        files = {
            "fixed_prompt.txt": fixed_prompt,
            "genre_prompt.txt": genre_prompt or "",
            "dynamic_prompt.txt": dynamic_prompt,
            "final_prompt-2.txt": final_prompt,
            "history.json": json.dumps(history, ensure_ascii=False, indent=2),
            "metadata.json": json.dumps({
                "timestamp": stamp,
                "world_id": world_id,
                "pc_id": pc_id,
                "npc_id": npc_id,
                "npc_name": npc_name,
                "scene_types": scene_types,
                "user_input": user_input,
                "manager_effects": manager_effects,
                "prompt_fingerprint": prompt_fingerprint,
                "prompt_lengths": {
                    "fixed": len(fixed_prompt),
                    "genre": len(genre_prompt or ""),
                    "dynamic": len(dynamic_prompt),
                    "final": len(final_prompt),
                },
            }, ensure_ascii=False, indent=2),
        }
        for name, content in files.items():
            (turn_dir / name).write_text(content, encoding="utf-8")

        summary = [
            f"# Turn Debug {stamp}",
            "",
            f"- world: `{world_id}`",
            f"- pc: `{pc_id}`",
            f"- npc: `{npc_name}` (`{npc_id}`)",
            f"- scene_types: `{scene_types}`",
            f"- fixed chars: `{len(fixed_prompt)}`",
            f"- genre chars: `{len(genre_prompt or '')}`",
            f"- dynamic chars: `{len(dynamic_prompt)}`",
            f"- final chars: `{len(final_prompt)}`",
            "",
            "## User Input",
            "",
            user_input,
            "",
            "## Time Plan",
            "",
            "```json",
            json.dumps(manager_effects.get("time_plan"), ensure_ascii=False, indent=2),
            "```",
            "",
            "## Pending Effects",
            "",
            "```json",
            json.dumps(manager_effects.get("pending_effects", []), ensure_ascii=False, indent=2),
            "```",
        ]
        (turn_dir / "summary.md").write_text("\n".join(summary), encoding="utf-8")
        return str(turn_dir)
    except OSError as e:
        print(f"[TurnDebug] 저장 실패: {e}")
        return None
