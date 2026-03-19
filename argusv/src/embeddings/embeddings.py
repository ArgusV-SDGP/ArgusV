"""
embeddings/embeddings.py — Multimodal Embedding Manager
-------------------------------------------------------
Tasks: VLM-07, VLM-08
Supports both text and image embeddings:
- Text-only: 384-dim (all-MiniLM-L6-v2) for RAG text search
- Multimodal: 512-dim CLIP (ViT-B/32) for video + text search

"""

import base64
import io
import logging
from typing import Optional, Union

import cv2
import numpy as np
from PIL import Image

import config as cfg

logger = logging.getLogger("embeddings")


class EmbeddingManager:
    """
    Multimodal RAG Embedder supporting text and image embeddings.

    Models:
    - Text-only: all-MiniLM-L6-v2 (384-dim, fast, for text RAG)
    - Multimodal: CLIP ViT-B/32 (512-dim, for video search)
    """

    def __init__(self, use_clip: bool = True):
        self._text_model = None  # 384-dim text model
        self._clip_model = None  # 512-dim CLIP model
        self._clip_processor = None
        self._use_clip = use_clip
        self._device = "cpu"

    def _ensure_text_model(self):
        """Load text-only embedding model (384-dim)"""
        if not self._text_model:
            logger.info("[Embeddings] Loading text model (all-MiniLM-L6-v2)...")
            from sentence_transformers import SentenceTransformer

            self._text_model = SentenceTransformer('all-MiniLM-L6-v2')
            logger.info("[Embeddings] Text model loaded (384-dim)")

    def _ensure_clip_model(self):
        """Load CLIP model for multimodal embeddings (512-dim)"""
        if not self._clip_model and self._use_clip:
            logger.info("[Embeddings] Loading CLIP model (ViT-B/32)...")

            try:
                # Try sentence-transformers CLIP
                from sentence_transformers import SentenceTransformer

                self._clip_model = SentenceTransformer('clip-ViT-B-32')
                self._device = str(self._clip_model.device)
                logger.info(f"[Embeddings] CLIP loaded (sentence-transformers) on {self._device}")

            except Exception:
                # Fallback to transformers
                try:
                    from transformers import CLIPProcessor, CLIPModel
                    import torch

                    model_id = "openai/clip-vit-base-patch32"
                    self._clip_model = CLIPModel.from_pretrained(model_id)
                    self._clip_processor = CLIPProcessor.from_pretrained(model_id)

                    if torch.cuda.is_available():
                        self._clip_model = self._clip_model.cuda()
                        self._device = "cuda"
                    else:
                        self._device = "cpu"

                    logger.info(f"[Embeddings] CLIP loaded (transformers) on {self._device}")

                except ImportError:
                    logger.warning(
                        "[Embeddings] CLIP not available. Install with: "
                        "pip install sentence-transformers"
                    )
                    self._use_clip = False

    async def embed_text(self, text: str) -> Optional[list[float]]:
        """
        Embed text using 384-dim model (for text RAG).
        Args:
            text: Text string to embed

        Returns:
            384-dim embedding vector as list
        """
        if not text:
            return None

        self._ensure_text_model()

        try:
            vector = self._text_model.encode(text)
            return vector.tolist()
        except Exception as e:
            logger.error(f"[Embeddings] Failed to embed text: {e}")
            return None

    def encode_text_clip(self, text: str) -> Optional[np.ndarray]:
        """
        Embed text using CLIP (512-dim, for multimodal search).

        Args:
            text: Text string

        Returns:
            512-dim CLIP embedding
        """
        if not text:
            return None

        self._ensure_clip_model()

        if not self._use_clip:
            logger.warning("[Embeddings] CLIP not available, falling back to text model")
            return np.array(self.embed_text(text), dtype=np.float32)

        try:
            if self._clip_processor is not None:
                # Using transformers
                import torch

                inputs = self._clip_processor(text=[text], return_tensors="pt", padding=True)
                if self._device == "cuda":
                    inputs = {k: v.cuda() for k, v in inputs.items()}

                with torch.no_grad():
                    embedding = self._clip_model.get_text_features(**inputs)

                embedding = embedding.cpu().numpy()[0]
            else:
                # Using sentence-transformers
                embedding = self._clip_model.encode(text, convert_to_numpy=True)

            # Normalize
            embedding = embedding / np.linalg.norm(embedding)
            return embedding.astype(np.float32)

        except Exception as e:
            logger.error(f"[Embeddings] CLIP text encoding failed: {e}")
            return None

    def encode_image(self, image: Union[np.ndarray, str]) -> Optional[np.ndarray]:
        """
        Encode image to 512-dim CLIP embedding.

        Args:
            image: numpy array (H,W,3 BGR) or base64 string

        Returns:
            512-dim CLIP embedding
        """
        self._ensure_clip_model()

        if not self._use_clip:
            logger.warning("[Embeddings] CLIP not available")
            return None

        try:
            # Handle base64 input
            if isinstance(image, str):
                image = self._base64_to_array(image)

            # Convert BGR to RGB
            if len(image.shape) == 3 and image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

            # Convert to PIL
            pil_image = Image.fromarray(image)

            # Encode
            if self._clip_processor is not None:
                import torch

                inputs = self._clip_processor(images=pil_image, return_tensors="pt")
                if self._device == "cuda":
                    inputs = {k: v.cuda() for k, v in inputs.items()}

                with torch.no_grad():
                    embedding = self._clip_model.get_image_features(**inputs)

                embedding = embedding.cpu().numpy()[0]
            else:
                embedding = self._clip_model.encode(pil_image, convert_to_numpy=True)

            # Normalize
            embedding = embedding / np.linalg.norm(embedding)
            return embedding.astype(np.float32)

        except Exception as e:
            logger.error(f"[Embeddings] Image encoding failed: {e}")
            return None

    def encode_multimodal(
        self,
        image: Optional[np.ndarray] = None,
        text: Optional[str] = None,
        alpha: float = 0.7
    ) -> Optional[np.ndarray]:
        """
        Create combined embedding from image and text.

        Args:
            image: Image array (optional)
            text: Text description (optional)
            alpha: Weight for image (1-alpha for text)

        Returns:
            Combined 512-dim CLIP embedding
        """
        if image is None and text is None:
            return None

        if image is not None and text is not None:
            img_emb = self.encode_image(image)
            txt_emb = self.encode_text_clip(text)

            if img_emb is None or txt_emb is None:
                return img_emb if img_emb is not None else txt_emb

            # Weighted combination
            combined = alpha * img_emb + (1 - alpha) * txt_emb
            combined = combined / np.linalg.norm(combined)
            return combined.astype(np.float32)

        elif image is not None:
            return self.encode_image(image)
        else:
            return self.encode_text_clip(text)

    def _base64_to_array(self, b64_string: str) -> np.ndarray:
        """Convert base64 image string to numpy array"""
        img_bytes = base64.b64decode(b64_string)
        img = Image.open(io.BytesIO(img_bytes))
        return np.array(img)

    def similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """Compute cosine similarity between embeddings"""
        return float(np.dot(emb1, emb2))


# Global singleton
_embedding_manager: Optional[EmbeddingManager] = None


def get_embedding_manager() -> EmbeddingManager:
    """Get or create global embedding manager"""
    global _embedding_manager

    if _embedding_manager is None:
        _embedding_manager = EmbeddingManager(use_clip=True)

    return _embedding_manager


# Legacy alias for backward compatibility
vector_db = get_embedding_manager()
