# ================================
# src/simulation/systems/social/models.py
#
# 트랜션트 NPC 생성 시 LLM이 채우는 개연성 기본값의 검증 모델을 정의합니다.
#
# Classes
#   - StubProfile : 신규 트랜션트 NPC의 LLM 생성 정체성/물리/관계 기본값
# ================================
from pydantic import BaseModel, ConfigDict


class StubProfile(BaseModel):
    """
    새로 등장한 트랜션트 NPC에 대해 LLM이 생성한 개연성 기본값.

    LLM이 원문 토큰이 실제 이름인지 역할/호칭인지 먼저 분류하고, 역할/호칭이면
    실제 한국어 이름을 생성한다. 물리 필드는 biological_sex·age·build 와 내부적으로
    일관되도록 생성된다. LLM이 스키마 밖 키를 반환하더라도 무시(extra="ignore")한다.
    """

    # LLM이 age/height/weight 등을 숫자(JSON number)로 반환해도 문자열로 받아들인다.
    # 그렇지 않으면 ValidationError로 생성된 기본값 전체가 버려진다.
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    reference_kind:       str = ""
    source_token:         str = ""
    name_kor:             str = ""
    name_roman:           str = ""
    promotion_eligible:   bool | None = None
    biological_sex:       str = ""
    age:                  str = ""
    height:               str = ""
    weight:               str = ""
    measurements:         str = ""
    physique:             str = ""
    appearance:           str = ""
    family:               str = ""
    formative_background: str = ""
    initial_mood:         str = ""
    personality:          str = ""
    speech_style:         str = ""
    relation_type:        str = ""
    relation_status:      str = ""
    initial_affinity:     int = 0
