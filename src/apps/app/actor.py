# ================================
# src/apps/app/actor.py
#
# Actor generation for fetch-based streaming (provider-agnostic).
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
import time
from collections.abc import AsyncIterator
from time import perf_counter

from anthropic import APIStatusError, AsyncAnthropic, AsyncAnthropicVertex, RateLimitError
from google.genai import types

from src.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_CLAUDE_OPUS_4_6_MODEL,
    ANTHROPIC_CLAUDE_OPUS_4_7_MODEL,
    ANTHROPIC_CLAUDE_OPUS_4_8_MODEL,
    ANTHROPIC_CLAUDE_OPUS_MODEL,
    ANTHROPIC_CLAUDE_SONNET_MODEL,
    ANTHROPIC_VERTEX_REGION,
    GOOGLE_PROJECT_ID,
)
from src.core.llm.client import record_llm_latency

_anthropic_client: AsyncAnthropic | None = None
_anthropic_vertex_client: AsyncAnthropicVertex | None = None


def _get_anthropic_client() -> AsyncAnthropic:
    """Return the module-level direct Anthropic client, creating it on first use."""
    global _anthropic_client
    if _anthropic_client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is required for Claude actor models.")
        _anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


def _get_anthropic_vertex_client() -> AsyncAnthropicVertex:
    """Return the module-level Claude-on-Vertex client, creating it on first use.

    Vertex는 ADC(GOOGLE_APPLICATION_CREDENTIALS)로 인증한다 — 별도 키 불필요.
    """
    global _anthropic_vertex_client
    if _anthropic_vertex_client is None:
        if not GOOGLE_PROJECT_ID:
            raise RuntimeError("GOOGLE_PROJECT_ID is required for Claude on Vertex AI.")
        _anthropic_vertex_client = AsyncAnthropicVertex(
            project_id=GOOGLE_PROJECT_ID,
            region=ANTHROPIC_VERTEX_REGION,
        )
    return _anthropic_vertex_client


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
    """Map UI Claude ids to the configured direct Anthropic model ids."""
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


def _claude_sampling_kwargs(resolved_model: str) -> dict:
    """Return sampling params accepted by the resolved model.

    Opus 4.7/4.8·Fable은 temperature/top_p/top_k를 보내면 400을 반환하므로 제외한다.
    그 외(Opus 4.6·Sonnet 4.6 등)는 롤플레이 다양성을 위해 temperature=1.0을 유지한다.
    """
    lowered = resolved_model.lower()
    if "opus-4-7" in lowered or "opus-4-8" in lowered or "fable" in lowered:
        return {}
    return {"temperature": 1.0}


def _is_quota_error(exc: Exception) -> bool:
    """Vertex 호출이 비용/쿼터/빌링 문제로 실패했는지 판단한다(폴백 트리거).

    429(쿼터 소진)·403(빌링 비활성/권한 거부)을 폴백 대상으로 본다.
    404(모델 ID 오설정)는 폴백하지 않고 그대로 전파해 설정 오류를 드러낸다.
    """
    if isinstance(exc, RateLimitError):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code in (403, 429)


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


def _claude_system_blocks(fixed_prompt: str, genre_prompt: str) -> list[dict]:
    """Build Anthropic system blocks with cache breakpoints on the stable prefix.

    Fixed는 턴 간 불변이라 항상 캐시 히트하고, Genre는 씬 타입별로 교체되므로
    별도 breakpoint를 둔다(씬이 바뀌어도 Fixed 블록 prefix는 계속 히트).
    cache_control이 없으면 Anthropic은 프롬프트 캐싱을 전혀 하지 않는다.
    """
    blocks: list[dict] = [
        {"type": "text", "text": fixed_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    if genre_prompt:
        blocks.append(
            {"type": "text", "text": genre_prompt, "cache_control": {"type": "ephemeral"}}
        )
    return blocks


def _claude_messages(dynamic_prompt: str, history: list[dict]) -> list[dict]:
    """Build Anthropic messages ending with a user turn.

    history 마지막 메시지에 cache breakpoint를 둬 턴 간 대화 prefix를 재사용한다.
    현재 턴(dynamic_prompt)은 매 턴 달라지는 volatile 부분이라 breakpoint 없음.
    """
    messages: list[dict] = [
        {
            "role": "assistant" if msg["role"] == "assistant" else "user",
            "content": str(msg["content"]),
        }
        for msg in history
    ]
    if messages:
        last = messages[-1]
        last["content"] = [
            {
                "type": "text",
                "text": last["content"],
                "cache_control": {"type": "ephemeral"},
            }
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


async def _open_claude_stream(
    system_blocks: list[dict],
    messages: list[dict],
    model_name: str,
    max_token: int,
):
    """Open a Claude stream on Vertex first; fall back to the direct API on quota errors.

    스트림은 await 시점(첫 토큰 전)에 HTTP 요청을 보내므로 비용/쿼터 실패는 여기서 잡혀
    아직 토큰이 흐르기 전에 다이렉트 API로 폴백할 수 있다.
    Vertex와 다이렉트 API의 모델 ID는 4.6+/Sonnet 4.6에서 동일해 같은 문자열을 쓴다.
    """
    resolved = _resolve_claude_model_name(model_name)
    kwargs = dict(
        model=resolved,
        max_tokens=max_token,
        system=system_blocks,
        messages=messages,
        stream=True,
        **_claude_sampling_kwargs(resolved),
    )
    try:
        return await _get_anthropic_vertex_client().messages.create(**kwargs)
    except Exception as exc:
        if not _is_quota_error(exc):
            raise
        print(f"[ActorStream] Vertex Claude 호출이 비용/쿼터/빌링 한도로 실패 → 다이렉트 API로 폴백 ({exc}).")
        return await _get_anthropic_client().messages.create(**kwargs)


async def _stream_claude_text_chunks(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    history: list[dict],
    model_name: str,
    max_token: int,
) -> AsyncIterator[str]:
    """Yield text chunks from Claude through Anthropic streaming."""
    stream = await _open_claude_stream(
        _claude_system_blocks(fixed_prompt, genre_prompt),
        _claude_messages(dynamic_prompt, history),
        model_name,
        max_token,
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
        async for text in _stream_claude_text_chunks(
            fixed_prompt, genre_prompt, dynamic_prompt, history, model_name, max_token
        ):
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

    # 측정용: 웹 스트리밍 Actor 호출의 총 지연을 기록한다(이 경로는 generate_content_async를 우회).
    start_epoch_ms = int(time.time() * 1000)
    started = perf_counter()
    status = "ok"
    try:
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
    except Exception:
        status = "error"
        raise
    finally:
        record_llm_latency(
            "actor", model_name, start_epoch_ms,
            int((perf_counter() - started) * 1000), None, status,
        )

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
