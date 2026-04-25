import json
import re
import anthropic

# 싱글톤 클라이언트
llm_client       = anthropic.Anthropic()       # 동기 전용 (actor_agent, ooc_parser 등)
async_llm_client = anthropic.AsyncAnthropic()  # async 함수 전용


def extract_json_from_llm(raw_text: str) -> dict:
    """
    Claude의 응답에서 JSON 부분만 안전하게 추출합니다.
    마크다운 찌꺼기 및 Trailing Comma 에러를 방지합니다.
    """
    try:
        clean = re.sub(r"```json|```", "", raw_text).strip()

        start_obj = clean.find('{')
        start_arr = clean.find('[')

        if start_obj == -1 and start_arr == -1:
            raise json.JSONDecodeError("No JSON structure found", clean, 0)

        if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
            start = start_obj
            end   = clean.rfind('}')
        else:
            start = start_arr
            end   = clean.rfind(']')

        if end == -1 or end < start:
            raise json.JSONDecodeError("Incomplete JSON structure", clean, 0)

        json_str = clean[start:end + 1]
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)

        parsed = json.loads(json_str)
        return parsed if isinstance(parsed, (dict, list)) else parsed

    except json.JSONDecodeError as e:
        print(f"[LLM Parser Error] 파싱 실패: {e}\nRaw Text: {raw_text[:150]}...")
        return {}