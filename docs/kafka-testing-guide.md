# Kafka Pipeline — Testing Guide

This guide covers how to verify that the end-to-end Kafka pipeline is working correctly. All tests assume Docker and Docker Compose are installed.

---

## Prerequisites

Copy the env file before starting anything:

```bash
cp .env.example .env
```

The API keys (`OPENAI_API_KEY`, `SLACK_BOT_TOKEN`, etc.) are not needed for any of the tests below — the pipeline is fully stubbed. Leave them as placeholders.

---

## 1. Smoke Test — Full Pipeline in One Command

The fastest way to verify everything works end-to-end.

```bash
docker compose -f docker-compose.dev.yml up \
    zookeeper kafka kafka-init \
    edge-gateway stream-ingestion vlm-inference \
    decision-engine notification actuation \
    --build
```

### What to look for

Watch the logs stream. Within ~30 seconds of startup you should see this pattern repeating:

```
argus-edge-gateway       | [Producer] Connected to Kafka at kafka:29092
argus-edge-gateway       | [Producer] Sent event_id=<uuid> | camera=cam-02 | objects=['person', 'backpack']
argus-edge-gateway       | [Producer] Delivered to raw-detections [partition=1, offset=0]

argus-stream-ingestion   | [stream-ingestion] Received event_id=<uuid> camera=cam-02 objects=['person', 'backpack']
argus-stream-ingestion   | [stream-ingestion] Forwarded VlmRequest event_id=<uuid>

argus-vlm-inference      | [vlm-inference] Processing event_id=<uuid> objects=['person', 'backpack']
argus-vlm-inference      | [vlm-inference] Result ... threat=MEDIUM confidence=0.74 → 'alert_operator'

argus-decision-engine    | [decision-engine] Received event_id=<uuid> threat=MEDIUM
argus-decision-engine    | [decision-engine] threat=MEDIUM — no action required
```

When `edge-gateway` happens to pick a high-risk object from the pool:

```
argus-vlm-inference      | [vlm-inference] Result ... threat=HIGH → 'dispatch_security'
argus-decision-engine    | [decision-engine] Action dispatched action_id=<uuid> threat=HIGH target=#security-alerts
argus-notification       | [notification] ALERT | action_id=<uuid> threat=HIGH | Unauthorized entry...
```

### Pass criteria

| Check | Expected |
|---|---|
| `kafka-init` exits cleanly | `argus-kafka-init exited with code 0` |
| edge-gateway produces | `Delivered to raw-detections` log lines appear |
| stream-ingestion receives | Logs show same `event_id` as edge-gateway |
| vlm-inference classifies | Logs show `threat=LOW/MEDIUM/HIGH` |
| decision-engine decides | HIGH threats show `Action dispatched`, others show `no action required` |
| notification receives | `ALERT` log lines appear for HIGH/CRITICAL threats |

---

## 2. Verify Topics Were Created

Run this after `kafka-init` completes to confirm all topics exist:

```bash
docker compose -f docker-compose.dev.yml exec kafka \
    kafka-topics --bootstrap-server kafka:29092 --list
```

Expected output (order may vary):

```
actions
actions.dlq
config-updates
raw-detections
raw-detections.dlq
vlm-requests
vlm-requests.dlq
vlm-results
vlm-results.dlq
```

---

## 3. Inspect Messages on a Topic

Read messages directly off any topic using the Kafka console consumer. Useful for confirming what a producer is actually sending.

**Watch `raw-detections` (edge-gateway output):**

```bash
docker compose -f docker-compose.dev.yml exec kafka \
    kafka-console-consumer \
    --bootstrap-server kafka:29092 \
    --topic raw-detections \
    --from-beginning
```

**Watch `vlm-results` (vlm-inference output):**

```bash
docker compose -f docker-compose.dev.yml exec kafka \
    kafka-console-consumer \
    --bootstrap-server kafka:29092 \
    --topic vlm-results \
    --from-beginning
```

**Watch `actions` (decision-engine output):**

```bash
docker compose -f docker-compose.dev.yml exec kafka \
    kafka-console-consumer \
    --bootstrap-server kafka:29092 \
    --topic actions \
    --from-beginning
```

Each message is a JSON blob matching the Pydantic schemas in `libs/shared/kafka_schemas.py`. Press `Ctrl+C` to stop.

---

