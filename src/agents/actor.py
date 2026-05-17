# ================================
# src/agents/actor.py
#
# PromptBuilder가 조립한 3-파트 프롬프트를 Gemini Actor 모델에 전달합니다.
#
# Functions
#   - _log_usage_metadata(response: object) -> None : Gemini usage metadata가 있으면 토큰 사용량 출력
#   - run_actor(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, conversation_history: list[dict] | None) -> str : Gemini에 프롬프트 전달 후 응답 텍스트 반환 (async)
# ================================

from src.config import MODEL_ACTOR as ACTOR_MODEL, MAX_TOKEN
from src.core.logging.prompt_debug import build_prompt_fingerprint, format_prompt_fingerprint
from src.core.llm.client import get_model, get_response_text

MAX_TOKENS = round(MAX_TOKEN * 0.65 / 100) * 100


def _log_usage_metadata(response: object) -> None:
    """Gemini 응답에 usage metadata가 있으면 토큰 사용량을 출력한다."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        print(f"[{ACTOR_MODEL}] usage metadata unavailable")
        return

    prompt_tokens = getattr(usage, "prompt_token_count", None)
    candidate_tokens = getattr(usage, "candidates_token_count", None)
    cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
    total_tokens = getattr(usage, "total_token_count", None)

    print(
        f"[{ACTOR_MODEL}] in={prompt_tokens} | "
        f"cached={cached_tokens} | "
        f"out={candidate_tokens} | "
        f"total={total_tokens}"
    )


async def run_actor(
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
    prompt_fingerprint = build_prompt_fingerprint(
        fixed_prompt=fixed_prompt,
        genre_prompt=genre_prompt,
        dynamic_prompt=dynamic_prompt,
        history=conversation_history or [],
    )
    print(format_prompt_fingerprint(prompt_fingerprint))

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

    response = await model.generate_content_async(
        gemini_msgs,
        generation_config={
            "max_output_tokens": MAX_TOKENS,
            "temperature": 1.0,
            "thinking_config": {"thinking_level": "MEDIUM"},
        }
    )

    _log_usage_metadata(response)

    return get_response_text(response)


# ── 단독 테스트 ───────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    from src.agents.manager import run_manager

    async def _main():
        fixed, genre, dynamic, scene_types = await run_manager(
            user_input="(소파에 앉아서 은서를 바라본다)",
            pc_id="sian",
            npc_id="eun_seo",
            recent_story="은서가 퇴근 후 막 집에 들어왔다. 오늘 헬스장에서 진상 손님이 있었다.",
            world_id="babe_univ"
        )
        print(f"[씬 타입] {scene_types}\n[모델] {ACTOR_MODEL}\n")
        print(await run_actor(fixed, genre, dynamic))

    asyncio.run(_main())
