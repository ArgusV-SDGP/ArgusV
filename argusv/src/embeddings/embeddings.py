"""
embeddings/embeddings.py — Minimal Local Embedder
-----------------------------------------------------
ArgusV implementation using sentence-transformers to convert text
into a 384-dimensional vector suitable for Postgres (pgvector).
"""

import logging
from typing import Optional

logger = logging.getLogger("embeddings")

class EmbeddingManager:
    """
    RAG Embedder using Sentence-Transformers underneath.
    This creates floating-point arrays out of strings.
    """
    def __init__(self):
        self._model = None

    def _ensure_model(self):
        if not self._model:
            logger.info("[Embeddings] Loading sentence-transformer model for RAG...")
            from sentence_transformers import SentenceTransformer
            # all-MiniLM-L6-v2 produces a fast 384-dimension vector ideal for Postgres
            self._model = SentenceTransformer('all-MiniLM-L6-v2') 
            logger.info("[Embeddings] Model loaded successfully.")

    async def embed_text(self, text: str) -> Optional[list[float]]:
        """
        Takes the text description from the GenAI VLM and returns a vector array.
        """
        if not text:
            return None

        self._ensure_model()
        try:
            vector = self._model.encode(text)
            return vector.tolist()
        except Exception as e:
            logger.error(f"[Embeddings] Failed to embed text: {e}")
            return None

# Singleton instance
vector_db = EmbeddingManager()
