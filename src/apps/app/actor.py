# ================================
# src/apps/app/actor.py
#
# Actor generation for fetch-based streaming (provider-agnostic).
#
# Functions
#   - recover_missing_analyze_prose(raw: str) -> tuple[str, bool] : Recover prose when Actor omits closing analyze tag.
#   - extract_scene_chars(raw_thinking: str, visible_text: str = "") -> list[str] : Extract visible secondary character names.
#   - _build_generation_config(model_name: str, system_text: str, max_token: int) -> types.GenerateContentConfig : Build a model-compatible generation config.
#   - _stream_actor_text_chunks(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, history: list[dict], genai_client: object, model_name: str, max_token: int, usage_sink: dict[str, int | None] | None = None) -> AsyncIterator[str] : Yield provider text chunks.
#   - _stream_actor_text_chunks_resilient(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, history: list[dict], genai_client: object, model_name: str, max_token: int, usage_sink: dict[str, int | None] | None = None) -> AsyncIterator[str] : Yield provider text chunks, retrying the whole stream on pre-first-token network blips.
#   - stream_actor_events(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, history: list[dict], genai_client: object, model_name: str, max_token: int) -> AsyncIterator[dict] : Yield token events and a final event.
# ================================

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import AsyncIterator
from time import perf_counter

import httpx
from anthropic import APIConnectionError
from google.genai import types

from src.apps.app.settings import load_settings
from src.config import (
    ANTHROPIC_CLAUDE_OPUS_4_6_MODEL,
    ANTHROPIC_CLAUDE_OPUS_4_7_MODEL,
    ANTHROPIC_CLAUDE_OPUS_4_8_MODEL,
    ANTHROPIC_CLAUDE_OPUS_5_MODEL,
    ANTHROPIC_CLAUDE_OPUS_MODEL,
    ANTHROPIC_CLAUDE_SONNET_5_MODEL,
    ANTHROPIC_CLAUDE_SONNET_MODEL,
    DEEPSEEK_V4_PRO_MODEL,
    LLM_MAX_RETRIES_429,
)
from src.core.llm.client import (
    get_anthropic_client,
    get_anthropic_vertex_client,
    get_deepseek_client,
    is_retryable_provider_limit,
    record_llm_latency,
    stream_anthropic_text_chunks,
    usage_token_counts,
)

_HEADER_HOUR_RE = re.compile(
    r"\*{1,2}\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*[월화수목금토일]요일\s*(\d{2})시\s*\d{2}분"
)
_HEADER_SPLIT_RE = re.compile(r"(?=\*\*\d{4}년)")
_PREFILL = "<analyze>\n"
_PROVIDER_PREFILL = _PREFILL.rstrip()
_ANALYZE_TAG_RE = re.compile(r"</?analyze>", re.IGNORECASE)

# 스트리밍 중 끊길 수 있는 일시적 네트워크 오류(재시도/우아한 종료 대상).
# httpx.TransportError는 ReadError/ReadTimeout/RemoteProtocolError/ConnectError 등의 베이스.
_STREAM_TRANSIENT_ERRORS = (httpx.TransportError, APIConnectionError)
_ACTOR_STREAM_MAX_ATTEMPTS = 3  # 첫 토큰 전 네트워크 블립 시 전체 스트림 재시도 횟수
_ACTOR_STREAM_BACKOFF_SEC = 0.5  # 네트워크 재시도 간 백오프 기준(시도 순번에 비례)
# 첫 토큰 전 403/429(주로 Gemini Actor)일 때 exponential backoff 재시도. Actor는 사용자가
# 기다리는 foreground 호출이라 대기 상한을 updater보다 짧게 잡는다.
_ACTOR_STREAM_RATE_LIMIT_CAP_SEC = 15
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
        config["thinking_config"] = types.ThinkingConfig(
            thinking_level=load_settings().actor_thinking_level
        )
    return types.GenerateContentConfig(**config)


def _is_gemini_model(model_name: str) -> bool:
    """Return whether the selected Actor model should use Google GenAI."""
    return model_name.lower().startswith("gemini")


def _is_claude_model(model_name: str) -> bool:
    """Return whether the selected Actor model should use the Anthropic API."""
    return model_name.lower().startswith("claude")


def _is_deepseek_model(model_name: str) -> bool:
    """Return whether the selected Actor model should use the DeepSeek API."""
    return model_name.lower().startswith("deepseek")


def _resolve_claude_model_name(model_name: str) -> str:
    """Map UI Claude ids to the configured direct Anthropic model ids."""
    lowered = model_name.lower()
    if "sonnet-5" in lowered:
        return ANTHROPIC_CLAUDE_SONNET_5_MODEL
    if "opus-5" in lowered:
        return ANTHROPIC_CLAUDE_OPUS_5_MODEL
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


def _resolve_deepseek_model_name(model_name: str) -> str:
    """Map UI DeepSeek ids to the configured DeepSeek model ids."""
    lowered = model_name.lower()
    if "v4-pro" in lowered:
        return DEEPSEEK_V4_PRO_MODEL
    return model_name


