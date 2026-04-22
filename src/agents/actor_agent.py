# src/agents/actor_agent.py

import os
from dotenv import load_dotenv
from pathlib import Path

from src.utils.llm_utils import llm_client

load_dotenv(Path(__file__).parent.parent.parent / ".env")

ACTOR_MODEL = os.getenv("MODEL_ACTOR", "claude-haiku-4-5-20251001")
MAX_TOKENS     = int(os.getenv("MAX_TOKEN", 4096))
TEMPERATURE    = 1.0   # extended thinking 없이 top-p 조절은 temperature=1 권장


def run_actor(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    conversation_history: list[dict] | None = None,
) -> str:
    """
    Prompt Caching 적용:
      - fixed_prompt (system) → cache_control: ephemeral  (최대 5000토큰, 5분 TTL)
      - dynamic_prompt는 매 턴 교체되므로 캐싱 대상 아님

    Args:
        fixed_prompt:          고정 섹션 (operator_policy / rules / world / ...)
        genre_prompt:          각 상황마다 적용되는 묘사 규정
        dynamic_prompt:        매 턴 동적 섹션 (헤더 + character + context + user_input)
        conversation_history:  이전 대화 [{role, content}, ...] — Chainlit에서 관리
    Returns:
        응답 텍스트
    """

    # ── 시스템 프롬프트 (캐시 마킹) ───────────────────────
    system_text = f"{fixed_prompt}\n\n{genre_prompt}"

    system = [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},   # ← Prompt Cache 핵심
        }
    ]

    # ── 메시지 조립 ────────────────────────────────────────
    messages: list[dict] = []

    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": dynamic_prompt})

    # ── API 호출 ──────────────────────────────────────────
    response = llm_client.messages.create(
        model=ACTOR_MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system,
        messages=messages,
    )

    # 캐시 히트 현황 로깅 (개발용)
    usage = response.usage
    cache_read    = getattr(usage, "cache_read_input_tokens",    0)
    cache_created = getattr(usage, "cache_creation_input_tokens", 0)
    print(
        f"[{ACTOR_MODEL}] in={usage.input_tokens} | "
        f"cache_created={cache_created} | cache_read={cache_read} | "
        f"out={usage.output_tokens}"
    )

    return response.content[0].text


# ── 단독 테스트 ───────────────────────────────────────────
if __name__ == "__main__":
    from src.agents.manager_agent import run_manager

    fixed, dynamic, scene_types = run_manager(
        user_input="(소파에 앉아서 은서를 바라본다)",
        pc_id="sian",
        npc_id="eun_seo",
        recent_story="은서가 퇴근 후 막 집에 들어왔다. 오늘 헬스장에서 진상 손님이 있었다.",
        world_id="babe_univ"
    )

    print(f"[씬 타입] {scene_types}\n[모델] {ACTOR_MODEL}\n")
    print(run_actor(fixed, dynamic))