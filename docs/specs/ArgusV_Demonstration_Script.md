# ArgusV — End-to-End System Demonstration Script 🎬

> **Goal**: Validate the integrated architecture by simulating a complete "Intruder" incident lifecycle, from camera detection to RAG chat query.

---

## Phase 1: System Startup (The "Pre-Flight" Check)

Before running the scenario, ensure all 7 microservices are healthy.

### 1.1 Start Infrastructure
```bash
docker-compose up -d
# Wait 30s for Kafka & Postgres to stabilize
```

### 1.2 Verify Service Health
Run `docker ps` and check for these containers:
*   `argus-edge-gateway` (Simulating Camera 1)
*   `argus-stream-ingestion`
*   `argus-vlm-inference`
*   `argus-decision-engine`
*   `argus-actuation`
*   `argus-notification`
*   `argus-rag-chat`
*   `argus-dashboard` (Frontend)

---

## Phase 2: The "Intruder" Scenario (Real-Time Flow)

**Scenario**: An unauthorized person enters the "Loading Dock" (Zone A) at night.

### Step 1: Trigger the Edge (Eyes) 👀
*   **Action**: Play the simulation video file `test_intruder.mp4` into the RTSP simulator.
*   **Observation (Logs)**:
    *   `edge-gateway`: `[INFO] Object Detected: PERSON (Conf: 0.88) in Zone: LOADING_DOCK`
    *   `edge-gateway`: `[INFO] Published to topic `raw-detections``

### Step 2: Ingestion & Enrichment (State) 📸
*   **Observation (Logs)**:
    *   `stream-ingestion`: `[INFO] New Incident INC-001 created for Zone A.`
    *   `stream-ingestion`: `[INFO] Capturing 3 frames...`
    *   `stream-ingestion`: `[INFO] Uploaded frame_1.jpg to MinIO (bucket: argus-frames).`
    *   `stream-ingestion`: `[INFO] Published to topic `vlm-requests``

### Step 3: VLM Analysis (Brain) 🧠
*   **Observation (Logs)**:
    *   `vlm-inference`: `[INFO] Processing INC-001 with GPT-4o.`
    *   `vlm-inference`: `[INFO] Analysis Result: { "severity": "HIGH", "reason": "Person loitering near secure door", "action": "WARN" }`
    *   `vlm-inference`: `[INFO] Published to topic `security-decisions``

### Step 4: Decision & Actuation (Hands) 🚨
*   **Observation (Logs)**:
    *   `decision-engine`: `[INFO] Rule Match: HIGH Severity + Night Time = ACTION: TRIGGER_SIREN`
    *   `actuation-service`: `[INFO] Received Command: TURN_ON Siren_01 for 30s.`
    *   `actuation-service`: `[INFO] GPIO_17 set to HIGH.`

### Step 5: User Notification (Voice) 📱
*   **Action**: Check Slack channel `#security-alerts`.
*   **Verification**:
    *   Message received: "🚨 **Intruder at Loading Dock**"
    *   Image attachment visible.
    *   "Acknowledge" button present.

### Step 6: Frontend Live Update 🖥️
*   **Action**: Open `http://localhost:3000` (Dashboard).
*   **Verification**:
    *   **Global Alert Bell**: Shows badge "1".
    *   **Incident Feed**: New row "INC-001 | High | Loading Dock | Just now".
    *   **Live View**: Clicking the incident opens the video player with the recorded clip.

---

## Phase 3: The "Forensic" Scenario (Memory & RAG) 🕵️‍♂️

**Scenario**: The events are over. The Security Chief wants to investigate what happened.

### Step 1: Open RAG Chat
*   **Action**: Navigate to `http://localhost:3000/chat`.

### Step 2: Natural Language Query
*   **Input**: *"Did we have any intruders at the loading dock last night?"*
*   **System Process**:
    1.  `rag-chat-service` coverts query to vector.
    2.  Queries Qdrant for "intruder" + "loading dock" + "last night".
    3.  Retrieves `INC-001` summary.
    4.  LLM generates response.

### Step 3: Verify Response
*   **Output**: 
    > "Yes, at 22:15 last night, a person was detected loitering near the secure door at the Loading Dock. The system triggered a siren and sent a Slack alert. (Source: INC-001)"

---

## Phase 4: Troubleshooting Guide 🔧

| Symptom | Likely Cause | Fix |
|:---|:---|:---|
| **No Edge Detection** | RTSP stream dead or Simulator crashed. | Check `edge-gateway` logs; restart RTSP sim. |
| **VLM Timeout** | OpenAI API key missing/invalid. | Check `.env` file for `OPENAI_API_KEY`. |
| **No Slack Alert** | Webhook URL invalid. | Verify `SLACK_WEBHOOK_URL` in `notification-service`. |
| **Chat says "I don't know"** | RAG Indexing failed (Background Task). | Check `decision-engine` logs for Qdrant connection errors. |

---

*Use this script to verify the system end-to-end after every major deployment.*
