"""
텍스트 임베딩 유틸리티.

첫 호출 시 모델을 다운로드/로드합니다 (수 초 소요).
이후 호출은 캐싱된 모델을 사용합니다.
"""

import asyncio
import os
import torch

from dotenv import load_dotenv

load_dotenv()

_model = None  # 싱글톤
MODEL_NAME    = os.getenv("MODEL_EMBEDDER")
EMBEDDING_DIM = os.getenv("EMBEDDING_DIM")
HF_TOKEN      = os.getenv("HF_TOKEN")

def _get_model():
    global _model
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