# Kafka — Remaining Work

The basic pipeline is wired end-to-end (edge-gateway → stream-ingestion → vlm-inference → decision-engine → notification/actuation). Everything below is what's left to make it production-ready.

---

## 1. `config-updates` Topic (not started)

The `config-updates` topic exists but nothing reads or writes to it over Kafka yet.

- [ ] **decision-engine** — produce a `ConfigUpdate` message to `config-updates` whenever a zone or rule is created/updated/deleted via the REST API. Hook into `ConfigManager.sync_zone_to_redis` and `sync_rule_to_redis` — add a Kafka produce call alongside the existing Redis publish. Use the `ConfigUpdate` schema from `libs/shared/kafka_schemas.py`.

- [ ] **edge-gateway** — add a `confluent-kafka` Consumer for `config-updates` in `config_loader.py`. Run it in a background thread (consistent with the existing threading pattern). On message receipt, invalidate and re-fetch the affected zone/rule from Redis into `active_rules`.

- [ ] **stream-ingestion** — add a second `aiokafka` consumer task for `config-updates` in the FastAPI lifespan, alongside the existing `raw-detections` consumer. On message receipt, call the existing cache invalidation logic in `StreamConfigLoader`.

---

## 2. Resilience & Error Handling (Phase 4)

### 2a. DLQ routing

All consumers currently `print` on error and move on — messages are silently lost.

- [ ] On processing failure in each consumer, publish the original raw message bytes + error metadata (`error`, `timestamp`, `source_topic`, `partition`, `offset`) to the corresponding `.dlq` topic:
  - `stream-ingestion` → `raw-detections.dlq`
  - `vlm-inference` → `vlm-requests.dlq`
  - `decision-engine` → `vlm-results.dlq`
  - `notification` → `actions.dlq`
  - `actuation` → `actions.dlq`

  All `.dlq` topics are already created by `kafka-init`.

### 2b. Manual offset commit

All consumers use `enable_auto_commit=True`, which risks processing a message twice or losing it on crash.

- [ ] Switch all `aiokafka` consumers to `enable_auto_commit=False`.
- [ ] Call `await consumer.commit()` explicitly only after successful processing (or after successfully routing to DLQ on failure).
- [ ] Applies to: `stream-ingestion`, `vlm-inference`, `decision-engine`, `notification`, `actuation`.

### 2c. Producer durability settings

High-value producers should not silently drop messages.

- [ ] **`vlm-inference`** producer (→ `vlm-results`) and **`decision-engine`** producer (→ `actions`): set `acks="all"`, `enable_idempotence=True` on the `AIOKafkaProducer`.
- [ ] **`edge-gateway`** producer (→ `raw-detections`): set `acks=all`, `retries=5`, `retry.backoff.ms=500`, `enable.idempotence=True` on the `confluent_kafka.Producer` config dict.

### 2d. Exponential backoff for transient failures

- [ ] Write a small retry helper that catches transient exceptions (rate limit errors, timeout errors, connection errors) and retries up to N times with increasing delay (1s → 2s → 4s → 8s) before giving up and routing to DLQ.
- [ ] Apply to **`vlm-inference`** (OpenAI API calls) and **`stream-ingestion`** (MinIO uploads) at minimum.

---

## 3. Observability (Phase 5)

### 3a. Kafka health check in `/health` endpoints

All FastAPI services return `{"status": "healthy"}` unconditionally.

- [ ] Track consumer liveness in a module-level flag (e.g. `kafka_healthy: bool`) that the consumer loop sets to `True` on connect and `False` if it exits unexpectedly.
- [ ] Expose it in the `/health` response and return HTTP 503 if unhealthy.
- [ ] Applies to: `stream-ingestion`, `vlm-inference`, `decision-engine`, `notification`.

### 3b. kafka-ui

- [ ] Add `provectuslabs/kafka-ui` to `docker-compose.dev.yml` on port `8080`. Gives a visual browser for topics, messages, consumer group lag, and partition assignments during development.

  ```yaml
  kafka-ui:
    image: provectuslabs/kafka-ui:latest
    ports:
      - "8080:8080"
    environment:
      KAFKA_CLUSTERS_0_NAME: argus-local
      KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS: kafka:29092
    depends_on:
      - kafka
  ```

---

## Priority Order

1. `config-updates` topic — completes the planned architecture
2. DLQ routing — prevents silent data loss
3. Manual offset commit — pairs with DLQ for at-least-once delivery
4. Producer durability — hardens the two most critical topics
5. kafka-ui — makes debugging everything above much easier
6. Exponential backoff — needed once real OpenAI/MinIO calls are in
7. Health check — polish, but useful for docker-compose `depends_on: condition: service_healthy`
