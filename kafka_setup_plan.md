---

Kafka Setup Plan

Phase 1 — Topic Initialization

Topics need to be created before services start. The cleanest approach is a Kafka init container in docker-compose that runs kafka-topics.sh on startup and exits.

Topics to create:

┌────────────────┬──────────────────┬────────────────────────────────┬────────────┬───────────┐
│ Topic │ Producer │ Consumer(s) │ Partitions │ Retention │
├────────────────┼──────────────────┼────────────────────────────────┼────────────┼───────────┤
│ raw-detections │ edge-gateway │ stream-ingestion │ 4 │ 1h │
├────────────────┼──────────────────┼────────────────────────────────┼────────────┼───────────┤
│ vlm-requests │ stream-ingestion │ vlm-inference │ 4 │ 1h │
├────────────────┼──────────────────┼────────────────────────────────┼────────────┼───────────┤
│ vlm-results │ vlm-inference │ decision-engine │ 4 │ 24h │
├────────────────┼──────────────────┼────────────────────────────────┼────────────┼───────────┤
│ actions │ decision-engine │ notification, actuation │ 4 │ 24h │
├────────────────┼──────────────────┼────────────────────────────────┼────────────┼───────────┤
│ config-updates │ decision-engine │ edge-gateway, stream-ingestion │ 1 │ 7d │
└────────────────┴──────────────────┴────────────────────────────────┴────────────┴───────────┘

Dead-letter topics for each (e.g. raw-detections.dlq) should also be created.

File: docker-compose.dev.yml — add a kafka-init service that depends_on: kafka and runs the topic creation commands, then exits.

---

Phase 2 — Shared Message Schemas

Define Pydantic models for each topic's message format. These can live as a shared module inside each service or as a copied schemas.py.

Schemas needed:

# raw-detections

class RawDetection(BaseModel):
event_id: str # uuid
camera_id: str
zone_id: str
detected_objects: list[str] # YOLO class labels
confidence_scores: list[float]
frame_timestamp: datetime
produced_at: datetime

# vlm-requests

class VlmRequest(BaseModel):
event_id: str
camera_id: str
zone_id: str
frame_urls: list[str] # MinIO presigned URLs
detection_context: RawDetection

# vlm-results

class VlmResult(BaseModel):
event_id: str
threat_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
summary: str
confidence: float
recommended_action: str
processed_at: datetime

# actions

class Action(BaseModel):
action_id: str
event_id: str
action_type: Literal["alert", "actuate"]
target: str # Slack channel, device ID, etc.
payload: dict

# config-updates

class ConfigUpdate(BaseModel):
update_type: Literal["zone", "rule", "camera"]
operation: Literal["create", "update", "delete"]
entity_id: str
data: dict

---

Phase 3 — Per-Service Kafka Integration

3a. edge-gateway (confluent-kafka)

- Producer: Publish RawDetection to raw-detections after YOLOv8 detects objects
- Consumer: Subscribe to config-updates to hot-reload zone/rule config from Redis

3b. stream-ingestion (aiokafka)

- Consumer group stream-ingestion-group: Consume raw-detections
- For each message: extract 3 frames from RTSP → upload to MinIO → publish VlmRequest to vlm-requests

3c. vlm-inference (aiokafka)

- Consumer group vlm-inference-group: Consume vlm-requests
- For each message: call GPT-4o Vision with frame URLs → publish VlmResult to vlm-results

3d. decision-engine (aiokafka)

- Consumer group decision-engine-group: Consume vlm-results
- Apply rules from Redis/Postgres → log incident → publish Action to actions + optionally config-updates

3e. notification (aiokafka)

- Consumer group notification-group: Consume actions where action_type == "alert"
- Fan-out to Slack / SMS / Email

3f. actuation (aiokafka)

- Consumer group actuation-group: Consume actions where action_type == "actuate"
- Send MQTT command to Mosquitto → physical device

---

Phase 4 — Resilience & Error Handling

Each consumer should:

1. Wrap message processing in try/except
2. On failure: publish original message to <topic>.dlq with error metadata
3. Use manual offset commit (commit only after successful processing)
4. Implement exponential backoff on transient failures (OpenAI rate limits, MinIO timeouts)
5. Add a startup readiness check — wait for Kafka broker to be available before subscribing

Producer config:

- acks=all for critical topics (vlm-results, actions)
- retries=5 with retry.backoff.ms=500
- enable.idempotence=True

---

Phase 5 — Health & Observability

