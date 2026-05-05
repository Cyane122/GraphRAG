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
#   - extract_json_from_llm(raw_text, source: str) -> dict | list : LLM 응답에서 JSON 안전 추출
# ================================

import json
import os
import re

from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID")

_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="global",
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
    candidates → parts를 순회해 text 파트를 직접 수집한다.
    모든 경로에서 실패하면 빈 문자열을 반환한다.
    """
    if response is None:
        return ""
    text = getattr(response, "text", None)
    if text is not None:
        return text
    try:
        parts = response.candidates[0].content.parts
        texts = [p.text for p in parts if getattr(p, "text", None) is not None]
        return "".join(texts)
    except Exception:
        return ""


# ════════════════════════════════════════════════════════════
# 모델 래퍼
# ════════════════════════════════════════════════════════════

class _GeminiModel:
    """
    genai.Client.models.generate_content /
    client.aio.models.generate_content 를
    model.generate_content(contents, generation_config) 인터페이스로 감싼다.

    generation_config 딕셔너리에서:
    - thinking_config: {"thinking_level": "LOW"|"MEDIUM"|"HIGH"}
      → types.ThinkingConfig으로 변환
    - 나머지 키(max_output_tokens, temperature, response_mime_type 등)는 그대로 전달
    """

    def __init__(self, model_name: str, system_prompt: str | None) -> None:
        self._model  = model_name
        self._system = system_prompt

    def _build_config(self, generation_config: dict | None) -> types.GenerateContentConfig:
        cfg = dict(generation_config or {})
        thinking_raw = cfg.pop("thinking_config", None)

        # thinking 미지정 → LOW 강제 (MINIMAL은 gemini-3-flash-preview 미지원)
        if thinking_raw is None:
            thinking_raw = {"thinking_level": "LOW"}

        build: dict = {}
        if self._system:
            build["system_instruction"] = self._system
        build["thinking_config"] = types.ThinkingConfig(
            thinking_level=thinking_raw.get("thinking_level", "LOW")
        )
        build["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
            disable=True
        )
        build.update(cfg)
        return types.GenerateContentConfig(**build)

    def generate_content(self, contents, generation_config: dict | None = None):
        resp = _client.models.generate_content(
            model=self._model,
            contents=contents,
            config=self._build_config(generation_config),
        )
        return _SafeResponse(resp)

    async def generate_content_async(self, contents, generation_config: dict | None = None):
        """
        내부적으로 streaming을 사용해 텍스트를 수집한다.
        non-streaming은 thinking 모델에서 텍스트 파트를 누락하는 버그가 있어서
        streaming으로 통일한다.
        """
        config_dict = generation_config or {}
        config = self._build_config(generation_config)

        if config_dict.get("response_mime_type") == "application/json":
            resp = await _client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
            return _SafeResponse(resp)

        text_parts: list[str] = []
        async for chunk in await _client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=config,
        ):
            if not chunk.candidates:
                continue
            candidate = chunk.candidates[0]
            if not candidate or not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                is_thought = getattr(part, "thought", False) or getattr(part, "is_thought", False)
                if not is_thought and part.text:
                    text_parts.append(part.text)

        class _StreamedResponse:
            text = "".join(text_parts)

        return _SafeResponse(_StreamedResponse())


def get_model(model_name: str, system_prompt: str | None = None) -> _GeminiModel:
    """모델 이름과 시스템 프롬프트를 받아 _GeminiModel 래퍼를 반환한다."""
    return _GeminiModel(model_name=model_name, system_prompt=system_prompt)


# ════════════════════════════════════════════════════════════
# JSON 파서
# ════════════════════════════════════════════════════════════

def extract_json_from_llm(raw_text, source: str = "unknown") -> dict | list:
    """
    LLM 응답에서 JSON을 안전하게 추출.

    처리 순서:
    1. None / 비문자열 입력 guard
    2. 마크다운 펜스 제거
    3. { } 또는 [ ] 범위 추출
    4. Trailing comma 제거
    5. 정상 파싱 시도
    6. 실패 시 잘린 JSON 복구 시도 (괄호 닫기 보정)
    7. 최종 실패 시 {} 반환
    """
    if not isinstance(raw_text, str):
        print(f"[LLM Parser:{source}] 입력이 문자열이 아님: {type(raw_text)} → {{}} 반환")
        return {}

    try:
        clean = re.sub(r"```(?:json)?\s*", "", raw_text)
        clean = clean.replace("```", "").strip()

        start_obj = clean.find('{')
        start_arr = clean.find('[')

        if start_obj == -1 and start_arr == -1:
            raise ValueError("No JSON structure found")

        if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
            start = start_obj
            end   = clean.rfind('}')
        else:
            start = start_arr
            end   = clean.rfind(']')

        if end != -1 and end >= start:
            json_str = _fix_trailing_comma(clean[start:end + 1])
            try:
                parsed = json.loads(json_str)
                return parsed if isinstance(parsed, (dict, list)) else {}
            except json.JSONDecodeError:
                json_str = _fix_unterminated_string(json_str)
                parsed = json.loads(json_str)
                return parsed if isinstance(parsed, (dict, list)) else {}

        json_str      = _fix_trailing_comma(clean[start:].rstrip(', \n.'))
        open_braces   = json_str.count('{') - json_str.count('}')
        open_brackets = json_str.count('[') - json_str.count(']')
        json_str     += ']' * max(0, open_brackets)
        json_str     += '}' * max(0, open_braces)

        parsed = json.loads(json_str)
        return parsed if isinstance(parsed, (dict, list)) else {}

    except Exception as e:
        preview = raw_text[:1000] if raw_text else "(empty)"
        print(f"[LLM Parser Error:{source}] 파싱 실패: {e}\nRaw Text: {preview}...")
        return {}


def _fix_trailing_comma(json_str: str) -> str:
    """JSON trailing comma 제거."""
    return re.sub(r',\s*([}\]])', r'\1', json_str)


def _fix_unterminated_string(json_str: str) -> str:
    """
    Unterminated string 복구.
    모델이 문자열 중간에 ...로 잘라버린 경우 닫는 따옴표 + 닫는 괄호를 추가한다.
    """
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

    if in_string:
        json_str = json_str.rstrip() + '"'

    open_braces = json_str.count('{') - json_str.count('}')
    open_brackets = json_str.count('[') - json_str.count(']')
    json_str += ']' * max(0, open_brackets)
    json_str += '}' * max(0, open_braces)
    return json_str
