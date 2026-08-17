# ================================
# src/config.py
#
# 환경변수를 한 곳에서 읽어 상수로 제공합니다.
# 모든 모듈은 os.getenv 대신 이 파일에서 import합니다.
#
# Functions
#   - _embedding_dim(raw: str | None) -> int | None : 임베딩 차원 환경변수를 검증·파싱합니다.
#   - _validate_hf_token(raw: str | None) -> str | None : Hugging Face 토큰을 정규화합니다.
#   - wiki_system_defaults() -> dict[str, bool] : Wiki gated postprocessor의 현재 기본값 표를 반환합니다.
# ================================

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── 앱 설정 ─────────────────────────────────────────────────
WORLD_ID    = os.getenv("WORLD_ID",    "babe_univ")
MAX_TOKEN   = int(os.getenv("MAX_TOKEN",   12288))
WIKI_VAULT_ROOT = Path(os.getenv("WIKI_VAULT_ROOT", "wiki_v2"))
# Wiki recall: 누적 문서(event/memory/goal/item/secret)가 이 예산을 넘을 때만
# 최근성·구조 관련성으로 축소한다. 예산 이하 thread는 전체 포함으로 동작 변화가 없다.
# Actor prompt는 정밀도(작게), Updater 입력은 recall(크게)을 우선한다.
WIKI_ACTOR_RECALL_BUDGET = int(os.getenv("WIKI_ACTOR_RECALL_BUDGET", 24))
WIKI_UPDATER_RECALL_BUDGET = int(os.getenv("WIKI_UPDATER_RECALL_BUDGET", 48))
WIKI_ACTOR_RECALL_TOKEN_BUDGET = int(
    os.getenv("WIKI_ACTOR_RECALL_TOKEN_BUDGET", 12000)
)
WIKI_UPDATER_RECALL_TOKEN_BUDGET = int(
    os.getenv("WIKI_UPDATER_RECALL_TOKEN_BUDGET", 32000)
)
# Wiki 실험적 postprocessor 게이트(기본 off). 켜면 정상 단일-Updater 뒤에 추가 LLM 호출로
# 해당 변경을 같은 pending commit에 충돌 없이 병합한다. 사용자 검증 후 default 여부를 정한다.
WIKI_MEMORY_DISTORTION = os.getenv("WIKI_MEMORY_DISTORTION", "false").strip().lower() == "true"
WIKI_GOSSIP = os.getenv("WIKI_GOSSIP", "false").strip().lower() == "true"
WIKI_PERSONALITY_DRIFT = os.getenv("WIKI_PERSONALITY_DRIFT", "false").strip().lower() == "true"
WIKI_PREGNANCY = os.getenv("WIKI_PREGNANCY", "false").strip().lower() == "true"
WIKI_SYSTEM_KEYS = (
    "memory_distortion",
    "gossip",
    "personality_drift",
    "pregnancy",
)
HOSTED_UI_ORIGINS = tuple(
    origin.strip()
    for origin in os.getenv(
        "HOSTED_UI_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,https://graphrag-fiction-room.cyane123.chatgpt.site",
    ).split(",")
    if origin.strip()
)

# ── LLM 호출 처리량 제어 ─────────────────────────────────────
# 한 턴에 여러 후처리 updater가 동시에 Vertex로 몰리면 순간 버스트로 429가 난다.
# 동시 진행 중인 비동기 LLM 호출 수를 이 값으로 제한한다(스트리밍 actor 호출 포함).
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", 2))
# 429(RESOURCE_EXHAUSTED)를 만났을 때 exponential backoff로 재시도할 최대 횟수.
LLM_MAX_RETRIES_429 = int(os.getenv("LLM_MAX_RETRIES_429", 4))

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
ANTHROPIC_CLAUDE_SONNET_5_MODEL = os.getenv("ANTHROPIC_CLAUDE_SONNET_5_MODEL", "claude-sonnet-5")
ANTHROPIC_CLAUDE_OPUS_4_6_MODEL = os.getenv("ANTHROPIC_CLAUDE_OPUS_4_6_MODEL", "claude-opus-4-6")
ANTHROPIC_CLAUDE_OPUS_4_7_MODEL = os.getenv("ANTHROPIC_CLAUDE_OPUS_4_7_MODEL", "claude-opus-4-7")
ANTHROPIC_CLAUDE_OPUS_4_8_MODEL = os.getenv("ANTHROPIC_CLAUDE_OPUS_4_8_MODEL", "claude-opus-4-8")
ANTHROPIC_CLAUDE_OPUS_5_MODEL = os.getenv("ANTHROPIC_CLAUDE_OPUS_5_MODEL", "claude-opus-5")
ANTHROPIC_CLAUDE_OPUS_MODEL = os.getenv("ANTHROPIC_CLAUDE_OPUS_MODEL", ANTHROPIC_CLAUDE_OPUS_4_8_MODEL)

# ── DeepSeek API ────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
DEEPSEEK_V4_PRO_MODEL = os.getenv("DEEPSEEK_V4_PRO_MODEL", "deepseek-v4-pro")

# ── Claude on Vertex AI (primary; quota/비용 부족 시 위 다이렉트 API로 폴백) ──
# Vertex 모델 ID는 4.6+/Sonnet 5에서 다이렉트 ID와 동일(평문)하므로 별도 매핑 불필요.
# region은 기본적으로 Gemini와 동일한 GOOGLE_CLOUD_LOCATION(global, Claude 권장값)을 따른다.
ANTHROPIC_VERTEX_REGION = os.getenv("ANTHROPIC_VERTEX_REGION", GOOGLE_CLOUD_LOCATION)

# ── 임베딩 ──────────────────────────────────────────────────
def wiki_system_defaults() -> dict[str, bool]:
    """Wiki gated postprocessor의 현재 기본값 표를 반환합니다."""
    return {
        "memory_distortion": WIKI_MEMORY_DISTORTION,
        "gossip": WIKI_GOSSIP,
        "personality_drift": WIKI_PERSONALITY_DRIFT,
        "pregnancy": WIKI_PREGNANCY,
    }


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
