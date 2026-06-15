# ================================
# src/simulation/systems/kakao/models.py
#
# KakaoTalk room and message data models.
#
# Classes
#   - KakaoRoomSummary : Prompt and UI summary for a KakaoTalk room
#   - KakaoMessageDraft : LLM-generated KakaoTalk message draft
# ================================
from pydantic import BaseModel, Field


class KakaoRoomSummary(BaseModel):
    """카카오톡 톡방 요약 정보를 표현합니다."""

    id: str
    name: str
    topic: str = ""
    members: list[str] = Field(default_factory=list)
    recent_messages: list[str] = Field(default_factory=list)


class KakaoMessageDraft(BaseModel):
    """저장 전 카카오톡 메시지 초안을 표현합니다."""

    sender_id: str
    content: str
