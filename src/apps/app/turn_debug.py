# ================================
# src/apps/app/turn_debug.py
#
# Write prompt and turn-debug snapshots during Actor execution.
#
# Functions
#   - write_actor_raw_snapshot(full_response: str, raw_thinking: str, visible_text: str, logs_dir: Path, debug_dir: str | None = None) -> None : Save latest Actor raw outputs
#   - write_turn_debug_snapshot(user_input: str, fixed_prompt: str, dynamic_prompt: str, scene_types: list[str], manager_effects: dict, history: list[dict], world_id: str, pc_id: str, npc_id: str, npc_name: str, logs_dir: Path, turn_debug_dir: Path, actor_model: str | None = None, world_mode: str | None = None, thread_id: str | None = None, scenario_id: str | None = None, user_message_id: str | None = None, commit_id: str | None = None) -> str | None : Save a pre-accept turn debug snapshot with conversation identity
# ================================
import json
from datetime import datetime
from pathlib import Path

from src.core.logging.prompt_debug import (
    append_prompt_fingerprint_log,
    build_prompt_fingerprint,
    format_prompt_fingerprint,
)


def write_actor_raw_snapshot(
    full_response: str,
    raw_thinking: str,
    visible_text: str,
    logs_dir: Path,
    debug_dir: str | None = None,
) -> None:
    """Save the latest raw Actor full/thinking/visible outputs."""
    files = {
        "raw_full.txt": full_response or "",
        "raw_thinking.txt": raw_thinking or "",
        "raw_output.txt": visible_text or "",
    }
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (logs_dir / name).write_text(content, encoding="utf-8")

        if debug_dir:
            turn_dir = Path(debug_dir)
            turn_dir.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                (turn_dir / name).write_text(content, encoding="utf-8")
    except OSError as e:
        print(f"[TurnDebug] raw output save failed: {e}")


def write_turn_debug_snapshot(
    user_input: str,
    fixed_prompt: str,
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
    actor_model: str | None = None,
    world_mode: str | None = None,
    thread_id: str | None = None,
    scenario_id: str | None = None,
    user_message_id: str | None = None,
    commit_id: str | None = None,
) -> str | None:
    """Actor 호출 직전의 프롬프트와 manager 산출물을 디버그 파일로 저장합니다.

    metadata와 fingerprint 레코드에는 대화 식별자(mode·thread·scenario·사용자 메시지·commit)를
    최상위로 남긴다. 이 스냅샷은 출력 repair·확정 이전의 진단 기록(record_kind)이며 확정 턴
    로그가 아니다. 식별자는 Actor 프롬프트나 fingerprint 해시에 섞이지 않는다.
    """
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        turn_dir = turn_debug_dir / stamp
        turn_dir.mkdir(parents=True, exist_ok=True)

        identity = {
            "record_kind": "pre_accept_turn_debug",
            "world_mode": world_mode,
            "thread_id": thread_id,
            "scenario_id": scenario_id,
            "user_message_id": user_message_id,
            "commit_id": commit_id,
        }
        prompt_fingerprint = build_prompt_fingerprint(
            fixed_prompt=fixed_prompt,
            dynamic_prompt=dynamic_prompt,
            history=history,
        )
        prompt_fingerprint.update({
            **identity,
            "world_id": world_id,
            "pc_id": pc_id,
            "npc_id": npc_id,
            "scene_types": scene_types,
        })
        append_prompt_fingerprint_log(prompt_fingerprint, logs_dir)
        print(format_prompt_fingerprint(prompt_fingerprint))

        final_prompt = (
            "[SYSTEM]\n"
            f"{fixed_prompt}\n\n"
            "[HISTORY]\n"
            f"{json.dumps(history, ensure_ascii=False, indent=2)}\n\n"
            "[USER_DYNAMIC_PROMPT]\n"
            f"{dynamic_prompt}\n"
        )

        files = {
            "fixed_prompt.txt": fixed_prompt,
            "dynamic_prompt.txt": dynamic_prompt,
            "final_prompt-2.txt": final_prompt,
            "history.json": json.dumps(history, ensure_ascii=False, indent=2),
            "metadata.json": json.dumps({
                "timestamp": stamp,
                **identity,
                "world_id": world_id,
                "pc_id": pc_id,
                "npc_id": npc_id,
                "npc_name": npc_name,
                "actor_model": actor_model,
                "scene_types": scene_types,
                "user_input": user_input,
                "manager_effects": manager_effects,
                "prompt_fingerprint": prompt_fingerprint,
                "prompt_lengths": {
                    "fixed": len(fixed_prompt),
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
            f"- mode: `{world_mode or ''}`",
            f"- thread: `{thread_id or ''}`",
            f"- world: `{world_id}`",
            f"- pc: `{pc_id}`",
            f"- npc: `{npc_name}` (`{npc_id}`)",
            f"- actor_model: `{actor_model or ''}`",
            f"- scene_types: `{scene_types}`",
            f"- fixed chars: `{len(fixed_prompt)}`",
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
        if manager_effects.get("engine") == "wiki":
            summary.extend([
                "",
                "## Wiki Context",
                "",
                "```json",
                json.dumps(
                    manager_effects.get("wiki_context", {}),
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "",
                "## Wiki Updater Documents",
                "",
                "```json",
                json.dumps(
                    manager_effects.get("updater_documents", []),
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
            ])
        (turn_dir / "summary.md").write_text("\n".join(summary), encoding="utf-8")
        return str(turn_dir)
    except OSError as e:
        print(f"[TurnDebug] 저장 실패: {e}")
        return None
