# ================================
# src/core/llm/client.py
#
# Gemini LLM 클라이언트 및 유틸리티를 제공합니다.
#
# Classes
#   - _GeminiModel : generate_content / generate_content_async 인터페이스 래퍼
#   - _SafeResponse : response.text가 항상 str을 반환하도록 보장하는 래퍼
#
# Functions
#   - get_client() -> genai.Client : 스트리밍 직접 호출 시 사용하는 클라이언트 반환
#   - get_model(model_name: str, system_prompt: str | None) -> _GeminiModel : 모델 래퍼 반환
#   - get_response_text(response) -> str : response.text가 None인 경우 parts에서 텍스트 추출
#   - log_empty_response_diagnostics(response: object, source: str) -> None : 빈 LLM 응답의 메타데이터를 출력
#   - extract_json_from_llm(raw_text, source: str, log_errors: bool) -> dict | list : LLM 응답에서 JSON 안전 추출
# ================================

import asyncio
import json
import re
from types import SimpleNamespace

from google import genai
from google.genai import types

from src.config import GOOGLE_CLOUD_LOCATION, GOOGLE_PROJECT_ID as PROJECT_ID

_LLM_TIMEOUT_SEC = 90  # 비스트리밍 JSON 호출 최대 대기 시간

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


def _usage_value(usage: object, attr: str) -> object:
    """Return one usage metadata value without exposing verbose SDK objects."""
    try:
        return getattr(usage, attr, None)
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
            chunk_usage = getattr(chunk, "usage_metadata", None)
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

    async def generate_content_async(self, contents, generation_config: dict | None = None):
        """
        비동기 LLM 응답을 반환한다.
        JSON mime 호출이 빈 텍스트를 반환하면 diagnostics를 남기고 streaming으로 재시도한다.
        """
        config_dict = generation_config or {}
        log_source = str(config_dict.get("log_source") or "json_async")
        config = self._build_config(generation_config)

        if config_dict.get("response_mime_type") == "application/json":
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
                return safe

            log_empty_response_diagnostics(resp, log_source)
            fallback_config = dict(config_dict)
            fallback_config.pop("log_source", None)
            fallback_config.pop("response_mime_type", None)
            fallback_config["thinking_config"] = {"thinking_budget": 0}
            current_budget = int(fallback_config.get("max_output_tokens") or 1024)
            fallback_config["max_output_tokens"] = max(2048, min(current_budget * 2, 8192))
            return await self._generate_content_stream_text(contents, fallback_config)

        return await self._generate_content_stream_text(contents, generation_config)


def get_model(model_name: str, system_prompt: str | None = None) -> _GeminiModel:
    """모델 이름과 시스템 프롬프트를 받아 _GeminiModel 래퍼를 반환한다."""
    return _GeminiModel(model_name=model_name, system_prompt=system_prompt)


# ════════════════════════════════════════════════════════════
# JSON 파서
# ════════════════════════════════════════════════════════════

def extract_json_from_llm(raw_text, source: str = "unknown", log_errors: bool = True) -> dict | list:
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
    """
    if not isinstance(raw_text, str):
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
