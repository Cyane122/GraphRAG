"""
액터 에이전트.

PromptBuilder가 조립한 3-파트 프롬프트(fixed / genre / dynamic)를
Gemini 모델에 전달해 롤플레이 응답 텍스트를 생성한다.

- MODEL_ACTOR: .env의 MODEL_ACTOR (기본값 gemini-3.1-pro-preview)
- thinking_level MEDIUM: 창작 품질과 속도의 균형점
- Implicit Caching: 동일 system prompt 반복 시 Gemini가 자동 캐시 적용
"""

import os
from dotenv import load_dotenv
from pathlib import Path

from src.core.llm.client import get_model, get_response_text

load_dotenv(Path(__file__).parent.parent.parent / ".env")

ACTOR_MODEL = os.getenv("MODEL_ACTOR", "gemini-3.1-pro-preview")
MAX_TOKENS  = round(int(os.getenv("MAX_TOKEN", 4096)) * 0.65 / 100) * 100


def run_actor(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    conversation_history: list[dict] | None = None,
) -> str:
    """
    Args:
        fixed_prompt:          고정 섹션 (operator_policy / rules / world / ...)
        genre_prompt:          씬별 묘사 규정
        dynamic_prompt:        매 턴 동적 섹션 (헤더 + character + context + user_input)
        conversation_history:  이전 대화 [{role, content}, ...] — Chainlit에서 관리
    Returns:
        응답 텍스트
    """

    system_text = f"{fixed_prompt}\n\n{genre_prompt}"
    model = get_model(model_name=ACTOR_MODEL, system_prompt=system_text)

    gemini_msgs = []

    if conversation_history:
        for msg in conversation_history:
            role = "model" if msg["role"] == "assistant" else "user"
            gemini_msgs.append({
                "role": role,
                "parts": [{"text": msg["content"]}],
            })

    gemini_msgs.append({
        "role": "user",
        "parts": [{"text": dynamic_prompt}],
    })

    response = model.generate_content(
        gemini_msgs,
        generation_config={
            "max_output_tokens": MAX_TOKENS,
            "temperature": 1.0,
            "thinking_config": {"thinking_level": "MEDIUM"},
        }
    )

    usage = response.usage_metadata
    prompt_tokens    = usage.prompt_token_count
    candidate_tokens = usage.candidates_token_count
    cached_tokens    = getattr(usage, "cached_content_token_count", 0)

    print(
        f"[{ACTOR_MODEL}] in={prompt_tokens} | "
        f"cached={cached_tokens} | "
        f"out={candidate_tokens} | "
        f"total={usage.total_token_count}"
    )

    return get_response_text(response)


# ── 단독 테스트 ───────────────────────────────────────────
if __name__ == "__main__":
    from src.agents.manager import run_manager

    fixed, dynamic, scene_types = run_manager(
        user_input="(소파에 앉아서 은서를 바라본다)",
        pc_id="sian",
        npc_id="eun_seo",
        recent_story="은서가 퇴근 후 막 집에 들어왔다. 오늘 헬스장에서 진상 손님이 있었다.",
        world_id="babe_univ"
    )

    print(f"[씬 타입] {scene_types}\n[모델] {ACTOR_MODEL}\n")
    print(run_actor(fixed, dynamic))
