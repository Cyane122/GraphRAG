"""
텍스트 임베딩 유틸리티.

모델: jhgan/ko-sroberta-multitask (768차원, 한국어 특화)
설치: pip install sentence-transformers

첫 호출 시 모델을 다운로드/로드합니다 (수 초 소요).
이후 호출은 캐싱된 모델을 사용합니다.
"""

import asyncio
from typing import Optional

_model = None  # 싱글톤


def _get_model():
    global _model
    if _model is None:
        print("[Embedder] 모델 로드 중: jhgan/ko-sroberta-multitask ...")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("jhgan/ko-sroberta-multitask")
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


EMBEDDING_DIM = 768  # jhgan/ko-sroberta-multitask 출력 차원