# ArgusV Complete RAG Pipeline & Prompt Configuration Guide

## Overview

This document describes the complete Video RAG (Retrieval-Augmented Generation) pipeline and custom prompt configuration system implemented in ArgusV.

---

## 1. Video RAG Pipeline Architecture

### Components

```
Video Stream → Frame Sampling → CLIP Embeddings → Milvus Vector DB
                                                        ↓
User Query → Text Embedding ← ← ← ← ← ← ← Semantic Search
      ↓                                                 ↓
    VLM (GPT-4o) ← ← ← Retrieved Video Context ← ← ← ←
      ↓
Natural Language Answer + Source Citations
```

### Key Technologies

| Component | Technology | Dimensions |
|-----------|------------|------------|
| **Text Embeddings** | all-MiniLM-L6-v2 | 384-dim |
| **Multimodal Embeddings** | CLIP ViT-B/32 | 512-dim |
| **Vector Database** | Milvus | Distributed |
| **VLM** | GPT-4o / Gemini / Ollama | Configurable |

---

## 2. Milvus Vector Database

### Collections

#### `video_frames`
Stores individual video frame embeddings for semantic search.

**Schema:**
```python
{
    "id": int64 (auto_increment),
    "frame_id": varchar(100),          # Unique frame ID
    "camera_id": varchar(50),          # Camera identifier
    "timestamp": int64,                # Unix timestamp
    "embedding": float_vector(512),    # CLIP embedding
    "segment_id": varchar(100),        # Video segment ID
    "has_detection": bool,             # Contains detections?
    "detection_classes": varchar(500), # Comma-separated classes
}
```

**Index:** IVF_FLAT with L2 distance, nlist=1024

#### `detection_events`
Stores detection event embeddings with VLM summaries.

**Schema:**
```python
{
    "id": int64 (auto_increment),
    "detection_id": varchar(100),      # Detection UUID
    "incident_id": varchar(100),       # Incident UUID
    "camera_id": varchar(50),
    "zone_name": varchar(100),
    "timestamp": int64,
    "embedding": float_vector(512),    # Multimodal embedding
    "object_class": varchar(50),       # person, vehicle, etc.
    "threat_level": varchar(20),       # HIGH, MEDIUM, LOW
    "summary": varchar(1000),          # VLM-generated summary
}
```

**Index:** IVF_FLAT with L2 distance, nlist=512

### Configuration

Add to `.env`:
```bash
# Milvus Vector Database
MILVUS_HOST=localhost
MILVUS_PORT=19530
MILVUS_USER=          # Optional
MILVUS_PASSWORD=      # Optional
MILVUS_COLLECTION_NAME=argusv_vectors

# Embedding Settings
EMBED_FRAME_SAMPLE_SEC=2  # Sample 1 frame every 2 seconds
```

### Docker Compose (Milvus)

```yaml
services:
  milvus-standalone:
    image: milvusdb/milvus:latest
    ports:
      - "19530:19530"
      - "9091:9091"
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
    volumes:
      - milvus-data:/var/lib/milvus
    depends_on:
      - etcd
      - minio

  etcd:
    image: quay.io/coreos/etcd:latest
    environment:
      ETCD_AUTO_COMPACTION_MODE: revision
      ETCD_AUTO_COMPACTION_RETENTION: "1000"
    volumes:
      - etcd-data:/etcd

  minio:
    image: minio/minio:latest
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    volumes:
      - minio-data:/minio_data
    command: minio server /minio_data

volumes:
  milvus-data:
  etcd-data:
  minio-data:
```

---

## 3. Embedding Pipeline

### Frame Indexing Worker

Automatically indexes video frames:

1. **Segment Complete Event** → Embedding Worker
2. **Frame Sampling** → Sample every N seconds
3. **CLIP Encoding** → Generate 512-dim embeddings
4. **Milvus Insert** → Store with metadata

**Configuration:**
```python
EMBED_FRAME_SAMPLE_SEC = 2  # Sample rate
```

### Detection Indexing Worker

Indexes detection events with multimodal embeddings:

1. **VLM Result** → Detection Embedding Worker
2. **Multimodal Embedding** → Combine image + text (CLIP)
3. **Milvus Insert** → Store detection with summary

**Embedding Strategy:**
- **Image + Text:** `0.6 * image_emb + 0.4 * text_emb` (α=0.6)
- **Image Only:** CLIP image embedding
- **Text Only:** CLIP text embedding

