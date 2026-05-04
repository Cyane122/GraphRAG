"""
Gemini 스트리밍 thought 파트 탐지 테스트.

확인 항목:
1. thought 파트가 실제로 스트림에 포함되는지
2. 속성명이 'thought'인지 'is_thought'인지 기타인지
3. thinking_level별 (LOW / MEDIUM / HIGH) 동작 차이
"""

import asyncio
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID")

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="global",
)

MODEL = "gemini-3.1-pro-preview"

PROMPT = """
다음 지시를 수행하세요.

<thinking>
SCENE: [1 sentence]
CHARACTERS: ["홍길동", "김철수"]
</thinking>

위 thinking 분석을 마친 뒤, "안녕하세요." 라고만 답하세요.
"""


async def test_level(level: str):
    print(f"\n{'='*60}")
    print(f"thinking_level = {level}")
    print('='*60)

    raw_text    = ""
    raw_thinking = ""
    part_log    = []

    async for chunk in await client.aio.models.generate_content_stream(
        model=MODEL,
        contents=[{"role": "user", "parts": [{"text": PROMPT}]}],
        config=types.GenerateContentConfig(
            max_output_tokens=500,
            temperature=1.0,
            thinking_config=types.ThinkingConfig(thinking_level=level),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        ),
    ):
        if not chunk.candidates:
            continue
        for part in chunk.candidates[0].content.parts:
            # 속성 전체 목록 기록 (첫 파트만)
            if not part_log:
                thought_attrs = [a for a in dir(part) if "thought" in a.lower()]
                part_log.append(thought_attrs)
                print(f"[속성 탐지] thought 관련 속성: {thought_attrs}")

            # 각 속성 시도
            is_thought = (
                getattr(part, "thought", False)
                or getattr(part, "is_thought", False)
            )

            if is_thought:
                raw_thinking += part.text or ""
            elif part.text:
                raw_text += part.text

    print(f"[텍스트 출력] {repr(raw_text[:200])}")
    print(f"[thinking 길이] {len(raw_thinking)} chars")
    if raw_thinking:
        print(f"[thinking 내용 앞 300자]\n{raw_thinking[:300]}")
    else:
        print("[thinking] 비어 있음 — thought 파트 미수신")


async def main():
    for level in ["LOW", "MEDIUM", "HIGH"]:
        await test_level(level)


if __name__ == "__main__":
    asyncio.run(main())