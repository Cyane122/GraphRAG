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
PERSPECTIVE = int(os.getenv("PERSPECTIVE", 3))
MAX_TOKEN   = int(os.getenv("MAX_TOKEN",   4096))

# ── LLM 모델 ────────────────────────────────────────────────
MODEL_ACTOR           = os.getenv("MODEL_ACTOR",           "gemini-3.1-pro-preview")
MODEL_CLASSIFIER      = os.getenv("MODEL_CLASSIFIER",      "gemini-3-flash-preview")
MODEL_STATE_UPDATER   = os.getenv("MODEL_STATE_UPDATER",   "gemini-3-flash-preview")
MODEL_COMPLEX_UPDATER = os.getenv("MODEL_COMPLEX_UPDATER", "gemini-3-flash-preview")

# ── Google Cloud ────────────────────────────────────────────
GOOGLE_PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID")

# ── 임베딩 ──────────────────────────────────────────────────
MODEL_EMBEDDER = os.getenv("MODEL_EMBEDDER")
EMBEDDING_DIM  = os.getenv("EMBEDDING_DIM")
HF_TOKEN       = os.getenv("HF_TOKEN")

# ── 기능 플래그 ─────────────────────────────────────────────
IMPERSONATION = os.getenv("IMPERSONATION", "true").lower() == "true"
