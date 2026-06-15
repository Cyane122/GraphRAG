# ================================
# src/ui/output_repair.py
#
# Guard에 걸린 Actor prose를 Pro 모델로 최소 수정합니다.
#
# Functions
#   - repair_actor_output(actor_output: str, blocked_terms: list[str], model_name: str) -> str : Guard 위반 Actor 응답을 최소 수정
# ================================

from pathlib import Path

from src.core.llm.client import get_model
from src.ui.output_guard import load_forbidden_terms


_REPAIR_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "agents"
    / "prompt_factory"
    / "prompt"
    / "blacklist"
    / "OUTPUT_REPAIR.md"
)


def _load_repair_system_prompt() -> str:
    """Pro repair에 사용할 시스템 프롬프트 자산을 로드합니다."""
    if _REPAIR_PROMPT_PATH.exists():
        return _REPAIR_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return (
        "You repair Korean roleplay prose after a local output guard rejection. "
        "Return only the repaired prose. Preserve events, speakers, ordering, and intensity. "
        "Do not add new facts, actions, dialogue, or explanations."
    )


def _repair_user_prompt(actor_output: str, blocked_terms: list[str]) -> str:
    """원문과 감지된 guard 항목을 Pro repair 입력으로 렌더링합니다."""
    terms = "\n".join(f"- {term}" for term in blocked_terms[:80])
    all_terms = "\n".join(f"- {item.label}" for item in load_forbidden_terms()[:240])
    return (
        "<guard_hits>\n"
        f"{terms}\n"
        "</guard_hits>\n\n"
        "<full_guard_list>\n"
        f"{all_terms}\n"
        "</full_guard_list>\n\n"
        "<actor_output>\n"
        f"{actor_output}\n"
        "</actor_output>"
    )


async def repair_actor_output(actor_output: str, blocked_terms: list[str], model_name: str) -> str:
    """Guard 위반 Actor 응답을 Pro 모델로 최소 수정하고 수정본을 반환합니다."""
    if not actor_output.strip():
        return actor_output

    model = get_model(model_name=model_name, system_prompt=_load_repair_system_prompt())
    response = await model.generate_content_async(
        _repair_user_prompt(actor_output, blocked_terms),
        generation_config={
            "max_output_tokens": 8192,
            "temperature": 0.2,
            "thinking_config": {"thinking_level": "LOW"},
            "log_source": "output_repair",
        },
    )
    repaired = response.text.strip()
    return repaired or actor_output