- Each service's /health endpoint should check Kafka connectivity (e.g., list topics or check consumer lag)
- Log every produced/consumed message with event_id for tracing
- Optional: add kafka-ui (Provectus) as a docker-compose service on port 8080 for dev visibility

---

Recommended Implementation Order

1. Phase 1 (topic init) — unblocks everything else; 1 docker-compose addition
2. Phase 2 (schemas) — define contracts before writing producers/consumers
3. Phase 3a (edge-gateway producer) — first data in the pipeline
4. Phase 3b (stream-ingestion consumer+producer) — frame extraction
5. Phase 3c (vlm-inference) — AI reasoning step
6. Phase 3d (decision-engine consumer) — incident creation
7. Phase 3e + 3f (notification + actuation) — output layer
8. Phase 4 (resilience) — harden each service
9. Phase 5 (observability) — visibility tooling

---

● Kafka Fundamentals + How ArgusV Uses It

---

The Core Problem Kafka Solves

Imagine 6 microservices that need to talk to each other. Without a message broker, each service calls the others directly (HTTP). This creates problems:

edge-gateway ──HTTP──► stream-ingestion
│
└──HTTP──► decision-engine
│
└──HTTP──► notification

- If stream-ingestion is down, edge-gateway crashes or loses data
- If edge-gateway produces faster than stream-ingestion can process, you lose events
- Adding a new consumer means changing the producer's code

Kafka's solution: decouple producers and consumers entirely via a persistent log.

edge-gateway ──► [Kafka] ◄── stream-ingestion reads at its own pace
◄── decision-engine also reads independently
◄── any new service can read without touching edge-gateway

---

The 5 Core Concepts

1. Topic

A topic is a named, ordered log of messages — like a database table but append-only.

Topic: "raw-detections"

Offset: 0 1 2 3 4
│ │ │ │ │
▼ ▼ ▼ ▼ ▼
[msg 0] [msg 1] [msg 2] [msg 3] [msg 4]
cam_01 cam_02 cam_01 cam_03 cam_01

- Messages are never deleted when consumed (unlike a queue)
- Messages are deleted after a retention period (e.g. 1 hour, 7 days)
- Any number of consumers can read the same topic independently

In ArgusV, topics are the "channels" between services:
raw-detections → what edge-gateway detected
vlm-requests → frames that need AI analysis
vlm-results → what the AI concluded
actions → what to do (alert/actuate)
config-updates → zone/rule changes to broadcast

---

2. Partition

Topics are split into partitions for parallelism. Think of a topic as a highway, partitions as lanes.

Topic: "raw-detections" with 4 partitions

Partition 0: [cam_01 msg] [cam_01 msg] [cam_01 msg] ...
Partition 1: [cam_02 msg] [cam_02 msg] ...
Partition 2: [cam_03 msg] ...
Partition 3: [cam_04 msg] ...

- Messages with the same key always go to the same partition (ordering guaranteed per key)
- In ArgusV: use camera_id as the key → all events from one camera stay ordered

---

3. Producer

A producer writes messages to a topic. That's it. It doesn't know or care who reads them.

# Conceptually what edge-gateway does

producer.send(
topic="raw-detections",
key="cam_01", # determines which partition
value={ # the actual message
"camera_id": "cam_01",
"detected_objects": ["person", "car"],
"timestamp": "..."
}
)

---

4. Consumer + Consumer Group

A consumer reads messages from a topic. A consumer group is a set of consumers that share the work.

Topic "raw-detections" (4 partitions)
Consumer Group "stream-ingestion-group" (2 instances)

Partition 0 ──► Consumer Instance A
Partition 1 ──► Consumer Instance A
Partition 2 ──► Consumer Instance B
Partition 3 ──► Consumer Instance B

Key rules:

- Each partition is assigned to exactly one consumer in a group at a time
- Kafka tracks offset — which message each group last processed
- If Consumer B crashes, Kafka reassigns its partitions to A automatically (rebalance)
- Different groups read independently — notification-group and actuation-group both read actions without interfering

---

5. Offset

An offset is just a number — the position of a message in a partition.

Partition 0: [0] [1] [2] [3] [4] [5] ...
▲
stream-ingestion-group is here (committed offset=3)
next time it starts, it resumes from 4

- Auto-commit (default): consumer commits offset every 5 seconds — risk of processing a message twice if it crashes mid-batch
- Manual commit: you commit only after successfully processing — safer for ArgusV

