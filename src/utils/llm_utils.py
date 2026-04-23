# src/utils/llm_utils.py
import json
import re
import anthropic

# 싱글톤 Anthropic 클라이언트 (모든 파일에서 공유)
llm_client = anthropic.Anthropic()


def extract_json_from_llm(raw_text: str) -> dict:
    """
    Claude의 응답에서 JSON 부분만 안전하게 추출합니다.
    마크다운 찌꺼기 및 Trailing Comma 에러를 방지합니다.
    """
    try:
        # ```json 같은 마크다운 백틱 제거
        clean = re.sub(r"```json|```", "", raw_text).strip()

        start_obj = clean.find('{')
        start_arr = clean.find('[')

        # JSON 구조가 전혀 없는 경우
        if start_obj == -1 and start_arr == -1:
            raise json.JSONDecodeError("No JSON structure found", clean, 0)

        # 텍스트에서 먼저 등장하는 괄호를 기준으로 시작과 끝 설정
        if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
            start = start_obj
            end = clean.rfind('}')
        else:
            start = start_arr
            end = clean.rfind(']')

        # 닫는 괄호가 없거나 구조가 깨진 경우
        if end == -1 or end < start:
            raise json.JSONDecodeError("Incomplete JSON structure", clean, 0)

        json_str = clean[start:end + 1]

        # Haiku 모델 특유의 trailing comma (,[}]) 습관 방어
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)

        parsed = json.loads(json_str)

        # 반환값이 딕셔너리 또는 리스트인 경우 모두 허용
        return parsed if isinstance(parsed, (dict, list)) else parsed


    except json.JSONDecodeError as e:
        print(f"[LLM Parser Error] 파싱 실패: {e}\nRaw Text: {raw_text[:150]}...")
        return {}