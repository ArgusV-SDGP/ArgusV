# Service Specification: Notification Service (`notification-service`)

> **Status**: DRAFT  
> **Type**: Microservice (Backend/Cloud)  
> **Language**: Python 3.11  
> **Responsibility**: Dispatching alerts to humans via multiple channels (Slack, SMS, Email).

---

## 1. Business Logic & Responsibility
The `notification-service` is the **voice** of the system. It ensures the right person knows about an incident at the right time. It handles routing logic (who is on call?) and formatting (sending an image to Slack but just text to SMS).

### Core Capabilities:
1.  **Multi-Channel Dispatch**: 
    -   **Slack**: Rich layout with Image Preview and "Acknowledge" button.
    -   **Twilio (SMS/Voice)**: Critical wake-up calls for high severity.
    -   **Email**: Low priority daily summaries.
2.  **Rate Limiting**: specific rules to avoid spamming (e.g., max 1 SMS per 5 mins per zone).
3.  **Template Rendering**: Dynamically inserting Zone Name, Time, and Threat Level into messages.

---

## 2. Engineering Requirements

### 2.1 Inputs & Outputs
-   **Input**: Kafka `notifications` topic.
-   **Output**: External API calls (Slack Webhook, Twilio API).

### 2.2 Technical Stack
-   **Framework**: Python (FastAPI BackgroundTasks or simple Consumer).
-   **Libraries**: `slack_sdk`, `twilio`.
-   **Template Engine**: `Jinja2` for message formatting.

---

## 3. Data Structures (Topic: `notifications`)

```json
{
  "incident_id": "inc_555",
  "severity": "HIGH",
  "channels": ["slack", "sms"],
  "payload": {
    "message": "Intruder detected at Loading Dock.",
    "image_url": "https://minio.../frame.jpg",
    "metadata": { "zone": "Dock", "confidence": 0.98 }
  }
}
```

---

## 4. MVP Implementation Steps
1.  **Secrets**: Load SLACK_BOT_TOKEN and TWILIO_SID from env vars.
2.  **Slack Client**: Implement `send_block_kit_message(channel, incident)`.
3.  **Rate Limiter**: Simple Redis key `last_sms_sent:{zone_id}` with TTL.
4.  **Dispatcher**: Switch statement based on `channels` list in incoming message.