## 4. Check Consumer Group Lag

Consumer group lag tells you how far behind a consumer is. Zero lag means it's keeping up. Growing lag means it's falling behind.

```bash
docker compose -f docker-compose.dev.yml exec kafka \
    kafka-consumer-groups \
    --bootstrap-server kafka:29092 \
    --describe \
    --all-groups
```

Expected output (lag should be 0 or very low for all groups):

```
GROUP                    TOPIC            PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
stream-ingestion-group   raw-detections   0          12              12              0
stream-ingestion-group   raw-detections   1          8               8               0
...
vlm-inference-group      vlm-requests     0          12              12              0
...
decision-engine-group    vlm-results      0          12              12              0
...
notification-group       actions          0          3               3               0
actuation-group          actions          0          3               3               0
```

---

## 5. Test a Single Service in Isolation

### Produce a test message manually

You can inject a message directly into any topic without running the upstream service. Useful for testing a single consumer in isolation.

**Inject a `RawDetection` into `raw-detections`:**

```bash
docker compose -f docker-compose.dev.yml exec kafka \
    kafka-console-producer \
    --bootstrap-server kafka:29092 \
    --topic raw-detections
```

Then paste this JSON and press Enter:

```json
{"event_id":"test-001","camera_id":"cam-01","zone_id":"zone-entrance","detected_objects":["person","backpack"],"confidence_scores":[0.92,0.87],"frame_timestamp":"2024-01-01T00:00:00Z","produced_at":"2024-01-01T00:00:00Z"}
```

`stream-ingestion` should immediately log that it received `event_id=test-001`.

**Inject a high-risk `VlmResult` into `vlm-results`** (to trigger an action without waiting for the full chain):

```json
{"event_id":"test-002","threat_level":"HIGH","summary":"Unauthorized entry detected","confidence":0.95,"recommended_action":"dispatch_security","processed_at":"2024-01-01T00:00:00Z"}
```

`decision-engine` should dispatch an action and `notification` should log an ALERT.

---

## 6. Test Individual Service Health Endpoints

Each FastAPI service exposes `/health`. Confirm they respond after startup:

```bash
# stream-ingestion (no port exposed by default — exec in)
docker compose -f docker-compose.dev.yml exec stream-ingestion \
    curl -s http://localhost:8000/health

# vlm-inference
docker compose -f docker-compose.dev.yml exec vlm-inference \
    curl -s http://localhost:8000/health

# decision-engine
docker compose -f docker-compose.dev.yml exec decision-engine \
    curl -s http://localhost:8000/health

# notification
docker compose -f docker-compose.dev.yml exec notification \
    curl -s http://localhost:8000/health
```

Expected response from each:

```json
{"status": "healthy", "service": "<service-name>"}
```

---

## 7. Test Restart Resilience

Verify that a consumer reconnects cleanly after a restart and picks up where it left off.

```bash
# Let the pipeline run for 30 seconds to accumulate some messages, then restart one service
docker compose -f docker-compose.dev.yml restart stream-ingestion
```

Watch the logs — `stream-ingestion` should reconnect to Kafka, log its consumer group and topic, and continue processing without losing any messages (because `auto_offset_reset=earliest` and offsets are tracked by the broker per consumer group).

---

## 8. Tear Down

```bash
# Stop all services but keep volumes (Kafka data, Postgres, etc.)
docker compose -f docker-compose.dev.yml down

# Stop and wipe all volumes (clean slate)
docker compose -f docker-compose.dev.yml down -v
```

Use `-v` when you want to reset topic offsets and start fresh.

---

## Common Issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `kafka-init` keeps restarting | Kafka broker not ready yet | It retries automatically — wait 30s |
| `[Consumer] Kafka not ready (attempt N/10)` | Service started before broker was ready | Also retries automatically — wait |
| Consumer receives nothing | Topic has no messages yet | Check edge-gateway logs for delivery confirmations |
| `NoBrokersAvailable` error | Wrong `KAFKA_BOOTSTRAP_SERVERS` value | Should be `kafka:29092` inside Docker, `localhost:9092` outside |
| `event_id` in stream-ingestion doesn't match edge-gateway | Messages going to different topic or wrong group | Check topic name in logs |
| `actuation` shows "Skipping action_type='alert'" | Expected — actuation only handles `actuate` type, notification handles `alert` | Not a bug |
