# ================================
# src/core/embedding/encoder.py
#
# HuggingFace SentenceTransformer 기반 텍스트 임베딩을 제공합니다.
# 첫 호출 시 모델을 다운로드/로드합니다 (수 초 소요).
# 이후 호출은 싱글톤 캐시된 모델을 사용합니다.
#
# Functions
#   - embed_sync(text: str) -> list[float] : 동기 임베딩. 비동기 컨텍스트 외부에서 사용
#   - embed_async(text: str) -> list[float] : 비동기 임베딩. 이벤트 루프를 차단하지 않도록 executor에서 실행
# ================================

import asyncio
import os
import threading

# HuggingFace 모델 로딩 진행바(tqdm "Loading weights …")를 끈다. import 전에 설정해야 적용된다.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import torch

from src.config import MODEL_EMBEDDER as MODEL_NAME, EMBEDDING_DIM, HF_TOKEN

_model = None  # 싱글톤
# embed_async가 모델을 executor 스레드에서 로드하므로, 동시 첫 호출이 모델을 두 번
# 적재하는 경쟁을 막기 위해 (asyncio.Lock이 아니라) threading.Lock으로 보호한다.
_model_lock = threading.Lock()


def _get_model():
    """SentenceTransformer 싱글톤을 반환한다. 최초 호출 시 로드한다(이중 검사 잠금)."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            print(f"[Embedder] 모델 로드 중: {MODEL_NAME} ...")
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(
                MODEL_NAME,
                device=("cuda" if torch.cuda.is_available() else "cpu"),
                token=HF_TOKEN,
            )
            print("[Embedder] 모델 로드 완료.")
    return _model


def embed_sync(text: str) -> list[float]:
    """동기 임베딩. 비동기 컨텍스트 외부에서 사용."""
    return _get_model().encode(text, show_progress_bar=False).tolist()


async def embed_async(text: str) -> list[float]:
    """
    비동기 임베딩. 이벤트 루프를 차단하지 않도록 executor에서 실행.
    async 함수 내에서 사용.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, embed_sync, text)
