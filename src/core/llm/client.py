# ================================
# src/core/llm/client.py
#
# LLM 클라이언트 및 provider 유틸리티를 제공합니다.
#
# Classes
#   - _GeminiModel : generate_content / generate_content_async 인터페이스 래퍼
#   - _SafeResponse : response.text가 항상 str을 반환하도록 보장하는 래퍼
#
# Functions
#   - get_anthropic_client() -> AsyncAnthropic : direct Anthropic 클라이언트를 지연 생성해 반환
#   - get_anthropic_vertex_client() -> AsyncAnthropicVertex : Claude-on-Vertex 클라이언트를 지연 생성해 반환
#   - get_deepseek_client() -> AsyncAnthropic : DeepSeek Anthropic-compatible 클라이언트를 지연 생성해 반환
#   - stream_anthropic_text_chunks(stream: AsyncIterator[object], label: str) -> AsyncIterator[str] : Anthropic SSE 스트림에서 텍스트 청크를 추출하고 토큰 한도 절단을 경고
#   - usage_token_counts(usage: object) -> dict[str, int | None] : provider usage 객체에서 prompt/output/thought/total 토큰 수 추출
#   - record_llm_latency(log_source: str, model: str, start_epoch_ms: int, elapsed_ms: int, mime: str | None, status: str, tokens: dict[str, int | None] | None = None) -> None : LLM 호출 지연과 토큰 사용량을 logs/llm_latency.jsonl에 기록
#   - is_retryable_provider_limit(exc: BaseException) -> bool : 예외가 재시도 가능한 403/429 provider 한도 오류인지 판별(provider 공용, Actor 스트리밍도 공유)
#   - _run_generate_with_retries(generate_once: Callable[[], Awaitable[tuple[object, str]]], log_source: str, model_name: str, mime: str | None) -> object : 1회 생성 코루틴을 provider-무관하게 재시도/계측
#   - get_client() -> genai.Client : 스트리밍 직접 호출 시 사용하는 클라이언트 반환
#   - get_model(model_name: str, system_prompt: str | None) -> _GeminiModel : Gemini 래퍼 반환
#   - get_response_text(response) -> str : response.text가 None인 경우 parts에서 텍스트 추출
#   - log_empty_response_diagnostics(response: object, source: str) -> None : 빈 LLM 응답의 메타데이터를 출력
#   - extract_json_from_llm(raw_text, source: str, log_errors: bool, strict: bool) -> dict | list : LLM 응답에서 JSON 안전 추출 (strict=True면 실패 시 LLMJsonError)
# ================================

import asyncio
import json
import random
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from anthropic import APIStatusError, AsyncAnthropic, AsyncAnthropicVertex, RateLimitError
from google import genai
from google.genai import types
from google.genai.errors import APIError as GoogleAPIError

from src.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_VERTEX_REGION,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    GOOGLE_CLOUD_LOCATION,
    GOOGLE_PROJECT_ID as PROJECT_ID,
    LLM_MAX_CONCURRENCY,
    LLM_MAX_RETRIES_429,
)
from src.core.llm.errors import LLMJsonError, TransientLLMError

_LLM_TIMEOUT_SEC = 90  # 비스트리밍 JSON 호출 + 스트리밍 소비 최대 대기 시간
_LLM_MAX_ATTEMPTS = 2  # 타임아웃 시 재시도 포함 총 시도 횟수
_LLM_BACKOFF_SEC = 0.5  # 재시도 간 백오프 기준(시도 순번에 비례)
_LLM_RATE_LIMIT_CAP_SEC = 30  # 403/429 exponential backoff 상한(지터 별도 가산)

# 한 턴에 여러 후처리 updater가 병렬로 Vertex를 때리면 순간 버스트로 429가 난다.
# 이 세마포어로 동시에 진행 중인 비동기 LLM 호출 수를 제한한다. 백오프 대기 중에는
# 세마포어를 잡지 않도록, 실제 호출 구간(_generate_once)에서만 획득/반납한다.
_LLM_SEMAPHORE = asyncio.Semaphore(max(1, LLM_MAX_CONCURRENCY))


_anthropic_client: AsyncAnthropic | None = None
_anthropic_vertex_client: AsyncAnthropicVertex | None = None
_deepseek_client: AsyncAnthropic | None = None