---

## 4. Semantic Video Search

### API Endpoint: `/api/chat`

Query video footage using natural language:

**Request:**
```json
{
  "message": "Show me people loitering near the gate last night",
  "history": [],
  "camera_id": null,
  "zone_id": null,
  "limit": 6
}
```

**Response:**
```json
{
  "answer": "Three instances of loitering were detected near the gate between 22:00-23:30...",
  "sources": [
    {
      "event_id": "evt_123",
      "camera_id": "cam-01",
      "timestamp": "2026-03-16T22:15:00",
      "vlm_summary": "Person standing in restricted zone for 45 seconds",
      "distance": 0.23
    }
  ]
}
```

### Search Capabilities

1. **Semantic Search:**
   - Natural language queries
   - CLIP-based similarity
   - Multi-language support

2. **Hybrid Search:**
   - Vector similarity + metadata filtering
   - Camera/zone/time range filters
   - Threat level filtering

3. **Temporal Search:**
   - Time range queries
   - "Last night", "yesterday morning"
   - Unix timestamp ranges

---

## 5. Custom Prompt Configuration

### Prompt System Architecture

Allows administrators to define custom VLM prompts for specific scenarios:

- **Zone-specific prompts:** Different prompts for different zones
- **Camera-specific prompts:** Customize per camera
- **Object class filters:** Apply to specific object types
- **Priority-based selection:** Higher priority templates override defaults

### PromptTemplate Structure

```python
{
    "prompt_id": "uuid",
    "name": "Parking Lot Loitering",
    "description": "High sensitivity for parking lot zone",
    "template": "CRITICAL: {object_class} detected in parking lot. Dwell time: {dwell_sec}s. Assess if this is suspicious loitering behavior.",
    "zone_filter": "parking_lot",          # Only for this zone
    "camera_filter": null,                 # All cameras
    "object_classes": ["person"],          # Only persons
    "priority": 10,                        # Higher = preferred
    "active": true
}
```

### Available Placeholders

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `{object_class}` | Detected object type | "person" |
| `{zone_name}` | Zone name | "restricted_area" |
| `{camera_id}` | Camera ID | "cam-01" |
| `{dwell_sec}` | Time in zone (seconds) | "45" |
| `{event_type}` | Event type | "LOITERING" |
| `{confidence}` | Detection confidence | "0.87" |
| `{speed}` | Object speed | "2.5" |

### Prompt Management API

#### Create Prompt
```bash
POST /api/prompts
Authorization: Bearer <admin_token>

{
  "name": "Warehouse High Security",
  "description": "Strict detection for warehouse zone",
  "template": "ALERT: {object_class} in {zone_name}. Dwell: {dwell_sec}s. Type: {event_type}. Determine if immediate security response needed.",
  "zone_filter": "warehouse",
  "object_classes": ["person", "vehicle"],
  "priority": 20
}
```

#### List Prompts
```bash
GET /api/prompts?active_only=true
Authorization: Bearer <token>
```

#### Update Prompt
```bash
PUT /api/prompts/{prompt_id}
Authorization: Bearer <admin_token>

{
  "priority": 15,
  "active": false
}
```

#### Test Prompt
```bash
POST /api/prompts/test
Authorization: Bearer <token>

{
  "template": "Alert: {object_class} in {zone_name} for {dwell_sec}s",
  "event_data": {
    "object_class": "person",
    "zone_name": "restricted",
    "dwell_sec": 30
  }
}
```

Response:
```json
{
  "rendered": "Alert: person in restricted for 30s",
  "status": "success"
}
```

---

## 6. Integration with VLM Worker

The VLM pipeline now uses custom prompts:

```python
# In vlm_inference_worker
from prompts.prompt_manager import get_prompt_manager

async def vlm_inference_worker():
    prompt_manager = get_prompt_manager()

    while True:
        event = await bus.vlm_requests.get()

        # Get custom prompt for this event
        custom_prompt = prompt_manager.get_prompt_for_event(event)

        # Use custom prompt in VLM call
        vlm_result = await call_vlm_with_prompt(event, custom_prompt)

        await bus.vlm_results.put(vlm_result)
```

---

## 7. Usage Examples

### Example 1: High Security Zone

Create a strict prompt for restricted areas:

