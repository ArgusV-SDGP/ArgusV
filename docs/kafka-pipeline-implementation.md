# Kafka Pipeline Implementation

**Branch:** `1-adding-basic-kafka-flow`

This document describes the changes made to wire up the end-to-end Kafka message pipeline across all ArgusV microservices. Prior to these changes, every service was a skeleton stub with no Kafka integration. The infrastructure (broker, topics, schemas) was already in place.

---

## What Was Already Done

| Item | Location | Status |
|---|---|---|
| Zookeeper + Kafka broker | `docker-compose.dev.yml` | Done |
| Topic auto-creation (`kafka-init`) | `docker-compose.dev.yml` | Done |
| Pydantic message schemas | `libs/shared/kafka_schemas.py` | Done |

---

## Changes Made

### 1. `services/edge-gateway/src/main.py`

**Was:** A bare `while True` loop printing a heartbeat message.

**Now:** A `confluent-kafka` producer that publishes `RawDetection` messages to the `raw-detections` topic every 5 seconds.

Key details:
- Reads `KAFKA_BOOTSTRAP_SERVERS` from environment (set to `kafka:29092` in docker-compose).
- Uses `camera_id` as the Kafka partition key so all events from the same camera land on the same partition (preserving order per camera).
- Registers an `on_delivery` callback that logs the partition and offset on success, or the error on failure.
- Simulates three cameras (`cam-01`, `cam-02`, `cam-03`) across three zones, picking 1–3 random objects from a pool each cycle.
- Handles `SIGINT`/`SIGTERM` gracefully — calls `producer.flush()` before exit so no in-flight messages are dropped.

---

### 2. `services/stream-ingestion/src/main.py`

**Was:** A FastAPI skeleton with no lifespan logic.

**Now:** A FastAPI app with a background `aiokafka` consumer on `raw-detections` that forwards each message as a `VlmRequest` to `vlm-requests`.

Key details:
- Consumer group: `stream-ingestion-group`.
- Deserializes incoming JSON into the `RawDetection` Pydantic model for schema validation on every message.
- Wraps the detection in a `VlmRequest` and produces it to `vlm-requests`. `frame_urls` is left empty — this is the hook for MinIO frame upload once that layer is wired in.
- Uses a `wait_for_kafka` probe-producer pattern (10 retries, 3s apart) so startup order in docker-compose doesn't cause crashes.
- Consumer and producer are stopped cleanly in the `finally` block when the FastAPI lifespan exits.

---

### 3. `services/vlm-inference/src/main.py`

**Was:** A FastAPI skeleton with no lifespan logic.

**Now:** A FastAPI app with a background `aiokafka` consumer on `vlm-requests` that runs stub inference and produces `VlmResult` messages to `vlm-results`.

Key details:
- Consumer group: `vlm-inference-group`.
- Stub inference (`stub_infer`) classifies threat level based on detected object names rather than making a real OpenAI Vision API call. This keeps the pipeline runnable without an API key and makes the output deterministic enough to test downstream services:
  - Objects in `HIGH_RISK_OBJECTS` (weapon, knife, gun) → `HIGH`
  - Objects in `MEDIUM_RISK_OBJECTS` (backpack, luggage, bag) → `MEDIUM`
  - Everything else → `LOW`
- The real GPT-4o Vision call slots in here once `frame_urls` is populated by stream-ingestion.
- Same retry-on-startup and clean shutdown pattern as stream-ingestion.

---

### 4. `services/decision-engine/src/main.py`

**Was:** A FastAPI app with REST endpoints for zone/rule/RAG config management, but no Kafka integration and no `lifespan` context.

**Now:** Same REST API, plus a background `aiokafka` consumer on `vlm-results` that produces `Action` messages to `actions` for HIGH and CRITICAL threats.

Key details:
- Consumer group: `decision-engine-group`.
- Stub rule: only `HIGH` and `CRITICAL` threat levels trigger an action. `LOW` and `MEDIUM` are logged and skipped. This is where real rule evaluation (loaded from Postgres/Redis via `ConfigManager`) plugs in later.
- Produced `Action` has `action_type="alert"` and `target="#security-alerts"` — the notification service reads this.
- All existing REST endpoints (`POST /api/zones`, `GET /api/zones`, `POST /api/rules`, `POST /api/rag/config`) are preserved exactly as they were.
- Added `lifespan` context manager to host the consumer task alongside the HTTP server.

