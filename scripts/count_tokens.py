# count_tokens.py
# 실행: python count_tokens.py

import os
import anthropic
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

client = anthropic.Anthropic()
MODEL  = os.getenv("MODEL_ACTOR", "claude-haiku-4-5-20251001")


def count(*, system: str = "", user: str = "") -> int:
    kwargs = {
        "model":    MODEL,
        "messages": [{"role": "user", "content": user or "(empty)"}],
    }
    if system:
        kwargs["system"] = system

    result = client.messages.count_tokens(**kwargs)
    return result.input_tokens


if __name__ == "__main__":
    from src.agents.manager import run_manager

    fixed, dynamic, scene_types = run_manager(
        user_input="(소파에 앉아서 은서를 바라본다)",
        pc_id="sian",
        npc_id="eun_seo",
        recent_story="은서가 퇴근 후 막 집에 들어왔다.",
    )

    from src.agents.prompt_factory.ooc_handler import _SYSTEM_PROMPT, LOCATIONS
    from src.simulation.state.classifier import CLASSIFIER_MODEL

    sections = {
        "fixed_prompt (캐싱 대상)":    count(system=fixed),
        "dynamic_prompt (매 턴 교체)": count(user=dynamic),
        "OOC system prompt":           count(system=_SYSTEM_PROMPT.format(
                                           locations="\n".join(f'"{k}": "{v}"' for k, v in LOCATIONS.items())
                                       )),
    }

    print(f"{'섹션':<30} {'토큰':>6}  {'캐싱 기준(1024)':>15}")
    print("-" * 55)
    for name, tokens in sections.items():
        met = "✅ 캐싱 가능" if tokens >= 1024 else "❌ 미달"
        print(f"{name:<30} {tokens:>6}  {met}")

    total = sections["fixed_prompt (캐싱 대상)"] + sections["dynamic_prompt (매 턴 교체)"]
    print(f"\n총 입력 토큰 (1턴 기준): {total}")