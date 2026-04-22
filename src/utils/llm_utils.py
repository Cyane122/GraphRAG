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

        start = clean.find('{')
        end = clean.rfind('}')
        if start == -1 or end == -1:
            raise json.JSONDecodeError("No JSON object found", clean, 0)

        json_str = clean[start:end + 1]

        # Haiku 모델 특유의 trailing comma(,[}]) 습관 방어
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)

        parsed = json.loads(json_str)
        return parsed if isinstance(parsed, dict) else {}

    except json.JSONDecodeError as e:
        print(f"[LLM Parser Error] 파싱 실패: {e}\nRaw Text: {raw_text[:150]}...")
        return {}