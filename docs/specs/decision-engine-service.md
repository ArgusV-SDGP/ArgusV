# Service Specification: Decision Engine Service (`decision-engine-service`)

> **Status**: DRAFT  
> **Type**: Microservice (Backend/Logic)  
> **Language**: Python 3.11  
> **Responsibility**: Rules execution, action orchestration, and RAG indexing.

---

## 1. Business Logic & Responsibility
The `decision-engine-service` is the **executor**. It takes the high-level analysis from the VLM and decides *what to do* based on pre-configured rules. It is also responsible for "remembering" the event by indexing it for the RAG system.

### Core Capabilities:
1.  **Rule Evaluation**: Checks `security-decisions` against User Rules (e.g., "If Severity=High AND Detect=Person AND Time=Night → Trigger Siren").
2.  **Actuation Dispatch**: Sends commands to the `actuation-service` (MQTT) and `notification-service`.
3.  **RAG Indexing (Async)**: Indexes the VLM summary into Qdrant for future chat queries. **critical:** This must be a background task to not block alerts.
4.  **Logging**: Persists the final incident record to PostgreSQL.

---

## 2. Engineering Requirements

### 2.1 Inputs & Outputs
-   **Input**: Kafka `security-decisions` (JSON from VLM).
-   **Output**: 
    -   Kafka `actions` (for actuators).
    -   Kafka `notifications` (for user alerts).
    -   Qdrant Vector DB (Upsert embedding).
    -   PostgreSQL (Insert Incident).

### 2.2 Technical Stack
-   **Framework**: Python (FastAPI background tasks or standalone consumer).
-   **Database**: `SQLAlchemy` (Postgres ORM), `Qdrant Client` (Vector DB).
-   **Embedding Model**: `OpenAI text-embedding-3-small` (for creating vector from VLM description).

---

## 3. Data Structures (Topic: `actions`)

```json
{
  "action_id": "act_999",
  "type": "TRIGGER_IOT",
  "target": "siren_01",
  "duration": 30,
  "reason": "High severity threat detected by VLM"
}
```

---

## 4. MVP Implementation Steps
1.  **Rule Engine**: Simple function `evaluate_rules(incident)` that returns list of actions.
2.  **FastAPI BackgroundTasks**: Implement RAG indexing as a `BackgroundTasks` function so the API returns/acknowledges the alert immediately.
3.  **DB Writer**: Write the full incident lifecycle (frames, VLM analysis, taken actions) to Postgres.
4.  **Embedder (Background)**: 
    -   Text: *"Person wearing balaclava attempting to pry open rear door."*
    -   Vector: `[0.1, -0.5, ...]`
    -   Upsert to Qdrant collection `incidents`.

---

## 5. Configuration API (Consumed by Dashboard)

The Dashboard uses these endpoints to manage the system's "brain" dynamically.

### 5.1 Notification Rules
-   `GET /api/rules`: List all active notification logic.
-   `POST /api/rules`: Create or update a rule.
    ```json
    {
      "zone_id": "loading_dock",
      "severity": "HIGH",
      "channels": ["slack", "sms"],
      "config": { "sms_to": "+15550001" }
    }
    ```
-   `DELETE /api/rules/{rule_id}`: Remove a rule.

### 5.2 RAG Configuration
-   `GET /api/rag/config`: Fetch current AI settings.
-   `POST /api/rag/config`: Update system prompts or retrieval limits.
    ```json
    {
      "key": "system_prompt",
      "value": "You are a witty security guard..."
    }
    ```

