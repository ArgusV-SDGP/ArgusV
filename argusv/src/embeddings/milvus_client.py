"""
embeddings/milvus_client.py — Milvus Vector Database Client
------------------------------------------------------------
Tasks: VLM-07, VLM-08

Manages Milvus vector database for video embeddings:
- Store CLIP embeddings for video frames/segments
- Semantic search for video retrieval
- Hybrid search (vector + metadata filtering)
- Collection management
"""

import logging
import time
from typing import Any, Optional

import numpy as np

import config as cfg

logger = logging.getLogger("embeddings.milvus")


class MilvusClient:
    """
    Milvus vector database client for ArgusV video embeddings.

    Collections:
    - video_frames: Individual frame embeddings (512-dim CLIP)
    - video_segments: Segment-level embeddings (aggregated)
    - detections: Detection event embeddings with metadata
    """

    def __init__(self):
        self._client = None
        self._connected = False
        self.collection_name = cfg.MILVUS_COLLECTION_NAME

    async def connect(self):
        """Initialize connection to Milvus"""
        try:
            from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility

            # Connect to Milvus
            connections.connect(
                alias="default",
                host=cfg.MILVUS_HOST,
                port=cfg.MILVUS_PORT,
                user=cfg.MILVUS_USER if cfg.MILVUS_USER else None,
                password=cfg.MILVUS_PASSWORD if cfg.MILVUS_PASSWORD else None,
            )

            self._connected = True
            logger.info(f"[Milvus] Connected to {cfg.MILVUS_HOST}:{cfg.MILVUS_PORT}")

            # Create collections if they don't exist
            await self._ensure_collections()

        except ImportError:
            logger.error("[Milvus] pymilvus not installed — run: pip install pymilvus")
            self._connected = False
        except Exception as e:
            logger.error(f"[Milvus] Connection failed: {e}")
            self._connected = False

    async def _ensure_collections(self):
        """Create Milvus collections if they don't exist"""
        from pymilvus import Collection, FieldSchema, CollectionSchema, DataType, utility

        # Video Frames Collection
        if not utility.has_collection("video_frames"):
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="frame_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="camera_id", dtype=DataType.VARCHAR, max_length=50),
                FieldSchema(name="timestamp", dtype=DataType.INT64),  # Unix timestamp
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=512),  # CLIP ViT-B/32
                FieldSchema(name="segment_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="has_detection", dtype=DataType.BOOL),
                FieldSchema(name="detection_classes", dtype=DataType.VARCHAR, max_length=500),
            ]
            schema = CollectionSchema(fields, description="Video frame embeddings")
            collection = Collection("video_frames", schema)

            # Create index
            index_params = {
                "metric_type": "L2",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 1024}
            }
            collection.create_index("embedding", index_params)
            logger.info("[Milvus] Created collection: video_frames")

        # Detection Events Collection
        if not utility.has_collection("detection_events"):
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="detection_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="incident_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="camera_id", dtype=DataType.VARCHAR, max_length=50),
                FieldSchema(name="zone_name", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="timestamp", dtype=DataType.INT64),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=512),
                FieldSchema(name="object_class", dtype=DataType.VARCHAR, max_length=50),
                FieldSchema(name="threat_level", dtype=DataType.VARCHAR, max_length=20),
                FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=1000),
            ]
            schema = CollectionSchema(fields, description="Detection event embeddings")
            collection = Collection("detection_events", schema)

            # Create index
            index_params = {
                "metric_type": "L2",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 512}
            }
            collection.create_index("embedding", index_params)
            logger.info("[Milvus] Created collection: detection_events")

    async def insert_frame_embedding(
        self,
        frame_id: str,
        camera_id: str,
        timestamp: int,
        embedding: np.ndarray,
        segment_id: Optional[str] = None,
        has_detection: bool = False,
        detection_classes: str = "",
    ) -> int:
        """
        Insert a video frame embedding into Milvus.

        Args:
            frame_id: Unique frame identifier
            camera_id: Camera ID
            timestamp: Unix timestamp
            embedding: 512-dim CLIP embedding vector
            segment_id: Associated video segment ID
            has_detection: Whether frame contains detections
            detection_classes: Comma-separated detection class names

        Returns:
            Milvus insert ID
        """
        if not self._connected:
            logger.warning("[Milvus] Not connected — skipping insert")
            return -1

        from pymilvus import Collection

        collection = Collection("video_frames")

        data = [
            [frame_id],
            [camera_id],
            [timestamp],
            [embedding.tolist()],
            [segment_id or ""],
            [has_detection],
            [detection_classes],
        ]

        result = collection.insert(data)
        collection.flush()

        logger.debug(f"[Milvus] Inserted frame {frame_id} (has_detection={has_detection})")
        return result.primary_keys[0]

    async def insert_detection_embedding(
        self,
        detection_id: str,
        incident_id: str,
        camera_id: str,
        zone_name: str,
        timestamp: int,
        embedding: np.ndarray,
        object_class: str,
        threat_level: str,
        summary: str,
    ) -> int:
        """
        Insert a detection event embedding into Milvus.

        Args:
            detection_id: Detection UUID
            incident_id: Associated incident UUID
            camera_id: Camera ID
            zone_name: Zone name
            timestamp: Unix timestamp
            embedding: 512-dim embedding (CLIP image + text combined)
            object_class: Detected object class
            threat_level: HIGH/MEDIUM/LOW
            summary: VLM-generated summary

        Returns:
            Milvus insert ID
        """
        if not self._connected:
            logger.warning("[Milvus] Not connected — skipping insert")
            return -1

        from pymilvus import Collection

        collection = Collection("detection_events")

        data = [
            [detection_id],
            [incident_id or ""],
            [camera_id],
            [zone_name],
            [timestamp],
            [embedding.tolist()],
            [object_class],
            [threat_level],
            [summary[:1000]],  # Truncate to max length
        ]

        result = collection.insert(data)
        collection.flush()

        logger.info(f"[Milvus] Indexed detection {detection_id} ({threat_level})")
        return result.primary_keys[0]

    async def search_similar_frames(
        self,
        query_embedding: np.ndarray,
        limit: int = 10,
        camera_id: Optional[str] = None,
        has_detection: Optional[bool] = None,
        time_range: Optional[tuple[int, int]] = None,
    ) -> list[dict[str, Any]]:
        """
        Search for similar video frames using vector similarity.

        Args:
            query_embedding: Query embedding vector (512-dim)
            limit: Maximum number of results
            camera_id: Filter by camera ID
            has_detection: Filter by detection presence
            time_range: (start_ts, end_ts) tuple for time filtering

        Returns:
            List of matching frames with metadata and similarity scores
        """
        if not self._connected:
            logger.warning("[Milvus] Not connected — returning empty results")
            return []

        from pymilvus import Collection

        collection = Collection("video_frames")
        collection.load()

        # Build filter expression
        filters = []
        if camera_id:
            filters.append(f'camera_id == "{camera_id}"')
        if has_detection is not None:
            filters.append(f'has_detection == {str(has_detection).lower()}')
        if time_range:
            filters.append(f'timestamp >= {time_range[0]} && timestamp <= {time_range[1]}')

        expr = " && ".join(filters) if filters else None

        # Search
        search_params = {"metric_type": "L2", "params": {"nprobe": 10}}
        results = collection.search(
            data=[query_embedding.tolist()],
            anns_field="embedding",
            param=search_params,
            limit=limit,
            expr=expr,
            output_fields=["frame_id", "camera_id", "timestamp", "segment_id", "has_detection", "detection_classes"],
        )

        # Format results
        matches = []
        for hit in results[0]:
            matches.append({
                "frame_id": hit.entity.get("frame_id"),
                "camera_id": hit.entity.get("camera_id"),
                "timestamp": hit.entity.get("timestamp"),
                "segment_id": hit.entity.get("segment_id"),
                "has_detection": hit.entity.get("has_detection"),
                "detection_classes": hit.entity.get("detection_classes"),
                "distance": hit.distance,
                "score": 1.0 / (1.0 + hit.distance),  # Convert distance to similarity score
            })

        logger.info(f"[Milvus] Found {len(matches)} similar frames")
        return matches

    async def search_similar_detections(
        self,
        query_embedding: np.ndarray,
        limit: int = 10,
        camera_id: Optional[str] = None,
        zone_name: Optional[str] = None,
        threat_level: Optional[str] = None,
        time_range: Optional[tuple[int, int]] = None,
    ) -> list[dict[str, Any]]:
        """
        Search for similar detection events using semantic similarity.

        Args:
            query_embedding: Query embedding (from text or image)
            limit: Maximum results
            camera_id: Filter by camera
            zone_name: Filter by zone
            threat_level: Filter by threat level (HIGH/MEDIUM/LOW)
            time_range: Time range filter

        Returns:
            List of similar detections with metadata and scores
        """
        if not self._connected:
            logger.warning("[Milvus] Not connected — returning empty results")
            return []

        from pymilvus import Collection

        collection = Collection("detection_events")
        collection.load()

        # Build filter
        filters = []
        if camera_id:
            filters.append(f'camera_id == "{camera_id}"')
        if zone_name:
            filters.append(f'zone_name == "{zone_name}"')
        if threat_level:
            filters.append(f'threat_level == "{threat_level}"')
        if time_range:
            filters.append(f'timestamp >= {time_range[0]} && timestamp <= {time_range[1]}')

        expr = " && ".join(filters) if filters else None

        # Search
        search_params = {"metric_type": "L2", "params": {"nprobe": 10}}
        results = collection.search(
            data=[query_embedding.tolist()],
            anns_field="embedding",
            param=search_params,
            limit=limit,
            expr=expr,
            output_fields=["detection_id", "incident_id", "camera_id", "zone_name", "timestamp", "object_class", "threat_level", "summary"],
        )

        # Format results
        matches = []
        for hit in results[0]:
            matches.append({
                "detection_id": hit.entity.get("detection_id"),
                "incident_id": hit.entity.get("incident_id"),
                "camera_id": hit.entity.get("camera_id"),
                "zone_name": hit.entity.get("zone_name"),
                "timestamp": hit.entity.get("timestamp"),
                "object_class": hit.entity.get("object_class"),
                "threat_level": hit.entity.get("threat_level"),
                "summary": hit.entity.get("summary"),
                "distance": hit.distance,
                "score": 1.0 / (1.0 + hit.distance),
            })

        logger.info(f"[Milvus] Found {len(matches)} similar detections")
        return matches

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: np.ndarray,
        limit: int = 10,
        **filters,
    ) -> list[dict[str, Any]]:
        """
        Hybrid search combining vector similarity and metadata filtering.

        Args:
            query_text: Natural language query
            query_embedding: Query embedding vector
            limit: Max results
            **filters: Additional filter kwargs (camera_id, zone_name, etc.)

        Returns:
            Ranked list of relevant detections
        """
        results = await self.search_similar_detections(
            query_embedding=query_embedding,
            limit=limit,
            camera_id=filters.get("camera_id"),
            zone_name=filters.get("zone_name"),
            threat_level=filters.get("threat_level"),
            time_range=filters.get("time_range"),
        )

        # Re-rank results using text similarity if needed
        # TODO: Implement BM25 or other text-based re-ranking

        return results

    async def get_collection_stats(self) -> dict[str, Any]:
        """Get statistics about indexed data"""
        if not self._connected:
            return {"connected": False}

        from pymilvus import Collection, utility

        stats = {"connected": True, "collections": {}}

        for collection_name in ["video_frames", "detection_events"]:
            if utility.has_collection(collection_name):
                collection = Collection(collection_name)
                stats["collections"][collection_name] = {
                    "num_entities": collection.num_entities,
                    "schema": collection.schema.to_dict(),
                }

        return stats


# Global client instance
_milvus_client: Optional[MilvusClient] = None


async def get_milvus_client() -> MilvusClient:
    """Get or create global Milvus client"""
    global _milvus_client

    if _milvus_client is None:
        _milvus_client = MilvusClient()
        await _milvus_client.connect()

    return _milvus_client