```bash
curl -X POST http://localhost:8000/api/prompts \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Restricted Area - Zero Tolerance",
    "description": "Immediate alert for any person in restricted zone",
    "template": "CRITICAL SECURITY ALERT: {object_class} detected in RESTRICTED ZONE {zone_name}. Dwell time: {dwell_sec} seconds. Event: {event_type}. IMMEDIATE RESPONSE REQUIRED. Respond with HIGH threat level and recommend ALERT action.",
    "zone_filter": "restricted_area",
    "object_classes": ["person"],
    "priority": 100
  }'
```

### Example 2: Parking Lot Monitoring

Lower sensitivity for parking lot:

```bash
curl -X POST http://localhost:8000/api/prompts \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Parking Lot - Normal Activity",
    "description": "Standard monitoring for parking lot",
    "template": "Parking lot activity: {object_class} detected. Dwell: {dwell_sec}s. Only flag as threat if behavior is clearly suspicious (e.g., looking into vehicles, prolonged loitering >5 minutes).",
    "zone_filter": "parking_lot",
    "priority": 5
  }'
```

### Example 3: Video Search Query

Search for specific incidents:

```bash
curl -X POST http://localhost:8000/api/chat/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Show me all incidents where someone was loitering near the entrance yesterday",
    "camera_id": "cam-01",
    "limit": 10
  }'
```

---

## 8. Performance Optimization

### Indexing Strategy

- **Frame Sampling:** Don't index every frame (2s intervals)
- **Batch Processing:** Process frames in batches of 32
- **GPU Acceleration:** Use CUDA for CLIP if available

### Milvus Tuning

```python
# Index parameters
IVF_FLAT:
  nlist = 1024        # Number of clusters
  nprobe = 10         # Search clusters

# For larger datasets, use IVF_SQ8 or HNSW
```

### Embedding Cache

- Cache frequently accessed embeddings
- Use Redis for embedding lookups
- Pre-compute popular query embeddings

---

## 9. Monitoring & Metrics

### Milvus Metrics

```bash
GET /api/milvus/stats
```

Response:
```json
{
  "connected": true,
  "collections": {
    "video_frames": {
      "num_entities": 150000,
      "index_type": "IVF_FLAT"
    },
    "detection_events": {
      "num_entities": 2500,
      "index_type": "IVF_FLAT"
    }
  }
}
```

### Embedding Worker Metrics

- Frames indexed per minute
- Average embedding time
- Milvus insert latency
- Queue depth

---

## 10. Troubleshooting

### Milvus Connection Issues

```bash
# Check Milvus status
docker logs milvus-standalone

# Test connection
curl http://localhost:9091/healthz
```

### CLIP Model Loading

```python
# If sentence-transformers fails, install transformers
pip install transformers torch

# Or use CPU-only
pip install sentence-transformers --no-deps
pip install transformers torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

### Embedding Performance

```python
# Enable GPU
export CUDA_VISIBLE_DEVICES=0

# Monitor GPU usage
nvidia-smi -l 1
```

---

## 11. Security Considerations

1. **Prompt Injection:** Validate all custom prompts
2. **Access Control:** Only ADMIN can create/edit prompts
3. **Rate Limiting:** Limit chat API calls per user
4. **Data Privacy:** Embeddings may contain sensitive info

---

## 12. Future Enhancements

- [ ] Multi-modal search (image + text queries)
- [ ] Video clip generation from search results
- [ ] Real-time embedding updates
- [ ] Distributed Milvus cluster
- [ ] Advanced re-ranking algorithms
- [ ] Face recognition integration
- [ ] License plate recognition
- [ ] Audio event detection
- [ ] Anomaly detection

---

## Conclusion

ArgusV now has a complete Video RAG pipeline with:
✅ Milvus vector database for semantic search
✅ CLIP multimodal embeddings (512-dim)
✅ Automatic video frame indexing
✅ Detection event indexing with VLM summaries
✅ Natural language video search
✅ Custom prompt configuration system
✅ Zone/camera/object-specific threat detection
✅ Admin API for prompt management

This enables powerful capabilities:
- **"Show me people loitering near the gate last night"**
- **Custom threat detection rules per zone**
- **Semantic video search across entire history**
- **Context-aware VLM analysis**

**The system is production-ready for advanced AI surveillance with RAG.**
