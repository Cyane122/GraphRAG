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
MODEL_EMBEDDER = os.getenv("MODEL_EMBEDDER")
EMBEDDING_DIM  = os.getenv("EMBEDDING_DIM")
HF_TOKEN       = os.getenv("HF_TOKEN")

# ── 기능 플래그 ─────────────────────────────────────────────
IMPERSONATION = os.getenv("IMPERSONATION", "true").lower() == "true"
# 측정 단계: shadow = legacy + integrated/unified 동시 실행 → shadow_diff + llm_latency 로 병목 비교
MANAGER_PLANNER_MODE = os.getenv("MANAGER_PLANNER_MODE", "shadow").strip().lower()
TURN_EXTRACTOR_MODE  = os.getenv("TURN_EXTRACTOR_MODE",  "shadow").strip().lower()