def _claude_sampling_kwargs(resolved_model: str) -> dict:
    """Return sampling params accepted by the resolved model.

    Opus 5·Opus 4.7/4.8·Fable은 temperature/top_p/top_k를 보내면 400을 반환하므로 제외한다.
    그 외(Opus 4.6·Sonnet 4.6·Sonnet 5 등)는 롤플레이 다양성을 위해 temperature=1.0을 유지한다.
    """
    lowered = resolved_model.lower()
    if "opus-5" in lowered or "opus-4-7" in lowered or "opus-4-8" in lowered or "fable" in lowered:
        return {}
    return {"temperature": 1.0}


def _safe_usage_metadata(owner: object) -> object | None:
    """Return usage_metadata when available without aborting generation on access errors."""
    try:
        return getattr(owner, "usage_metadata", None)
    except Exception:
        return None


def _merge_usage_sink(usage_sink: dict[str, int | None], usage: object) -> None:
    """Merge only non-None token counts into the caller-provided usage sink."""
    for key, value in usage_token_counts(usage).items():
        if value is not None:
            usage_sink[key] = value


def _gemini_messages(dynamic_prompt: str, history: list[dict]) -> list[dict]:
    """Build Google GenAI contents ending with a user turn and prefill instruction."""
    messages = [
        {
            "role": "model" if msg["role"] == "assistant" else "user",
            "parts": [{"text": msg["content"]}],
        }
        for msg in history
    ]
    messages.append({
        "role": "user",
        "parts": [{"text": f"{dynamic_prompt}\n\nBegin your response with {_PROVIDER_PREFILL}."}],
    })
    return messages


def _anthropic_system_blocks(fixed_prompt: str, genre_prompt: str) -> list[dict]:
    """Build Anthropic-compatible system blocks with cache breakpoints on the stable prefix.

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


def _anthropic_messages(dynamic_prompt: str, history: list[dict]) -> list[dict]:
    """Build Anthropic-compatible messages ending with a user turn.

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
    usage_sink: dict[str, int | None] | None = None,
) -> AsyncIterator[str]:
    """Yield text chunks from Gemini through Google GenAI streaming.

    usage_sink가 주어지면 마지막으로 보고된 usage_metadata의 토큰 수를 채워
    호출자가 스트리밍 턴의 입력/출력 토큰을 계측할 수 있게 한다.
    """
    finish_reason = None
    async for chunk in await genai_client.aio.models.generate_content_stream(
        model=model_name,
        contents=_gemini_messages(dynamic_prompt, history),
        config=_build_generation_config(model_name, system_text, max_token),
    ):
        if usage_sink is not None:
            chunk_usage = _safe_usage_metadata(chunk)
            if chunk_usage is not None:
                _merge_usage_sink(usage_sink, chunk_usage)
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
    Vertex와 다이렉트 API의 모델 ID는 4.6+/Sonnet 5에서 동일해 같은 문자열을 쓴다.
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
        return await get_anthropic_vertex_client().messages.create(**kwargs)
    except Exception as exc:
        if not is_retryable_provider_limit(exc):
            raise
        print(f"[ActorStream] Vertex Claude 호출이 비용/쿼터/빌링 한도로 실패 → 다이렉트 API로 폴백 ({exc}).")
        return await get_anthropic_client().messages.create(**kwargs)


async def _open_deepseek_stream(
    system_blocks: list[dict],
    messages: list[dict],
    model_name: str,
    max_token: int,
):
    """Open a DeepSeek stream with thinking enabled at high effort."""
    return await get_deepseek_client().messages.create(
        model=_resolve_deepseek_model_name(model_name),
        max_tokens=max_token,
        system=system_blocks,
        messages=messages,
        stream=True,
        temperature=1.3,
        extra_body={
            "thinking": {"type": "enabled"},
            "output_config": {"effort": "high"},
        },
    )


async def _stream_actor_text_chunks(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    history: list[dict],
    genai_client: object,
    model_name: str,
    max_token: int,
    usage_sink: dict[str, int | None] | None = None,
) -> AsyncIterator[str]:
    """Yield raw Actor text chunks from the selected provider.

    usage_sink는 provider가 토큰 사용량을 보고할 때만 채워진다(현재 Gemini).
    """
    system_text = f"{fixed_prompt}\n\n{genre_prompt}" if genre_prompt else fixed_prompt
    if _is_gemini_model(model_name):
        async for text in _stream_gemini_text_chunks(
            system_text,
            dynamic_prompt,
            history,
            genai_client,
            model_name,
            max_token,
            usage_sink,
        ):
            yield text
        return
    if _is_claude_model(model_name):
        stream = await _open_claude_stream(
            _anthropic_system_blocks(fixed_prompt, genre_prompt),
            _anthropic_messages(dynamic_prompt, history),
            model_name,
            max_token,
        )
        async for text in stream_anthropic_text_chunks(stream, "Claude"):
            yield text
        return
    if _is_deepseek_model(model_name):
        stream = await _open_deepseek_stream(
            _anthropic_system_blocks(fixed_prompt, genre_prompt),
            _anthropic_messages(dynamic_prompt, history),
            model_name,
            max_token,
        )
        async for text in stream_anthropic_text_chunks(stream, "DeepSeek"):
            yield text
        return
    raise ValueError(f"Unsupported actor model: {model_name}")


