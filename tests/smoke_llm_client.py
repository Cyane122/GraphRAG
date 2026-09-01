# ================================
# tests/smoke_llm_client.py
#
# core/llm 회복력(JSON strict 파싱, 타임아웃 재시도/백오프)을 네트워크 없이 검증하는 smoke 검사.
#
# Functions
#   - _check_extract_json() -> None : strict/비strict 모드의 JSON 추출 동작을 검증.
#   - _check_retry() -> None : 타임아웃 1회 후 재시도 성공 / 모두 실패 시 TransientLLMError 검증.
#   - main() -> None : 전체 smoke 검사를 실행.
# ================================

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.core.llm.client as client  # noqa: E402
from src.core.llm.errors import LLMJsonError, TransientLLMError  # noqa: E402


def _check_extract_json() -> None:
    """strict=False는 실패 시 {}를, strict=True는 LLMJsonError를 내고, 정상 입력은 파싱된다."""
    # 정상 입력은 dict로 파싱된다(펜스 포함).
    assert client.extract_json_from_llm('```json\n{"a": 1}\n```', source="t") == {"a": 1}

    # 비strict: 깨진 입력 → {} (기존 계약 유지).
    assert client.extract_json_from_llm("not json at all", source="t", log_errors=False) == {}
    assert client.extract_json_from_llm(None, source="t") == {}

    # strict: 깨진 입력 → LLMJsonError.
    for bad in ("not json at all", None):
        try:
            client.extract_json_from_llm(bad, source="t", log_errors=False, strict=True)
        except LLMJsonError:
            pass
        else:
            raise AssertionError(f"strict 모드가 {bad!r}에 대해 예외를 내지 않았다")


async def _check_retry() -> None:
    """타임아웃을 일시 오류로 보고 재시도하며, 모두 실패하면 TransientLLMError(=TimeoutError)를 던진다."""
    model = client.get_model("dummy-model")

    # 1) 첫 시도 타임아웃 → 두 번째 시도 성공 시 응답을 반환한다.
    calls = {"n": 0}
    sentinel = object()

    async def flaky(contents, generation_config, config_dict, mime, log_source):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("first attempt times out")
        return sentinel, "ok"

    model._generate_once = flaky
    result = await model.generate_content_async("hi", {"log_source": "t"})
    assert result is sentinel and calls["n"] == 2, calls

    # 2) 모든 시도 타임아웃 → TransientLLMError, 그리고 기존 except TimeoutError가 포착 가능.
    async def always_timeout(contents, generation_config, config_dict, mime, log_source):
        raise TimeoutError("always")

    model._generate_once = always_timeout
    try:
        await model.generate_content_async("hi", {"log_source": "t"})
    except TransientLLMError as exc:
        assert isinstance(exc, TimeoutError), "TransientLLMError는 TimeoutError로도 포착돼야 한다"
    else:
        raise AssertionError("재시도 소진 후 TransientLLMError가 발생하지 않았다")


def main() -> None:
    """core/llm 회복력 smoke 검사를 실행한다."""
    _check_extract_json()
    asyncio.run(_check_retry())
    print("smoke_llm_client: ok")


if __name__ == "__main__":
    main()
