# ================================
# src/ui/web_app/actor.py
#
# Chainlit-free Actor generation for fetch streaming.
#
# Functions
#   - recover_missing_analyze_prose(raw: str) -> tuple[str, bool] : Recover prose when Actor omits closing analyze tag.
#   - extract_scene_chars(raw_thinking: str, visible_text: str = "") -> list[str] : Extract visible secondary character names.
#   - _build_generation_config(model_name: str, system_text: str, max_token: int) -> types.GenerateContentConfig : Build a model-compatible generation config.
#   - _stream_actor_text_chunks(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, history: list[dict], genai_client: object, model_name: str, max_token: int) -> AsyncIterator[str] : Yield provider text chunks.
#   - stream_actor_events(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, history: list[dict], genai_client: object, model_name: str, max_token: int) -> AsyncIterator[dict] : Yield token events and a final event.
# ================================

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic
from google.genai import types

from src.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_CLAUDE_OPUS_4_6_MODEL,
    ANTHROPIC_CLAUDE_OPUS_4_7_MODEL,
    ANTHROPIC_CLAUDE_OPUS_4_8_MODEL,
    ANTHROPIC_CLAUDE_OPUS_MODEL,
    ANTHROPIC_CLAUDE_SONNET_MODEL,
)

_HEADER_HOUR_RE = re.compile(
    r"\*{1,2}\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*[월화수목금토일]요일\s*(\d{2})시\s*\d{2}분"
)
_HEADER_SPLIT_RE = re.compile(r"(?=\*\*\d{4}년)")
_PREFILL = "<analyze>\n"
_PROVIDER_PREFILL = _PREFILL.rstrip()
_ANALYZE_TAG_RE = re.compile(r"</?analyze>", re.IGNORECASE)
_META_LINE_RE = re.compile(
    r"^\s*(?:"
    r"CHARACTERS|STYLE|PLAN|CHECK|STATE|RELATIONSHIP|EVENT|LOCATION|TIME|SCENE|"
    r"SAFETY|CONSTRAINT|OUTPUT|SUMMARY|INTENT|SUBTEXT|BEATS?|NOTES?"
    r")\s*[:：]",
    re.IGNORECASE,
)


def _hour_from_response(text: str) -> int | None:
    """Parse response header hour when present."""
    match = _HEADER_HOUR_RE.search(text)
    return int(match.group(1)) if match else None


def recover_missing_analyze_prose(raw: str) -> tuple[str, bool]:
    """Recover visible prose when the model never closes the analyze block."""
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


def extract_scene_chars(raw_thinking: str, visible_text: str = "") -> list[str]:
    """Extract visible scene character names from Actor thinking JSON."""
    chars_m = re.search(r"CHARACTERS:\s*(\[.*?\])", raw_thinking, re.DOTALL)
    if not chars_m:
        return []
    try:
        parsed = json.loads(chars_m.group(1))
    except Exception:
        return []
    result: list[str] = []
    for char in parsed:
        if not isinstance(char, str):
            continue
        name = char.strip()
        if visible_text and name not in visible_text:
            continue
        if re.match(r"^[가-힣]{2,4}$", name) or re.match(r"^[가-힣]{2,4}의\s*[가-힣]{2,4}$", name):
            result.append(name)
    return result


def _compose_full_response(raw: str, raw_thinking: str, prose: str, recovered_missing_analyze: bool) -> str:
    """Return a frontend-ready response preserving the analyze block."""
    if "</analyze>" in raw and not recovered_missing_analyze:
        return raw.strip()
    return f"<analyze>\n{raw_thinking}\n</analyze>\n{prose}".strip()


