"""
tests/test_embeddings.py — Tests for Embedding Manager and Milvus Client
Task: TEST-01
"""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch, AsyncMock

# Add src to path
import sys
from pathlib import Path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from embeddings.embeddings import EmbeddingManager


class TestEmbeddingManager:
    """Test suite for EmbeddingManager."""

    def test_embedding_manager_initialization(self):
        """Test EmbeddingManager initialization."""
        manager = EmbeddingManager(use_clip=True)

        assert manager._text_model is None
        assert manager._clip_model is None
        assert manager._use_clip is True
        assert manager._device == "cpu"


    @patch('embeddings.embeddings.SentenceTransformer')
    @pytest.mark.asyncio
    async def test_embed_text(self, mock_sentence_transformer):
        """Test text embedding generation."""
        # Setup mock
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1, 0.2, 0.3, 0.4])
        mock_sentence_transformer.return_value = mock_model

        manager = EmbeddingManager(use_clip=False)
        embedding = await manager.embed_text("test text")

        assert embedding is not None
        assert len(embedding) == 4
        assert isinstance(embedding, list)


    @patch('embeddings.embeddings.SentenceTransformer')
    def test_encode_image(self, mock_sentence_transformer):
        """Test image embedding generation."""
        # Setup mock
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1] * 512)
        mock_sentence_transformer.return_value = mock_model

        manager = EmbeddingManager(use_clip=True)
        manager._clip_model = mock_model

        # Create dummy image array
        image = np.zeros((480, 640, 3), dtype=np.uint8)

        embedding = manager.encode_image(image)

        assert embedding is not None
        assert len(embedding) == 512
        assert isinstance(embedding, np.ndarray)


    @patch('embeddings.embeddings.SentenceTransformer')
    def test_encode_multimodal(self, mock_sentence_transformer):
        """Test multimodal embedding (image + text)."""
        # Setup mock
        mock_model = MagicMock()
        img_emb = np.array([0.5] * 512)
        txt_emb = np.array([0.3] * 512)
        mock_model.encode.side_effect = [img_emb, txt_emb]
        mock_sentence_transformer.return_value = mock_model

        manager = EmbeddingManager(use_clip=True)
        manager._clip_model = mock_model

        # Create dummy image
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        text = "person in restricted area"

        embedding = manager.encode_multimodal(image=image, text=text, alpha=0.6)

        assert embedding is not None
        assert len(embedding) == 512
        assert isinstance(embedding, np.ndarray)

        # Verify it's normalized
        norm = np.linalg.norm(embedding)
        assert abs(norm - 1.0) < 0.01  # Should be unit vector


    @patch('embeddings.embeddings.SentenceTransformer')
    def test_similarity(self, mock_sentence_transformer):
        """Test cosine similarity computation."""
        manager = EmbeddingManager(use_clip=False)

        emb1 = np.array([1.0, 0.0, 0.0])
        emb2 = np.array([1.0, 0.0, 0.0])
        emb3 = np.array([0.0, 1.0, 0.0])

        # Identical embeddings
        sim_same = manager.similarity(emb1, emb2)
        assert abs(sim_same - 1.0) < 0.01

        # Orthogonal embeddings
        sim_diff = manager.similarity(emb1, emb3)
        assert abs(sim_diff - 0.0) < 0.01


    def test_embed_empty_text(self):
        """Test handling of empty text input."""
        manager = EmbeddingManager(use_clip=False)

        import asyncio
        embedding = asyncio.run(manager.embed_text(""))

        assert embedding is None


    @patch('embeddings.embeddings.SentenceTransformer')
    def test_encode_multimodal_image_only(self, mock_sentence_transformer):
        """Test multimodal encoding with only image."""
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.5] * 512)
        mock_sentence_transformer.return_value = mock_model

        manager = EmbeddingManager(use_clip=True)
        manager._clip_model = mock_model

        image = np.zeros((480, 640, 3), dtype=np.uint8)

        embedding = manager.encode_multimodal(image=image, text=None)

        assert embedding is not None
        assert len(embedding) == 512


    @patch('embeddings.embeddings.SentenceTransformer')
    def test_encode_multimodal_text_only(self, mock_sentence_transformer):
        """Test multimodal encoding with only text."""
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.3] * 512)
        mock_sentence_transformer.return_value = mock_model

        manager = EmbeddingManager(use_clip=True)
        manager._clip_model = mock_model

        embedding = manager.encode_multimodal(image=None, text="test text")

        assert embedding is not None
        assert len(embedding) == 512


class TestMilvusClient:
    """Test suite for Milvus vector database client."""

    @patch('embeddings.milvus_client.MilvusClient')
    def test_milvus_client_initialization(self, mock_milvus):
        """Test MilvusClient initialization."""
        from embeddings.milvus_client import MilvusClient as ArgusVMilvusClient

        mock_client = MagicMock()
        mock_milvus.return_value = mock_client

        client = ArgusVMilvusClient(host="localhost", port=19530)

        # Verify connection attempted
        assert mock_milvus.called


    @pytest.mark.asyncio
    @patch('embeddings.milvus_client.MilvusClient')
    async def test_insert_frame_embedding(self, mock_milvus):
        """Test inserting frame embedding into Milvus."""
        from embeddings.milvus_client import MilvusClient as ArgusVMilvusClient

        mock_client = MagicMock()
        mock_milvus.return_value = mock_client

        client = ArgusVMilvusClient()

        embedding = np.random.rand(512).astype(np.float32).tolist()

        await client.insert_frame_embedding(
            frame_id="frame-001",
            camera_id="cam-01",
            timestamp=1234567890,
            embedding=embedding,
            segment_id="seg-001",
            has_detection=True,
            detection_classes="person",
        )

        # Verify insert was called
        assert mock_client.insert.called or mock_client.upsert.called


    @pytest.mark.asyncio
    @patch('embeddings.milvus_client.MilvusClient')
    async def test_search_similar_frames(self, mock_milvus):
        """Test semantic search for similar frames."""
        from embeddings.milvus_client import MilvusClient as ArgusVMilvusClient

        mock_client = MagicMock()
        mock_client.search.return_value = [
            [
                {"id": 1, "distance": 0.15, "entity": {"frame_id": "frame-001"}},
                {"id": 2, "distance": 0.23, "entity": {"frame_id": "frame-002"}},
            ]
        ]
        mock_milvus.return_value = mock_client

        client = ArgusVMilvusClient()

        query_embedding = np.random.rand(512).astype(np.float32).tolist()

        results = await client.search_similar_frames(
            query_embedding=query_embedding,
            limit=10,
        )

        # Verify search was called
        assert mock_client.search.called


    @pytest.mark.asyncio
    @patch('embeddings.milvus_client.MilvusClient')
    async def test_search_with_filters(self, mock_milvus):
        """Test semantic search with metadata filters."""
        from embeddings.milvus_client import MilvusClient as ArgusVMilvusClient

        mock_client = MagicMock()
        mock_client.search.return_value = [[]]
        mock_milvus.return_value = mock_client

        client = ArgusVMilvusClient()

        query_embedding = np.random.rand(512).astype(np.float32).tolist()

        await client.search_similar_frames(
            query_embedding=query_embedding,
            limit=10,
            camera_id="cam-01",
            time_range=(1234567800, 1234567900),
            has_detection=True,
        )

        # Verify search was called with filters
        assert mock_client.search.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