def get_anthropic_client() -> AsyncAnthropic:
    """Return the module-level direct Anthropic client, creating it on first use."""
    global _anthropic_client
    if _anthropic_client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is required for Claude actor models.")
        _anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


def get_anthropic_vertex_client() -> AsyncAnthropicVertex:
    """Return the module-level Claude-on-Vertex client, creating it on first use.

    Vertex는 ADC(GOOGLE_APPLICATION_CREDENTIALS)로 인증한다 — 별도 키 불필요.
    """
    global _anthropic_vertex_client
    if _anthropic_vertex_client is None:
        if not PROJECT_ID:
            raise RuntimeError("GOOGLE_PROJECT_ID is required for Claude on Vertex AI.")
        _anthropic_vertex_client = AsyncAnthropicVertex(
            project_id=PROJECT_ID,
            region=ANTHROPIC_VERTEX_REGION,
        )
    return _anthropic_vertex_client


def get_deepseek_client() -> AsyncAnthropic:
    """Return the module-level DeepSeek Anthropic-compatible client."""
    global _deepseek_client
    if _deepseek_client is None:
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY is required for DeepSeek actor models.")
        _deepseek_client = AsyncAnthropic(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
    return _deepseek_client


async def stream_anthropic_text_chunks(
    stream: AsyncIterator[object],
    label: str,
) -> AsyncIterator[str]:
    """Anthropic 호환 SSE 스트림에서 텍스트 청크만 뽑아 순서대로 내보낸다.

    Claude와 DeepSeek은 같은 Anthropic 이벤트 스키마를 쓰므로 소비 코드도 하나다.
    provider 차이는 스트림을 여는 messages.create kwargs에만 남는다.
    label은 로그 문구용 provider 이름이다.
    토큰 한도로 응답이 잘렸으면 조용히 넘기지 않고 경고를 남긴다(silent truncation 방지).
    """
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

    if stop_reason == "max_tokens":
        print(f"[ActorStream] {label} 응답이 토큰 한도로 잘렸습니다 (stop_reason={stop_reason}).")


_RETRYABLE_LIMIT_STATUS = (403, 429)


def is_retryable_provider_limit(exc: BaseException) -> bool:
    """예외가 재시도 가능한 provider 한도 오류(403/429)인지 판별한다.

    403(빌링 비활성·권한 거부)과 429(요청량 초과)를 함께 재시도 대상으로 본다.
    판별은 예외 타입과 상태 코드로 구조적으로 한다 — Anthropic 호환 클라이언트는
    RateLimitError 또는 APIStatusError.status_code를, Vertex(google-genai)는
    APIError.code를 준다. 응답 본문에 우연히 "429"가 섞여도 오탐하지 않도록,
    문자열 검사는 구조적 코드가 전혀 없는 예외에 대한 최후 수단으로만 남긴다.
    Actor 스트리밍 경로와 비스트리밍 재시도 루프가 같은 함수를 공유한다.
    """
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in _RETRYABLE_LIMIT_STATUS
    if isinstance(exc, GoogleAPIError):
        return getattr(exc, "code", None) in _RETRYABLE_LIMIT_STATUS
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code is not None:
        return code in _RETRYABLE_LIMIT_STATUS
    return "RESOURCE_EXHAUSTED" in str(exc).upper()

# 측정용: 모든 비동기 LLM 호출 1건의 지연을 한 줄씩 누적한다(Phase 2 병목 분석).
_LLM_LATENCY_LOG = Path("logs") / "llm_latency.jsonl"


def usage_token_counts(usage: object) -> dict[str, int | None]:
    """usage_metadata 유사 객체에서 prompt/output/thought/total 토큰 수를 추출한다.

    provider 마다 필드명이 달라 Gemini(`*_token_count`)와 Anthropic 호환
    (`input_tokens`/`output_tokens`)을 함께 본다. 값이 없으면 None을 남긴다.
    """
    return {
        "prompt_tokens": _usage_value(usage, "prompt_token_count", "input_tokens"),
        "output_tokens": _usage_value(usage, "candidates_token_count", "output_tokens"),
        "thought_tokens": _usage_value(usage, "thoughts_token_count"),
        "total_tokens": _usage_value(usage, "total_token_count"),
    }


def record_llm_latency(
    log_source: str,
    model: str,
    start_epoch_ms: int,
    elapsed_ms: int,
    mime: str | None,
    status: str,
    tokens: dict[str, int | None] | None = None,
) -> None:
    """LLM 호출 1건의 지연을 logs/llm_latency.jsonl에 한 줄로 남긴다. 측정 전용, 실패는 무시.

    start_epoch_ms(시작 시각) + elapsed_ms 로 호출 구간이 겹치면 병렬, 안 겹치면 순차임을
    사후 분석할 수 있다. log_source 별로 묶으면 호출 종류별 지연 분포를 볼 수 있다.
    tokens가 있으면 prompt/output/thought/total 사용량을 같은 줄에 덧붙여 장기 플레이
    비용 집계에 쓴다(provider가 보고하지 않으면 값은 None).
    """
    try:
        _LLM_LATENCY_LOG.parent.mkdir(exist_ok=True)
        payload = {
            "ts": start_epoch_ms,
            "log_source": log_source,
            "model": model,
            "elapsed_ms": elapsed_ms,
            "mime": mime,
            "status": status,
        }
        if tokens:
            payload.update(tokens)
        line = json.dumps(payload, ensure_ascii=False)
        with _LLM_LATENCY_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass

# sexual_information 등 성인 콘텐츠를 포함하는 업데이터 호출용 safety bypass
_CONTENT_OFF_SAFETY_SETTINGS = [
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
]

_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=GOOGLE_CLOUD_LOCATION,
)