---

Kafka vs. a Queue (like RabbitMQ)

┌────────────────────┬───────────────────────────────┬─────────────────────────────────────────┐
│ │ Traditional Queue │ Kafka │
├────────────────────┼───────────────────────────────┼─────────────────────────────────────────┤
│ After consuming │ Message is deleted │ Message stays (until retention expires) │
├────────────────────┼───────────────────────────────┼─────────────────────────────────────────┤
│ Multiple consumers │ Each gets a different message │ Each group gets ALL messages │
├────────────────────┼───────────────────────────────┼─────────────────────────────────────────┤
│ Replay │ Not possible │ Yes — reset offset to replay │
├────────────────────┼───────────────────────────────┼─────────────────────────────────────────┤
│ Order guarantee │ Per-queue │ Per-partition │
├────────────────────┼───────────────────────────────┼─────────────────────────────────────────┤
│ Throughput │ Moderate │ Very high (millions/sec) │
└────────────────────┴───────────────────────────────┴─────────────────────────────────────────┘

In ArgusV, both notification and actuation need to read the actions topic. A queue couldn't do this — Kafka handles it natively via consumer groups.

---

Zookeeper (Why It Exists in Your docker-compose)

Kafka traditionally uses Zookeeper to:

- Track which brokers are alive
- Store topic metadata and partition assignments
- Manage leader election

Your docker-compose has both zookeeper and kafka — this is the standard setup for Kafka 7.5.x (Confluent). Newer Kafka (KRaft mode) eliminates Zookeeper, but for your use case this is fine.

---

How the ArgusV Pipeline Flows Through Kafka

[Camera RTSP Stream]
│
▼
edge-gateway
(YOLOv8 detects "person")
│
│ produces to
▼
┌─────────────────┐
│ raw-detections │ {"camera_id":"cam_01", "objects":["person"], ...}
└─────────────────┘
│
│ consumed by
▼
stream-ingestion
(grabs 3 frames from RTSP, uploads to MinIO)
│
│ produces to
▼
┌──────────────┐
│ vlm-requests │ {"event_id":"...", "frame_urls":["minio://..."], ...}
└──────────────┘
│
│ consumed by
▼
vlm-inference
(sends frames to GPT-4o: "is this person a threat?")
│
│ produces to
▼
┌─────────────┐
│ vlm-results │ {"threat_level":"HIGH", "summary":"Unauthorized entry", ...}
└─────────────┘
│
│ consumed by
▼
decision-engine
(checks rules: zone restricted? yes → create incident → alert)
│
│ produces to
▼
┌─────────┐
│ actions │ {"action_type":"alert", "target":"#security-channel", ...}
└─────────┘
│
├─── consumed by notification (sends Slack/SMS)
│
└─── consumed by actuation (triggers door lock via MQTT)

And separately, whenever a zone/rule changes:

    decision-engine
          │ produces to
          ▼
    ┌────────────────┐
    │ config-updates │
    └────────────────┘
          │
          ├─── consumed by edge-gateway     (hot-reload which zones to watch)
          └─── consumed by stream-ingestion (update frame extraction logic)

---

Key Terms Cheat Sheet

┌────────────────┬──────────────────────────────────────────────┬──────────────────────────────────────────────┐
│ Term │ Simple Definition │ ArgusV Example │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Topic │ Named message channel │ raw-detections │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Partition │ Sub-lane of a topic for parallelism │ 4 partitions, one per camera group │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Offset │ Message position number in a partition │ "process from offset 42" │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Producer │ Service that writes to Kafka │ edge-gateway │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Consumer │ Service that reads from Kafka │ stream-ingestion │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Consumer Group │ Set of consumers sharing work │ stream-ingestion-group │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Broker │ The Kafka server itself │ kafka:29092 in docker-compose │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Retention │ How long messages are kept │ 1h for raw-detections, 7d for config-updates │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Commit │ Saving your read position │ "I've processed up to offset 42" │
├────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ DLQ │ Dead letter queue — where failed messages go │ raw-detections.dlq │
└────────────────┴──────────────────────────────────────────────┴──────────────────────────────────────────────┘

---

What aiokafka vs confluent-kafka Means in Your Code

Your services use two different Kafka client libraries:

- confluent-kafka (edge-gateway) — C-based, very high performance, sync API. Good for the high-frequency detection producer.
- aiokafka (all other services) — pure Python, async/await compatible. Works naturally with FastAPI's async event loop.

---
