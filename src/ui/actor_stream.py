# ================================
# src/ui/actor_stream.py
#
# Gemini Actor 응답을 Chainlit 메시지로 스트리밍합니다.
#
# Functions
#   - recover_missing_analyze_prose(raw: str) -> tuple[str, bool] : Recover prose when Actor omits the closing analyze tag
#   - stream_actor(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, history: list[dict], genai_client: object, model_name: str, max_token: int, npc_name: str, logs_dir: Path, status_text: str, send_output: bool = True) -> tuple[str, list[str], cl.Message, int | None, str] : Actor 응답 생성
# ================================

import asyncio
import json
import re
from pathlib import Path

import chainlit as cl
from google.genai import types

from src.ui.status import send_status_toast

_HEADER_HOUR_RE = re.compile(
    r"\*{1,2}\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*[월화수목금토일]요일\s*(\d{2})시\s*\d{2}분"
)
_HEADER_SPLIT_RE = re.compile(r"(?=\*\*\d{4}년)")
_PREFILL = "<analyze>\n"
_ANALYZE_TAG_RE = re.compile(r"</?analyze>", re.IGNORECASE)
_META_LINE_RE = re.compile(
    r"^\s*(?:"
    r"CHARACTERS|STYLE|PLAN|CHECK|STATE|RELATIONSHIP|EVENT|LOCATION|TIME|SCENE|"
    r"SAFETY|CONSTRAINT|OUTPUT|SUMMARY|INTENT|SUBTEXT|BEATS?|NOTES?"
    r")\s*[:：]",
    re.IGNORECASE,
)


def _hour_from_response(text: str) -> int | None:
    """응답 텍스트의 날짜 헤더에서 시각(0-23)을 파싱합니다."""
    match = _HEADER_HOUR_RE.search(text)
    return int(match.group(1)) if match else None


def _extract_prose(raw: str) -> str:
    """Actor raw text에서 analyze 블록을 제거한 prose만 반환합니다."""
    if "</analyze>" in raw:
        return re.sub(r"<analyze>.*?</analyze>", "", raw, flags=re.DOTALL).strip()
    return recover_missing_analyze_prose(raw)[0]


def recover_missing_analyze_prose(raw: str) -> tuple[str, bool]:
    """Recover user-visible prose when the model never closes the analyze block."""
    match = _HEADER_SPLIT_RE.search(raw)
    if match:
        return raw[match.start():].strip(), True

    cleaned = _ANALYZE_TAG_RE.sub("", raw.replace(_PREFILL, "", 1))
    recovered_lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            if recovered_lines and recovered_lines[-1]:
                recovered_lines.append("")
            continue
        if _META_LINE_RE.match(stripped):
            continue
        if stripped.startswith(("-", "*")) and _META_LINE_RE.match(stripped.lstrip("-* ")):
            continue
        recovered_lines.append(line)

    recovered = "\n".join(recovered_lines).strip()
    return recovered, bool(recovered)


def _extract_scene_chars(raw_thinking: str) -> list[str]:
    """thinking 텍스트에서 등장인물 이름/서술어를 추출합니다.
    순수 이름(2-4자 한글) 또는 'X의 Y' 관계 서술어를 원문 그대로 반환합니다."""
    chars_m = re.search(r"CHARACTERS:\s*(\[.*?\])", raw_thinking, re.DOTALL)
    if not chars_m:
        return []
    try:
        parsed = json.loads(chars_m.group(1))
    except Exception:
        return []
    result = []
    for char in parsed:
        if not isinstance(char, str):
            continue
        name = char.strip()
        # 순수 이름: 2-4자 한글
        if re.match(r"^[가-힣]{2,4}$", name):
            result.append(name)
        # 관계 서술어: 'X의 Y' (각 부분 2-4자 한글)
        elif re.match(r"^[가-힣]{2,4}의\s*[가-힣]{2,4}$", name):
            result.append(name)
    return result


async def _flush_remainder(
    remainder: str,
    gen_msg: cl.CustomElement,
    response_msg: cl.Message,
    first_text: bool,
    send_output: bool,
) -> bool:
    """분석 블록 이후 남은 prose를 UI에 출력하고 first_text 상태를 반환합니다."""
    if not remainder:
        return first_text
    if not send_output:
        return first_text
    if first_text:
        await gen_msg.remove()
        await response_msg.send()
        first_text = False
    await response_msg.stream_token(remainder)
    return first_text


