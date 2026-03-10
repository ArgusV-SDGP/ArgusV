"""
embeddings/embeddings.py — CLIP semantic embeddings
-----------------------------------------------------
Frigate equivalent: frigate/embeddings/embeddings.py

Generates CLIP image embeddings for semantic search.
Allows queries like "person with red jacket" to find
matching recordings without keywords.

TODO VLM-08: implement
"""

import logging
import numpy as np
from typing import Optional

logger = logging.getLogger("embeddings")


class EmbeddingManager:
    """
    Generates and stores CLIP embeddings for detection frames.
    Frigate stores these in a vector DB (SQLite-vec / Qdrant).
    ArgusV can use pgvector (Postgres extension) or Qdrant.

    TODO VLM-08:
      1. Install clip / open-clip-torch
      2. Generate embedding on each START detection
      3. Store in pgvector column on Detection table
      4. Expose GET /api/search?q=... endpoint using cosine similarity
    """

    def __init__(self):
        self._model = None
        self._preprocess = None
        self._tokenizer = None

    def _load_model(self):
        """Lazy-load CLIP model."""
        if self._model: return
        try:
            import open_clip
            import torch
            logger.info("Loading CLIP model (ViT-B-32)...")
            self._model, _, self._preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="laion2b_s34b_b79k"
            )
            self._tokenizer = open_clip.get_tokenizer("ViT-B-32")
            logger.info("CLIP model loaded successfully")
        except Exception as e:
            logger.warning(f"CLIP model load failed (using random vectors): {e}")

    async def embed_frame(self, frame_b64: str) -> Optional[list[float]]:
        """
        Generate CLIP embedding for a base64 frame.
        Returns 512-dim list of floats or None.
        """
        if not frame_b64: return None
        
        self._load_model()
        if not self._model:
            # Fallback for dev: 512-dim random vector
            return [float(x) for x in np.random.randn(512)]

        try:
            import torch
            from PIL import Image
            import io
            import base64

            # Decode B64
            img_data = base64.b64decode(frame_b64)
            img = Image.open(io.BytesIO(img_data)).convert("RGB")
            
            # Preprocess
            image = self._preprocess(img).unsqueeze(0)
            
            # Encode
            with torch.no_grad():
                image_features = self._model.encode_image(image)
                image_features /= image_features.norm(dim=-1, keepdim=True)
            
            return image_features.cpu().numpy().flatten().tolist()
        except Exception as e:
            logger.error(f"Embedding encoding failed: {e}")
            return None

    async def embed_text(self, query: str) -> Optional[list[float]]:
        """Generate CLIP embedding for a text query."""
        if not query: return None
        
        self._load_model()
        if not self._model:
            return [float(x) for x in np.random.randn(512)]

        try:
            import torch
            text = self._tokenizer([query])
            with torch.no_grad():
                text_features = self._model.encode_text(text)
                text_features /= text_features.norm(dim=-1, keepdim=True)
            return text_features.cpu().numpy().flatten().tolist()
        except Exception as e:
            logger.error(f"Text embedding failed: {e}")
            return None

    async def search(self, query: str, camera_id: str = None,
                      limit: int = 20) -> list[dict]:
        """
        Semantic search using cosine similarity against stored embeddings.
        Task VLM-08
        """
        query_vec = await self.embed_text(query)
        if not query_vec: return []

        from db.connection import get_db_sync
        from db.models import Detection
        from sqlalchemy import text
        
        db = get_db_sync()
        try:
            # Since we are using JSONB instead of pgvector Vector type, 
            # we'll do the similarity in Python or via a custom SQL if using JSONB functions.
            # Performance note: In a real system, move this to pgvector.
            q = db.query(Detection).filter(Detection.embedding.isnot(None))
            if camera_id:
                q = q.filter(Detection.camera_id == camera_id)
            
            results = q.all()
            
            def cosine_sim(v1, v2):
                if not v1 or not v2: return 0
                return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))

            # Rank in Python
            scored = []
            for d in results:
                sim = cosine_sim(query_vec, d.embedding)
                scored.append((sim, d))
            
            scored.sort(key=lambda x: x[0], reverse=True)
            
            return [
                {
                    "detection_id": str(s[1].detection_id),
                    "score": round(float(s[0]), 3),
                    "object_class": s[1].object_class,
                    "detected_at": s[1].detected_at.isoformat(),
                    "vlm_summary": s[1].vlm_summary
                }
                for s in scored[:limit]
            ]
        finally:
            db.close()
