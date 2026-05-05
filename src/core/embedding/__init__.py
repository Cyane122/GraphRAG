# ================================
# src/core/embedding/__init__.py
#
# core.embedding 패키지 공개 인터페이스.
# ================================

from src.core.embedding.encoder import embed_sync, embed_async, EMBEDDING_DIM

__all__ = ["embed_sync", "embed_async", "EMBEDDING_DIM"]