---

### 5. `services/notification/src/main.py`

**Was:** A FastAPI skeleton with no lifespan logic.

**Now:** A FastAPI app with a background `aiokafka` consumer on `actions` that logs alert actions.

Key details:
- Consumer group: `notification-group`.
- Filters for `action_type == "alert"` and logs the threat level, summary, and target channel.
- Slack (`slack_sdk`) and SMS (Twilio) calls are stubbed with `# TODO` comments at the exact integration point.

---

### 6. `services/actuation/src/main.py`

**Was:** A bare `while True` loop printing a heartbeat message.

**Now:** An `asyncio`-based `aiokafka` consumer on `actions` that handles `action_type == "actuate"` messages.

Key details:
- Runs as a plain Python asyncio script (no FastAPI — this service has no HTTP API to expose).
- Filters for `action_type == "actuate"` only; alert-type actions are skipped with a log line.
- MQTT publish via `paho-mqtt` to Mosquitto is stubbed with a `# TODO` comment at the exact integration point.
- `SIGINT`/`SIGTERM` handled via `asyncio` event to cancel the consumer task cleanly.

---

### 7. `services/actuation/pyproject.toml`

Added two missing dependencies:
- `aiokafka` — required for the Kafka consumer implemented above.
- `pydantic` — required to deserialize `Action` messages from the shared schema.

---

## Full Message Flow

```
edge-gateway
  │  produces RawDetection (every 5s, simulated cameras)
  ▼
[raw-detections topic]
  │
  ▼
stream-ingestion
  │  validates RawDetection, wraps as VlmRequest (frame_urls empty for now)
  ▼
[vlm-requests topic]
  │
  ▼
vlm-inference
  │  stub inference → classifies threat level
  ▼
[vlm-results topic]
  │
  ▼
decision-engine
  │  HIGH/CRITICAL → produce Action("alert")
  │  LOW/MEDIUM   → log only, no action
  ▼
[actions topic]
  │
  ├──▶ notification  (action_type="alert"   → log / TODO: Slack + SMS)
  └──▶ actuation     (action_type="actuate" → log / TODO: MQTT publish)
```

---

## Running the Smoke Test

```bash
docker compose -f docker-compose.dev.yml up \
    zookeeper kafka kafka-init \
    edge-gateway stream-ingestion vlm-inference \
    decision-engine notification actuation
```

Expected log output (interleaved across containers):

```
argus-edge-gateway       | [Producer] Sent event_id=... | camera=cam-02 | objects=['person', 'backpack']
argus-stream-ingestion   | [Consumer] Received event_id=... camera=cam-02 objects=['person', 'backpack']
argus-stream-ingestion   | [Consumer] Forwarded VlmRequest event_id=...
argus-vlm-inference      | [vlm-inference] Processing event_id=... objects=['person', 'backpack']
argus-vlm-inference      | [vlm-inference] Result ... threat=MEDIUM → 'alert_operator'
argus-decision-engine    | [decision-engine] Received event_id=... threat=MEDIUM
argus-decision-engine    | [decision-engine] threat=MEDIUM — no action required

# If edge-gateway happens to pick a high-risk object:
argus-vlm-inference      | [vlm-inference] Result ... threat=HIGH → 'dispatch_security'
argus-decision-engine    | [decision-engine] Action dispatched action_id=... threat=HIGH
argus-notification       | [notification] ALERT | threat=HIGH | target=#security-alerts | ...
```

---

## What Is Not Yet Wired In

| Feature | Where to add it |
|---|---|
| MinIO frame upload | `stream-ingestion` — populate `frame_urls` in `VlmRequest` |
| Real GPT-4o Vision call | `vlm-inference` — replace `stub_infer()` with OpenAI API call using `frame_urls` |
| Rule-based decision logic | `decision-engine` — load rules from Redis/Postgres via `ConfigManager` |
| Slack notifications | `notification` — call `slack_sdk` where the `# TODO` comment is |
| SMS notifications | `notification` — call Twilio where the `# TODO` comment is |
| MQTT device control | `actuation` — call `paho-mqtt` where the `# TODO` comment is |
| Dead Letter Queue handling | All services — route failed messages to `*.dlq` topics |