def get_client() -> genai.Client:
    """app.py 등에서 스트리밍 직접 호출 시 사용."""
    return _client


class _SafeResponse:
    """
    Gemini 응답을 래핑해 .text가 항상 str을 반환하도록 보장.
    response.text가 None인 경우 candidates.parts에서 텍스트를 수집한다.
    """
    def __init__(self, response):
        self._r = response

    @property
    def text(self) -> str:
        return get_response_text(self._r)

    def __getattr__(self, name):
        return getattr(self._r, name)


# ════════════════════════════════════════════════════════════
# 응답 텍스트 안전 추출
# ════════════════════════════════════════════════════════════

def get_response_text(response) -> str:
    """
    response.text가 None인 경우(thinking 전용 반환, 콘텐츠 차단 등)
    candidates → parts를 순회해 사용자에게 보여줄 text 파트를 직접 수집한다.
    모든 경로에서 실패하면 빈 문자열을 반환한다.
    """
    if response is None:
        return ""

    try:
        parts = response.candidates[0].content.parts
        texts = [
            p.text
            for p in parts
            if getattr(p, "text", None) is not None
            and not (getattr(p, "thought", False) or getattr(p, "is_thought", False))
        ]
        return "".join(texts)
    except Exception:
        pass

    try:
        text = getattr(response, "text", None)
    except Exception:
        text = None
    if text is not None:
        return text

    return ""


