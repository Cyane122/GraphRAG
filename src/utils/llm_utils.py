import json
import re
import anthropic

# 싱글톤 클라이언트
llm_client       = anthropic.Anthropic()       # 동기 전용 (actor_agent, ooc_parser 등)
async_llm_client = anthropic.AsyncAnthropic()  # async 함수 전용


def extract_json_from_llm(raw_text: str) -> dict | list:
    """
    LLM 응답에서 JSON을 안전하게 추출합니다.

    처리 순서:
    1. 마크다운 펜스 제거
    2. { } 또는 [ ] 범위 추출
    3. Trailing comma 제거
    4. 정상 파싱 시도
    5. 실패 시 잘린 JSON 복구 시도 (괄호 닫기 보정)
    6. 최종 실패 시 {} 반환
    """
    try:
        # 1. 펜스 및 공백 제거
        clean = re.sub(r"```(?:json)?\s*", "", raw_text)
        clean = clean.replace("```", "").strip()

        # 2. 시작 위치 탐색
        start_obj = clean.find('{')
        start_arr = clean.find('[')

        if start_obj == -1 and start_arr == -1:
            raise ValueError("No JSON structure found")

        # 객체 vs 배열 우선순위 결정
        if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
            start   = start_obj
            close   = '}'
            end     = clean.rfind('}')
        else:
            start   = start_arr
            close   = ']'
            end     = clean.rfind(']')

        # 3. 정상 범위 추출
        if end != -1 and end >= start:
            json_str = clean[start:end + 1]
            json_str = _fix_trailing_comma(json_str)
            try:
                parsed = json.loads(json_str)
                return parsed if isinstance(parsed, (dict, list)) else {}
            except json.JSONDecodeError:
                pass  # 복구 시도로 넘어감

        # 4. 잘린 JSON 복구 시도
        json_str = clean[start:].rstrip(', \n.')
        json_str = _fix_trailing_comma(json_str)
        open_braces   = json_str.count('{') - json_str.count('}')
        open_brackets = json_str.count('[') - json_str.count(']')
        json_str += ']' * max(0, open_brackets)
        json_str += '}' * max(0, open_braces)
        try:
            parsed = json.loads(json_str)
            return parsed if isinstance(parsed, (dict, list)) else {}
        except json.JSONDecodeError as e:
            raise e

    except Exception as e:
        print(f"[LLM Parser Error] 파싱 실패: {e}\nRaw Text: {raw_text[:200]}...")
        return {}


def _fix_trailing_comma(json_str: str) -> str:
    """JSON 마지막 쉼표(trailing comma) 제거."""
    return re.sub(r',\s*([}\]])', r'\1', json_str)