async def stream_actor(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    history: list[dict],
    genai_client: object,
    model_name: str,
    max_token: int,
    npc_name: str,
    logs_dir: Path,
    status_text: str,
    send_output: bool = True,
) -> tuple[str, list[str], cl.Message, int | None, str]:
    """Gemini 스트림으로 Actor 응답을 생성하고 필요하면 UI에 표시합니다."""
    system_text = f"{fixed_prompt}\n\n{genre_prompt}" if genre_prompt else fixed_prompt
    gemini_msgs = [
        {
            "role": "model" if msg["role"] == "assistant" else "user",
            "parts": [{"text": msg["content"]}],
        }
        for msg in history
    ]
    gemini_msgs.append({"role": "user", "parts": [{"text": dynamic_prompt}]})
    gemini_msgs.append({"role": "model", "parts": [{"text": _PREFILL}]})

    gen_msg = await send_status_toast(status_text)
    response_msg = cl.Message(content="", author=npc_name)

    raw = _PREFILL
    raw_thinking = ""
    thinking_buf = _PREFILL
    thinking_done = False
    first_text = True
    recovered_missing_analyze = False

    try:
        try:
            async for chunk in await genai_client.aio.models.generate_content_stream(
                model=model_name,
                contents=gemini_msgs,
                config=types.GenerateContentConfig(
                    system_instruction=system_text,
                    max_output_tokens=max_token,
                    temperature=1.0,
                    thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            ):
                if not chunk.candidates:
                    continue
                candidate = chunk.candidates[0]
                if not candidate or not candidate.content or not candidate.content.parts:
                    continue

                for part in candidate.content.parts:
                    text = part.text or ""
                    if not text:
                        continue
                    raw += text

                    if thinking_done:
                        if send_output and first_text:
                            await gen_msg.remove()
                            await response_msg.send()
                            first_text = False
                        if send_output:
                            await response_msg.stream_token(text)
                        continue

                    thinking_buf += text
                    if "</analyze>" in thinking_buf:
                        head, tail = thinking_buf.split("</analyze>", 1)
                        raw_thinking = re.sub(r"<analyze>\s*", "", head).strip()
                        thinking_done = True
                        first_text = await _flush_remainder(
                            tail.lstrip(), gen_msg, response_msg, first_text, send_output
                        )
        except Exception as exc:
            print(f"[Actor] 스트리밍 오류: {exc}")

        if not thinking_done and thinking_buf:
            match = _HEADER_SPLIT_RE.search(thinking_buf)
            if "</analyze>" in thinking_buf:
                head, tail = thinking_buf.split("</analyze>", 1)
                raw_thinking = re.sub(r"<analyze>\s*", "", head).strip()
                remainder = tail.lstrip()
            elif match:
                raw_thinking = re.sub(r"<analyze>\s*", "", thinking_buf[:match.start()]).strip()
                remainder = thinking_buf[match.start():]
                recovered_missing_analyze = True
            else:
                raw_thinking = re.sub(r"<analyze>\s*", "", thinking_buf).strip()
                remainder, recovered_missing_analyze = recover_missing_analyze_prose(thinking_buf)
            first_text = await _flush_remainder(
                remainder, gen_msg, response_msg, first_text, send_output
            )

        if send_output and first_text:
            await gen_msg.remove()
            await response_msg.send()
    finally:
        # CancelledError(세션 종료) 포함 항상 실행 — step을 JSON에 확정 저장
        # asyncio.shield: 외부 태스크가 취소돼도 update()는 완료까지 보장
        try:
            if send_output:
                await asyncio.shield(response_msg.update())
            else:
                await asyncio.shield(gen_msg.remove())
        except Exception:
            pass

    prose = _extract_prose(raw)
    logs_dir.mkdir(exist_ok=True)
    (logs_dir / "raw_full.txt").write_text(raw, encoding="utf-8")
    (logs_dir / "raw_output.txt").write_text(prose, encoding="utf-8")
    (logs_dir / "raw_thinking.txt").write_text(raw_thinking, encoding="utf-8")
    (logs_dir / "actor_recovery.json").write_text(
        json.dumps(
            {"missing_analyze_recovered": recovered_missing_analyze},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{'=' * 60}\n[Actor Prose]\n{prose[:800]}\n{'=' * 60}")
    print(
        f"[Actor Thinking ({len(raw_thinking)}chars)] / prose={len(prose)}chars "
        f"/ recovered_missing_analyze={recovered_missing_analyze}"
    )

    return prose, _extract_scene_chars(raw_thinking), response_msg, _hour_from_response(prose), raw_thinking
