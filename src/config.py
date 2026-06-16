# ================================
# src/config.py
#
# 환경변수를 한 곳에서 읽어 상수로 제공합니다.
# 모든 모듈은 os.getenv 대신 이 파일에서 import합니다.
# ================================

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── 앱 설정 ─────────────────────────────────────────────────
WORLD_ID    = os.getenv("WORLD_ID",    "babe_univ")
MAX_TOKEN   = int(os.getenv("MAX_TOKEN",   12288))

# ── LLM 모델 ────────────────────────────────────────────────
MODEL_ACTOR           = os.getenv("MODEL_ACTOR",           "gemini-3.1-pro-preview")
MODEL_CLASSIFIER      = os.getenv("MODEL_CLASSIFIER",      "gemini-3-flash-preview")
MODEL_STATE_UPDATER   = os.getenv("MODEL_STATE_UPDATER",   "gemini-3-flash-preview")
# temperature=0 구조화 추출 전용 (multi_character / dynamic_information / state_updater)
MODEL_COMPLEX_UPDATER = os.getenv("MODEL_COMPLEX_UPDATER", "gemini-3-flash-preview")
# 이벤트 생성, 보조 관계 업데이트 등 판단·서술이 필요한 Pro 전용 작업
MODEL_EVENT_CREATOR   = os.getenv("MODEL_EVENT_CREATOR",   "gemini-3.1-pro-preview")
MODEL_PRO_UPDATER     = os.getenv("MODEL_PRO_UPDATER",     "gemini-3.1-pro-preview")
MODEL_MANAGER_PLANNER = os.getenv("MODEL_MANAGER_PLANNER", MODEL_PRO_UPDATER)
MODEL_TURN_EXTRACTOR  = os.getenv("MODEL_TURN_EXTRACTOR",  MODEL_PRO_UPDATER)
MODEL_OUTPUT_REPAIR   = os.getenv("MODEL_OUTPUT_REPAIR",   "gemini-3-flash-preview")

# ── Google Cloud ────────────────────────────────────────────
GOOGLE_PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", os.getenv("CLOUD_ML_REGION", "global"))

# ── Direct partner model APIs ───────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_CLAUDE_SONNET_MODEL = os.getenv("ANTHROPIC_CLAUDE_SONNET_MODEL", "claude-sonnet-4-6")
ANTHROPIC_CLAUDE_OPUS_4_6_MODEL = os.getenv("ANTHROPIC_CLAUDE_OPUS_4_6_MODEL", "claude-opus-4-6")
ANTHROPIC_CLAUDE_OPUS_4_7_MODEL = os.getenv("ANTHROPIC_CLAUDE_OPUS_4_7_MODEL", "claude-opus-4-7")
ANTHROPIC_CLAUDE_OPUS_4_8_MODEL = os.getenv("ANTHROPIC_CLAUDE_OPUS_4_8_MODEL", "claude-opus-4-8")
ANTHROPIC_CLAUDE_OPUS_MODEL = os.getenv("ANTHROPIC_CLAUDE_OPUS_MODEL", ANTHROPIC_CLAUDE_OPUS_4_8_MODEL)

# ── 임베딩 ──────────────────────────────────────────────────
def _embedding_dim(raw: str | None) -> int | None:
    """EMBEDDING_DIM을 파싱한다.

    미설정이면 None(호출부가 1024 기본값을 쓴다). 설정됐는데 정수가 아니거나 양수가 아니면
    import 시점에 즉시 실패한다 — 잘못된 차원을 조용히 1024로 떨어뜨리면, encoder가 실제로
    내보내는 차원과 벡터 스키마(FLOAT[1024])가 어긋나 이후 Event/Memory 임베딩 저장이
    소리 없이 실패할 수 있기 때문이다(빠른 실패가 더 안전)."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"EMBEDDING_DIM={text!r} is not an integer") from exc
    if value <= 0:
        raise ValueError(f"EMBEDDING_DIM={text!r} must be a positive integer")
    return value


def _validate_hf_token(raw: str | None) -> str | None:
    """HF_TOKEN을 정규화한다. 설정돼 있는데 형식이 어긋나면 경고만 한다(공개 모델은 토큰 불필요)."""
    text = (raw or "").strip()
    if not text:
        return None
    if not text.startswith("hf_"):
        print("[config] HF_TOKEN is set but does not start with 'hf_'; gated-model downloads may fail.")
    return text


MODEL_EMBEDDER = os.getenv("MODEL_EMBEDDER")
EMBEDDING_DIM  = _embedding_dim(os.getenv("EMBEDDING_DIM"))
HF_TOKEN       = _validate_hf_token(os.getenv("HF_TOKEN"))

# ── 기능 플래그 ─────────────────────────────────────────────
IMPERSONATION = os.getenv("IMPERSONATION", "true").lower() == "true"
# 측정 결과(2026-06-15): integrated/unified는 Pro 모델이라 legacy(Flash)보다 10~25s 느림 → 채택 안 함.
# legacy 기본 유지. shadow/unified/integrated는 측정·실험용으로만 env에서 켠다.
MANAGER_PLANNER_MODE = os.getenv("MANAGER_PLANNER_MODE", "legacy").strip().lower()
TURN_EXTRACTOR_MODE  = os.getenv("TURN_EXTRACTOR_MODE",  "legacy").strip().lower()