def _safe_primitive(value: object) -> object:
    """로그에 안전하게 남길 수 있는 짧은 원시값으로 변환합니다."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_primitive(item) for item in value[:8]]
    if isinstance(value, dict):
        return {str(key): _safe_primitive(item) for key, item in list(value.items())[:20]}
    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__}>"


def _response_diagnostics(response: object) -> dict:
    """Gemini 응답 객체에서 빈 응답 원인 추적에 필요한 메타데이터를 추출합니다."""
    info: dict = {
        "response_type": type(response).__name__ if response is not None else None,
        "candidates": [],
        "prompt_feedback": None,
        "usage_metadata": None,
    }
    if response is None:
        return info

    for attr in ("prompt_feedback", "usage_metadata"):
        try:
            info[attr] = _safe_primitive(getattr(response, attr, None))
        except Exception as exc:
            info[attr] = f"<unavailable: {exc}>"

    try:
        candidates = getattr(response, "candidates", None) or []
    except Exception:
        candidates = []

    for candidate in candidates[:3]:
        candidate_info: dict = {}
        for attr in ("finish_reason", "finish_message", "safety_ratings", "citation_metadata"):
            try:
                candidate_info[attr] = _safe_primitive(getattr(candidate, attr, None))
            except Exception as exc:
                candidate_info[attr] = f"<unavailable: {exc}>"
        try:
            parts = getattr(getattr(candidate, "content", None), "parts", None) or []
            candidate_info["parts"] = [
                {
                    "has_text": bool(getattr(part, "text", None)),
                    "text_len": len(getattr(part, "text", "") or ""),
                    "thought": bool(getattr(part, "thought", False) or getattr(part, "is_thought", False)),
                    "part_type": type(part).__name__,
                }
                for part in parts[:8]
            ]
        except Exception as exc:
            candidate_info["parts"] = f"<unavailable: {exc}>"
        info["candidates"].append(candidate_info)
    return info


def _usage_value(usage: object, *attrs: str) -> int | None:
    """Return the first present usage metadata value coerced to a plain int.

    provider 별 필드명을 순서대로 시도하고 전부 없으면 None을 반환한다.
    값이 JSON 직렬화에 안전한 int로 변환되지 않으면 None으로 버린다.
    """
    for attr in attrs:
        try:
            value = getattr(usage, attr, None)
        except Exception:
            value = None
        if value is None:
            continue
        try:
            return int(value)
        except Exception:
            return None
    return None


def _safe_usage_metadata(owner: object) -> object | None:
    """Return usage_metadata when available without letting provider quirks raise."""
    try:
        return getattr(owner, "usage_metadata", None)
    except Exception:
        return None


def _compact_empty_response_diagnostics(response: object) -> dict:
    """Return a short empty-response diagnostic suitable for normal logs."""
    candidates = []
    try:
        raw_candidates = getattr(response, "candidates", None) or []
    except Exception:
        raw_candidates = []
    for candidate in raw_candidates[:2]:
        try:
            parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        except Exception:
            parts = []
        candidates.append({
            "finish_reason": str(getattr(candidate, "finish_reason", None)),
            "finish_message": getattr(candidate, "finish_message", None),
            "parts": len(parts),
        })

    try:
        usage = getattr(response, "usage_metadata", None)
    except Exception:
        usage = None

    return {
        "candidates": candidates,
        "prompt_tokens": _usage_value(usage, "prompt_token_count"),
        "output_tokens": _usage_value(usage, "candidates_token_count"),
        "thought_tokens": _usage_value(usage, "thoughts_token_count"),
        "total_tokens": _usage_value(usage, "total_token_count"),
    }


def log_empty_response_diagnostics(response: object, source: str) -> None:
    """빈 LLM 응답의 finish_reason/safety/parts 메타데이터를 로그로 남깁니다."""
    print(
        f"[LLM Empty Response:{source}] "
        f"{json.dumps(_compact_empty_response_diagnostics(response), ensure_ascii=False, default=str)}"
    )


# ════════════════════════════════════════════════════════════
# 모델 래퍼
# ════════════════════════════════════════════════════════════

class _GeminiModel:
    """
    genai.Client.models.generate_content /
    client.aio.models.generate_content 를
    model.generate_content(contents, generation_config) 인터페이스로 감싼다.

    generation_config 딕셔너리에서:
    - thinking_config: {"thinking_level": "LOW"|"MEDIUM"|"HIGH", "thinking_budget": int, "include_thoughts": bool}
      → types.ThinkingConfig으로 변환
    - 나머지 키(max_output_tokens, temperature, response_mime_type 등)는 그대로 전달
    """

    def __init__(self, model_name: str, system_prompt: str | None) -> None:
        self._model  = model_name
        self._system = system_prompt

    def _build_config(self, generation_config: dict | None) -> types.GenerateContentConfig:
        """generation_config dict를 Gemini GenerateContentConfig로 변환한다."""
        cfg = dict(generation_config or {})
        cfg.pop("log_source", None)
        thinking_raw = cfg.pop("thinking_config", None)
        bypass_safety = cfg.pop("bypass_safety", False)
        is_json_response = cfg.get("response_mime_type") == "application/json"

        # JSON 분류/업데이트 호출은 짧은 구조화 출력이 목적이라 thinking 토큰이
        # max_output_tokens를 잠식하면 빈 JSON 응답으로 끝날 수 있다.
        # 일부 모델은 thinking_budget=0을 무시하고 최소 ~500 thinking 토큰을 사용하므로
        # max_output_tokens가 없는 JSON 호출에는 안전 하한을 적용한다.
        # 호출자가 thinking_level 또는 thinking_budget을 명시한 경우 해당 값을 존중한다.
        caller_set_thinking = thinking_raw is not None and (
            "thinking_level" in thinking_raw or "thinking_budget" in thinking_raw
        )
        if thinking_raw is None:
            thinking_raw = {"thinking_budget": 0} if is_json_response else {"thinking_level": "LOW"}
        elif is_json_response and not caller_set_thinking:
            thinking_raw = {"thinking_budget": 0}

        if is_json_response and not cfg.get("max_output_tokens"):
            cfg["max_output_tokens"] = 4096

        build: dict = {}
        if self._system:
            build["system_instruction"] = self._system
        thinking_args: dict = {}
        if "thinking_level" in thinking_raw:
            thinking_args["thinking_level"] = thinking_raw.get("thinking_level")
        if "thinking_budget" in thinking_raw:
            thinking_args["thinking_budget"] = thinking_raw.get("thinking_budget")
        if "include_thoughts" in thinking_raw:
            thinking_args["include_thoughts"] = thinking_raw.get("include_thoughts")
        build["thinking_config"] = types.ThinkingConfig(**thinking_args)
        build["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
            disable=True
        )
        if bypass_safety:
            build["safety_settings"] = _CONTENT_OFF_SAFETY_SETTINGS
        build.update(cfg)
        return types.GenerateContentConfig(**build)

    def generate_content(self, contents, generation_config: dict | None = None):
        resp = _client.models.generate_content(
            model=self._model,
            contents=contents,
            config=self._build_config(generation_config),
        )
        return _SafeResponse(resp)

    async def _generate_content_stream_text(
        self,
        contents: object,
        generation_config: dict | None = None,
    ) -> _SafeResponse:
        """Streaming 호출로 non-thought 텍스트 파트를 수집한 응답 래퍼를 반환한다."""
        config = self._build_config(generation_config)
        text_parts: list[str] = []
        usage_metadata = None
        async for chunk in await _client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=config,
        ):
            chunk_usage = _safe_usage_metadata(chunk)
            if chunk_usage is not None:
                usage_metadata = chunk_usage
            if not chunk.candidates:
                continue
            candidate = chunk.candidates[0]
            if not candidate or not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                is_thought = getattr(part, "thought", False) or getattr(part, "is_thought", False)
                if not is_thought and part.text:
                    text_parts.append(part.text)

        return _SafeResponse(
            SimpleNamespace(
                text="".join(text_parts),
                usage_metadata=usage_metadata,
            )
        )

    async def _generate_once(
        self,
        contents,
        generation_config: dict | None,
        config_dict: dict,
        mime: str | None,
        log_source: str,
    ) -> tuple[_SafeResponse, str]:
        """LLM 응답 한 번을 받아 (응답, 지연 로그용 status) 튜플로 반환한다.

        JSON mime 호출이 빈 텍스트면 diagnostics를 남기고 streaming으로 폴백한다.
        비스트리밍 JSON 호출과 streaming 소비 모두 타임아웃을 건다(무한 대기 방지).
        동시 호출 수는 _LLM_SEMAPHORE로 제한해 순간 버스트에 의한 429를 줄인다.
        """
        config = self._build_config(generation_config)
        async with _LLM_SEMAPHORE:
            if mime == "application/json":
                resp = await asyncio.wait_for(
                    _client.aio.models.generate_content(
                        model=self._model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=_LLM_TIMEOUT_SEC,
                )
                safe = _SafeResponse(resp)
                if safe.text.strip():
                    return safe, "ok"

                log_empty_response_diagnostics(resp, log_source)
                fallback_config = dict(config_dict)
                fallback_config.pop("log_source", None)
                fallback_config.pop("response_mime_type", None)
                fallback_config["thinking_config"] = {"thinking_budget": 0}
                current_budget = int(fallback_config.get("max_output_tokens") or 1024)
                fallback_config["max_output_tokens"] = max(2048, min(current_budget * 2, 8192))
                fallback = await asyncio.wait_for(
                    self._generate_content_stream_text(contents, fallback_config),
                    timeout=_LLM_TIMEOUT_SEC,
                )
                return fallback, "empty_fallback_stream"

            streamed = await asyncio.wait_for(
                self._generate_content_stream_text(contents, generation_config),
                timeout=_LLM_TIMEOUT_SEC,
            )
            return streamed, "ok"

    async def generate_content_async(self, contents, generation_config: dict | None = None):
        """비동기 Gemini 응답을 반환한다(타임아웃/429 재시도는 공용 헬퍼가 담당)."""
        config_dict = generation_config or {}
        log_source = str(config_dict.get("log_source") or "unlabeled")
        mime = config_dict.get("response_mime_type")

        async def _once() -> tuple[_SafeResponse, str]:
            """이 모델의 1회 생성 시도를 공용 재시도 루프에 넘길 형태로 감싼다."""
            return await self._generate_once(
                contents, generation_config, config_dict, mime, log_source
            )

        return await _run_generate_with_retries(_once, log_source, self._model, mime)


async def _run_generate_with_retries(
    generate_once: Callable[[], Awaitable[tuple[object, str]]],
    log_source: str,
    model_name: str,
    mime: str | None,
) -> object:
    """1회 생성 코루틴(generate_once)을 provider-무관하게 재시도/계측한다.

    두 종류의 일시 오류를 재시도한다:
    - 타임아웃: 시도 순번에 비례한 짧은 백오프, 최대 _LLM_MAX_ATTEMPTS회.
    - 403/429 provider 한도: exponential backoff + jitter, 최대 LLM_MAX_RETRIES_429회.
    재시도 소진 시 TransientLLMError를 던진다(호출처가 '빈 응답'과 구분 가능).
    백오프 sleep은 세마포어 밖에서 이뤄지고 지연 로그에도 포함되지 않는다.

    generate_once는 인자 없는 async 콜러블로, (응답, 지연 로그용 status)를 반환해야 한다.
    """
    last_error: BaseException | None = None
    timeout_attempts = 0
    rate_limit_attempts = 0
    status = "ok"
    while True:
        # 측정용 타이밍: 시도 1건의 총 지연을 status와 함께 기록한다.
        start_epoch_ms = int(time.time() * 1000)
        started = perf_counter()
        status = "ok"
        retry_delay: float | None = None
        tokens: dict[str, int | None] | None = None
        try:
            result, status = await generate_once()
            tokens = usage_token_counts(_safe_usage_metadata(result))
            return result
        except (asyncio.TimeoutError, TimeoutError) as exc:
            status = "timeout"
            last_error = exc
            timeout_attempts += 1
            if timeout_attempts < _LLM_MAX_ATTEMPTS:
                retry_delay = _LLM_BACKOFF_SEC * timeout_attempts
        except Exception as exc:
            if not is_retryable_provider_limit(exc):
                status = "error"
                raise
            status = "rate_limit"
            last_error = exc
            rate_limit_attempts += 1
            if rate_limit_attempts <= LLM_MAX_RETRIES_429:
                # exponential backoff(2^n) + full jitter, 상한 _LLM_RATE_LIMIT_CAP_SEC.
                retry_delay = min(
                    2 ** rate_limit_attempts, _LLM_RATE_LIMIT_CAP_SEC
                ) + random.uniform(0, 1)
                print(
                    f"[LLM limit:{log_source}] provider 403/429 "
                    f"retry {rate_limit_attempts}/{LLM_MAX_RETRIES_429} "
                    f"in {retry_delay:.1f}s (model={model_name})"
                )
            else:
                print(
                    f"[LLM limit:{log_source}] gave up after "
                    f"{LLM_MAX_RETRIES_429} retries (model={model_name})"
                )
        finally:
            record_llm_latency(
                log_source,
                model_name,
                start_epoch_ms,
                int((perf_counter() - started) * 1000),
                mime,
                status,
                tokens,
            )
        # 재시도 예산이 남아 있으면 세마포어를 놓은 상태로 백오프 후 다시 시도한다.
        if retry_delay is None:
            break
        await asyncio.sleep(retry_delay)

    raise TransientLLMError(
        f"LLM call failed after retries "
        f"(source={log_source}, model={model_name}, last_status={status})"
    ) from last_error


def get_model(
    model_name: str, system_prompt: str | None = None
) -> "_GeminiModel":
    """주어진 모델 이름으로 Gemini 래퍼를 반환한다."""
    return _GeminiModel(model_name=model_name, system_prompt=system_prompt)


# ════════════════════════════════════════════════════════════
# JSON 파서
# ════════════════════════════════════════════════════════════

def extract_json_from_llm(
    raw_text,
    source: str = "unknown",
    log_errors: bool = True,
    strict: bool = False,
) -> dict | list:
    """
    LLM 응답에서 JSON을 안전하게 추출.

    처리 순서:
    1. None / 비문자열 입력 guard
    2. 마크다운 펜스 제거
    3. { } 또는 [ ] 범위 추출
    4. Trailing comma 제거
    5. 정상 파싱 시도
    6. 실패 시 잘린 JSON 복구 시도 (괄호 닫기 보정)
    7. 최종 실패 시 {} 반환. log_errors=False면 실패 로그를 생략한다.

    strict=True이면 추출 실패 시 {} 대신 LLMJsonError를 던진다 — 호출처가 '추출 실패'와
    '정상적인 빈 결과'를 구분해야 할 때 사용한다(기본 False로 기존 동작 유지).
    """
    if not isinstance(raw_text, str):
        if strict:
            raise LLMJsonError(f"{source}: 입력이 문자열이 아님 ({type(raw_text).__name__})")
        print(f"[LLM Parser:{source}] 입력이 문자열이 아님: {type(raw_text)} → {{}} 반환")
        return {}

    try:
        clean = re.sub(r"```(?:json)?\s*", "", raw_text)
        clean = clean.replace("```", "").strip()

        parsed = _parse_json_candidate(clean)
        if parsed is not None:
            return parsed

        for json_str in _iter_json_candidates(clean):
            parsed = _parse_json_candidate(json_str)
            if parsed is not None:
                return parsed

        raise ValueError("No JSON structure found")

    except Exception as e:
        preview_limit = 1000
        if raw_text:
            preview = raw_text[:preview_limit]
            suffix = "... [log truncated]" if len(raw_text) > preview_limit else ""
        else:
            preview = "(empty)"
            suffix = ""
        if log_errors:
            print(f"[LLM Parser Error:{source}] 파싱 실패: {e}\nRaw Text: {preview}{suffix}")
        if strict:
            raise LLMJsonError(f"{source}: {e}") from e
        return {}


def _parse_json_candidate(json_str: str) -> dict | list | None:
    """JSON 후보 문자열을 보정한 뒤 dict/list로 파싱한다."""
    if not json_str:
        return None

    for candidate in (
        json_str,
        _fix_trailing_comma(json_str),
        _fix_unterminated_string(_fix_trailing_comma(json_str)),
    ):
        try:
            parsed = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def _iter_json_candidates(clean: str) -> list[str]:
    """응답 문자열 안에서 실제로 파싱 가능한 JSON 후보들을 앞에서부터 만든다."""
    decoder = json.JSONDecoder()
    candidates: list[str] = []

    for idx, ch in enumerate(clean):
        if ch not in "{[":
            continue
        try:
            _, end = decoder.raw_decode(clean[idx:])
            candidates.append(clean[idx:idx + end])
            continue
        except json.JSONDecodeError:
            pass

        close_ch = "}" if ch == "{" else "]"
        end = clean.rfind(close_ch)
        if end >= idx:
            candidates.append(clean[idx:end + 1])

    return candidates


def _fix_trailing_comma(json_str: str) -> str:
    """JSON trailing comma 제거."""
    return re.sub(r',\s*([}\]])', r'\1', json_str)


def _fix_unterminated_string(json_str: str) -> str:
    """
    Unterminated string 복구.
    모델이 문자열 중간에 잘라버린 경우 닫는 따옴표 + 괄호를 LIFO 순서로 추가한다.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in json_str:
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch in '{[':
                stack.append(ch)
            elif ch == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif ch == ']' and stack and stack[-1] == '[':
                stack.pop()

    closers = '"' if in_string else ''
    for opener in reversed(stack):
        closers += '}' if opener == '{' else ']'
    return json_str.rstrip() + closers
