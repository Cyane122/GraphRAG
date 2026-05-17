# ================================
# src/core/state_normalization.py
#
# Normalizes state values returned by LLM extractors before DB writes.
#
# Functions
#   - normalize_stress_level(value: object) -> int | None : Convert stress labels/numeric strings to 0-10.
# ================================

import re


_STRESS_LABELS = {
    "none": 0,
    "no stress": 0,
    "n/a": 0,
    "na": 0,
    "null": 0,
    "zero": 0,
    "없음": 0,
    "없다": 0,
    "없어": 0,
    "무 stress": 0,
    "무": 0,
    "무스트레스": 0,
    "평온": 0,
    "안정": 0,
    "안정적": 0,
    "calm": 0,
    "peaceful": 0,
    "minimal": 1,
    "very mild": 1,
    "very low": 1,
    "아주 낮음": 1,
    "매우 낮음": 1,
    "거의 없음": 1,
    "미미함": 1,
    "최소": 1,
    "low": 2,
    "low stress": 2,
    "mild": 2,
    "light": 2,
    "slight": 2,
    "minor": 2,
    "낮음": 2,
    "낮다": 2,
    "낮은": 2,
    "약함": 2,
    "약한": 2,
    "약간": 2,
    "조금": 2,
    "경미": 2,
    "경미함": 2,
    "하": 2,
    "normal": 3,
    "manageable": 3,
    "보통 이하": 3,
    "관리 가능": 3,
    "moderate low": 4,
    "medium low": 4,
    "medium-low": 4,
    "중하": 4,
    "보통보다 낮음": 4,
    "medium": 5,
    "medium stress": 5,
    "mid": 5,
    "moderate": 5,
    "average": 5,
    "보통": 5,
    "중간": 5,
    "중": 5,
    "medium high": 6,
    "medium-high": 6,
    "moderate high": 6,
    "moderately high": 6,
    "중상": 6,
    "보통보다 높음": 6,
    "elevated": 7,
    "상당함": 7,
    "높은 편": 7,
    "high": 8,
    "high stress": 8,
    "severe": 8,
    "very severe": 9,
    "strong": 8,
    "stressed": 8,
    "높음": 8,
    "높다": 8,
    "심함": 8,
    "강함": 8,
    "상": 8,
    "very high": 9,
    "very stressed": 9,
    "extreme": 9,
    "extremely high": 9,
    "intense": 9,
    "매우 높음": 9,
    "아주 높음": 9,
    "매우 심함": 9,
    "극심": 9,
    "극심함": 9,
    "극단적": 9,
    "max": 10,
    "maximum": 10,
    "critical": 10,
    "overwhelming": 10,
    "최대": 10,
    "최고": 10,
    "최상": 10,
    "한계": 10,
    "압도적": 10,
}
_STRESS_NUMBER_RE = re.compile(
    r"^(?:stress\s*)?(?:level\s*)?(10|[0-9])\s*(?:/10|out of 10|점|단계)?$",
    re.IGNORECASE,
)


def normalize_stress_level(value: object) -> int | None:
    """Convert common stress labels and numeric strings to an integer from 0 to 10."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value <= 10:
        return value
    if isinstance(value, float) and value.is_integer() and 0 <= value <= 10:
        return int(value)
    if not isinstance(value, str):
        return None

    text = value.strip()
    try:
        number = int(text)
    except ValueError:
        normalized = re.sub(r"[\s_-]+", " ", text.lower()).strip()
        mapped = _STRESS_LABELS.get(normalized)
        if mapped is not None:
            return mapped
        match = _STRESS_NUMBER_RE.match(normalized)
        return int(match.group(1)) if match else None
    return number if 0 <= number <= 10 else None