async def _stream_actor_text_chunks_resilient(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    history: list[dict],
    genai_client: object,
    model_name: str,
    max_token: int,
    usage_sink: dict[str, int | None] | None = None,
) -> AsyncIterator[str]:
    """Actor 텍스트 청크를 스트리밍하되 첫 토큰 전 일시 오류는 전체 스트림을 재시도한다.

    재시도 대상(모두 첫 토큰 방출 전에만):
    - 네트워크 블립(_STREAM_TRANSIENT_ERRORS): 시도 순번에 비례한 짧은 백오프.
    - 403/429 provider 한도(주로 Gemini Actor): exponential backoff + jitter.
    아직 사용자에게 어떤 토큰도 방출하지 않은 상태에서만 재시도한다(재시도가 이미 보낸
    출력을 중복시키지 않도록). 한 번이라도 방출한 뒤 끊기면 그대로 전파해 호출자가
    부분 응답으로 마무리하거나 오류를 드러내게 한다.
    """
    last_error: BaseException | None = None
    network_attempts = 0
    rate_limit_attempts = 0
    while True:
        yielded_any = False
        retry_delay: float | None = None
        if usage_sink is not None:
            usage_sink.clear()
        try:
            async for text in _stream_actor_text_chunks(
                fixed_prompt=fixed_prompt,
                genre_prompt=genre_prompt,
                dynamic_prompt=dynamic_prompt,
                history=history,
                genai_client=genai_client,
                model_name=model_name,
                max_token=max_token,
                usage_sink=usage_sink,
            ):
                yielded_any = True
                yield text
            return
        except _STREAM_TRANSIENT_ERRORS as exc:
            # 이미 토큰을 보냈으면 재시도 불가(중복) → 전파해 부분 종료.
            if yielded_any:
                raise
            last_error = exc
            network_attempts += 1
            if network_attempts < _ACTOR_STREAM_MAX_ATTEMPTS:
                retry_delay = _ACTOR_STREAM_BACKOFF_SEC * network_attempts
                print(
                    f"[ActorStream] transient stream error before first token, "
                    f"retry {network_attempts}/{_ACTOR_STREAM_MAX_ATTEMPTS - 1} "
                    f"({type(exc).__name__}: {exc})"
                )
        except Exception as exc:
            # 403/429는 첫 토큰 전에만 재시도한다(스트리밍 중 한도 오류는 안전 재시작 불가).
            if not is_retryable_provider_limit(exc) or yielded_any:
                raise
            last_error = exc
            rate_limit_attempts += 1
            if rate_limit_attempts <= LLM_MAX_RETRIES_429:
                retry_delay = min(
                    2 ** rate_limit_attempts, _ACTOR_STREAM_RATE_LIMIT_CAP_SEC
                ) + random.uniform(0, 1)
                print(
                    f"[ActorStream limit:{model_name}] provider 403/429 "
                    f"retry {rate_limit_attempts}/{LLM_MAX_RETRIES_429} in {retry_delay:.1f}s"
                )
        # 재시도 예산이 없으면(retry_delay 미설정) 마지막 오류를 전파한다.
        if retry_delay is None:
            if last_error is not None:
                raise last_error
            return
        await asyncio.sleep(retry_delay)


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

    # 측정용: 웹 스트리밍 Actor 호출의 총 지연과 토큰 사용량을 기록한다
    # (이 경로는 generate_content_async를 우회하므로 계측도 여기서 직접 한다).
    start_epoch_ms = int(time.time() * 1000)
    started = perf_counter()
    status = "ok"
    got_text = False
    usage_sink: dict[str, int | None] = {}
    try:
        async for text in _stream_actor_text_chunks_resilient(
            fixed_prompt=fixed_prompt,
            genre_prompt=genre_prompt,
            dynamic_prompt=dynamic_prompt,
            history=history,
            genai_client=genai_client,
            model_name=model_name,
            max_token=max_token,
            usage_sink=usage_sink,
        ):
            got_text = True
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
    except _STREAM_TRANSIENT_ERRORS as exc:
        # 스트림 중간에 끊긴 경우: 부분 응답이라도 있으면 500 대신 우아하게 마무리한다
        # (deferred commit이라 pending으로 저장되어 사용자가 reroll/edit 가능).
        # 아무 텍스트도 못 받았으면 복구할 게 없으므로 그대로 전파한다.
        if not got_text:
            status = "error"
            raise
        status = "partial_stream_error"
        print(
            f"[ActorStream] stream interrupted after partial output, finalizing "
            f"({type(exc).__name__}: {exc})"
        )
    except Exception:
        status = "error"
        raise
    finally:
        record_llm_latency(
            "actor", model_name, start_epoch_ms,
            int((perf_counter() - started) * 1000), None, status,
            usage_sink or None,
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