def _build_generation_config(model_name: str, system_text: str, max_token: int) -> types.GenerateContentConfig:
    """Build a model-compatible generation config."""
    config: dict[str, object] = {
        "system_instruction": system_text,
        "max_output_tokens": max_token,
        "temperature": 1.0,
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if model_name.lower().startswith("gemini"):
        config["thinking_config"] = types.ThinkingConfig(thinking_level="HIGH")
    return types.GenerateContentConfig(**config)


def _is_gemini_model(model_name: str) -> bool:
    """Return whether the selected Actor model should use Google GenAI."""
    return model_name.lower().startswith("gemini")


def _is_claude_model(model_name: str) -> bool:
    """Return whether the selected Actor model should use the Anthropic API."""
    return model_name.lower().startswith("claude")


def _resolve_claude_model_name(model_name: str) -> str:
    """Map UI Claude ids to the configured Anthropic model ids."""
    lowered = model_name.lower()
    if "opus-4-6" in lowered:
        return ANTHROPIC_CLAUDE_OPUS_4_6_MODEL
    if "opus-4-7" in lowered:
        return ANTHROPIC_CLAUDE_OPUS_4_7_MODEL
    if "opus-4-8" in lowered:
        return ANTHROPIC_CLAUDE_OPUS_4_8_MODEL
    if "opus" in lowered:
        return ANTHROPIC_CLAUDE_OPUS_MODEL
    if "sonnet" in lowered:
        return ANTHROPIC_CLAUDE_SONNET_MODEL
    return model_name


def _gemini_messages(dynamic_prompt: str, history: list[dict]) -> list[dict]:
    """Build Google GenAI chat-style contents with the Actor prefill."""
    messages = [
        {
            "role": "model" if msg["role"] == "assistant" else "user",
            "parts": [{"text": msg["content"]}],
        }
        for msg in history
    ]
    messages.append({"role": "user", "parts": [{"text": dynamic_prompt}]})
    messages.append({"role": "model", "parts": [{"text": _PROVIDER_PREFILL}]})
    return messages


def _claude_messages(dynamic_prompt: str, history: list[dict]) -> list[dict]:
    """Build Anthropic messages ending with a user turn."""
    messages = [
        {
            "role": "assistant" if msg["role"] == "assistant" else "user",
            "content": str(msg["content"]),
        }
        for msg in history
    ]
    messages.append({
        "role": "user",
        "content": f"{dynamic_prompt}\n\nBegin your response with {_PROVIDER_PREFILL}.",
    })
    return messages


async def _stream_gemini_text_chunks(
    system_text: str,
    dynamic_prompt: str,
    history: list[dict],
    genai_client: object,
    model_name: str,
    max_token: int,
) -> AsyncIterator[str]:
    """Yield text chunks from Gemini through Google GenAI streaming."""
    finish_reason = None
    async for chunk in await genai_client.aio.models.generate_content_stream(
        model=model_name,
        contents=_gemini_messages(dynamic_prompt, history),
        config=_build_generation_config(model_name, system_text, max_token),
    ):
        if not chunk.candidates:
            continue
        candidate = chunk.candidates[0]
        # finish_reason은 본문 part가 빈 마지막 청크에 실려 오므로 part 체크보다 먼저 캡처한다.
        if candidate is not None and getattr(candidate, "finish_reason", None) is not None:
            finish_reason = candidate.finish_reason
        if not candidate or not candidate.content or not candidate.content.parts:
            continue

        for part in candidate.content.parts:
            text = part.text or ""
            if text:
                yield text

    # 토큰 한도로 응답이 잘렸으면 조용히 넘기지 않고 경고를 남긴다(silent truncation 방지).
    if finish_reason is not None and "MAX_TOKENS" in str(finish_reason).upper():
        print(f"[ActorStream] Gemini 응답이 토큰 한도로 잘렸습니다 (finish_reason={finish_reason}).")


async def _stream_claude_text_chunks(
    system_text: str,
    dynamic_prompt: str,
    history: list[dict],
    model_name: str,
    max_token: int,
) -> AsyncIterator[str]:
    """Yield text chunks from Claude through Anthropic streaming."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is required for Claude actor models.")

    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    stream = await client.messages.create(
        model=_resolve_claude_model_name(model_name),
        max_tokens=max_token,
        temperature=1.0,
        system=system_text,
        messages=_claude_messages(dynamic_prompt, history),
        stream=True,
    )
    stop_reason = None
    async for event in stream:
        etype = getattr(event, "type", "")
        if etype == "message_delta":
            delta = getattr(event, "delta", None)
            sr = getattr(delta, "stop_reason", None) if delta is not None else None
            if sr:
                stop_reason = sr
            continue
        if etype != "content_block_delta":
            continue
        delta = getattr(event, "delta", None)
        text = getattr(delta, "text", "") if delta is not None else ""
        if text:
            yield text

    # 토큰 한도로 응답이 잘렸으면 조용히 넘기지 않고 경고를 남긴다(silent truncation 방지).
    if stop_reason == "max_tokens":
        print(f"[ActorStream] Claude 응답이 토큰 한도로 잘렸습니다 (stop_reason={stop_reason}).")


async def _stream_actor_text_chunks(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    history: list[dict],
    genai_client: object,
    model_name: str,
    max_token: int,
) -> AsyncIterator[str]:
    """Yield raw Actor text chunks from the selected provider."""
    system_text = f"{fixed_prompt}\n\n{genre_prompt}" if genre_prompt else fixed_prompt
    if _is_gemini_model(model_name):
        async for text in _stream_gemini_text_chunks(
            system_text, dynamic_prompt, history, genai_client, model_name, max_token
        ):
            yield text
        return
    if _is_claude_model(model_name):
        async for text in _stream_claude_text_chunks(system_text, dynamic_prompt, history, model_name, max_token):
            yield text
        return
    raise ValueError(f"Unsupported actor model: {model_name}")


async def stream_actor_events(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    history: list[dict],
    genai_client: object,
    model_name: str,
    max_token: int,
) -> AsyncIterator[dict]:
    """Yield Actor token events followed by one final event."""
    raw = _PREFILL
    raw_thinking = ""
    thinking_buf = _PREFILL
    visible_parts: list[str] = []
    thinking_done = False
    recovered_missing_analyze = False

    async for text in _stream_actor_text_chunks(
        fixed_prompt=fixed_prompt,
        genre_prompt=genre_prompt,
        dynamic_prompt=dynamic_prompt,
        history=history,
        genai_client=genai_client,
        model_name=model_name,
        max_token=max_token,
    ):
        raw += text

        if thinking_done:
            visible_parts.append(text)
            yield {"type": "token", "content": text}
            continue

        thinking_buf += text
        if "</analyze>" in thinking_buf:
            head, tail = thinking_buf.split("</analyze>", 1)
            raw_thinking = re.sub(r"<analyze>\s*", "", head).strip()
            thinking_done = True
            if tail.lstrip():
                token = tail.lstrip()
                visible_parts.append(token)
                yield {"type": "token", "content": token}

    if not thinking_done and thinking_buf:
        match = _HEADER_SPLIT_RE.search(thinking_buf)
        if "</analyze>" in thinking_buf:
            head, tail = thinking_buf.split("</analyze>", 1)
            raw_thinking = re.sub(r"<analyze>\s*", "", head).strip()
            prose = tail.lstrip()
        elif match:
            raw_thinking = re.sub(r"<analyze>\s*", "", thinking_buf[:match.start()]).strip()
            prose = thinking_buf[match.start():]
            recovered_missing_analyze = True
        else:
            raw_thinking = re.sub(r"<analyze>\s*", "", thinking_buf).strip()
            prose, recovered_missing_analyze = recover_missing_analyze_prose(thinking_buf)
        if prose:
            visible_parts.append(prose)
            yield {"type": "token", "content": prose}

    visible_text = "".join(visible_parts).strip()
    full_response = _compose_full_response(raw, raw_thinking, visible_text, recovered_missing_analyze)
    yield {
        "type": "complete",
        "content": full_response,
        "visible_text": visible_text,
        "scene_chars": extract_scene_chars(raw_thinking, visible_text),
        "hour": _hour_from_response(visible_text),
        "raw_thinking": raw_thinking,
    }
