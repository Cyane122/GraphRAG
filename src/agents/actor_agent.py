# src/agents/actor_agent.py

from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")

llm = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

ACTOR_MODEL = "openai/gpt-oss-120b:free" # "meta-llama/llama-3.3-70b-instruct:free"


def run_actor(
    fixed_prompt: str,
    dynamic_prompt: str,
    conversation_history: list[dict] = None,
) -> str:
    """
    fixed_prompt  : 시스템 프롬프트 (캐시 대상)
    dynamic_prompt: 매 턴 유저 메시지
    conversation_history: 이전 대화 기록 [{"role": "user/assistant", "content": "..."}]
    """

    messages = [{"role": "system", "content": fixed_prompt}]

    # 이전 대화 기록 주입
    if conversation_history:
        messages.extend(conversation_history)

    # 현재 턴 동적 프롬프트
    messages.append({"role": "user", "content": dynamic_prompt})

    response = llm.chat.completions.create(
        model=ACTOR_MODEL,
        messages=messages,
        temperature=0.85,
        max_tokens=3000,
    )

    return response.choices[0].message.content


# ── 테스트 ────────────────────────────────────────────────
if __name__ == "__main__":
    from src.agents.manager_agent import run_manager

    fixed, dynamic, scene_types = run_manager(
        user_input="(소파에 앉아서 은서를 바라본다)",
        pc_id="sian",
        npc_id="eun_seo",
        recent_story="은서가 퇴근 후 막 집에 들어왔다. 오늘 헬스장에서 진상 손님이 있었다.",
    )

    print(f"[씬 타입] {scene_types}")
    print("[Actor 응답 생성 중...]\n")

    response = run_actor(fixed, dynamic)
    print(response)