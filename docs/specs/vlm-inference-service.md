# Service Specification: VLM Inference Service (`vlm-inference-service`)

> **Status**: DRAFT  
> **Type**: Microservice (Backend/AI)  
> **Language**: Python 3.11  
> **Responsibility**: Contextual understanding and reasoning using Vision Language Models.

---

## 1. Business Logic & Responsibility
This is the **brain** of the system. It replaces simple motion detection with "intelligence". It answers the question: "Is this person actually a threat, or just passing by?"

### Core Capabilities:
1.  **Visual Reasoning**: Analyzes images to understand behavior (Loitering vs. Delivery vs. Staff).
2.  **Prompt Engineering**: Uses specific system prompts to enforce security policies.
3.  **Structured Output**: Returns strict JSON classifications (Threat Level, Description, Action Recommendation) instead of free text.

---

## 2. Engineering Requirements

### 2.1 Inputs & Outputs
-   **Input**: Kafka `vlm-requests` (contains Image URLs).
-   **Output**: Kafka `security-decisions` (contains AI analysis).

### 2.2 Technical Stack
-   **Primary Model**: GPT-4o (via OpenAI API).
-   **Fallback Model**: Claude 3.5 Sonnet (via Anthropic API) if primary fails.
-   **Library**: `LangChain` or native OpenAI SDK.

### 2.3 Prompt Strategy (System Prompt)
*"You are Argus, an automated security guard. Analyze the attached images. Determine if the person is a threat based on:
1. Loitering (staying in one place > 30s).
2. Unauthorized area access.
3. Suspicious behavior (peering in windows, masking face).
Return JSON: { "severity": "low|medium|high", "reason": "...", "action": "voice_warning|police" }."*

---

## 3. Data Structures (Topic: `security-decisions`)

```json
{
  "incident_id": "inc_555",
  "analysis": {
    "severity": "HIGH",
    "description": "Person wearing balaclava attempting to pry open rear door.",
    "confidence": 0.95,
    "recommended_action": "TRIGGER_SIREN"
  },
  "raw_response": "..."
}
```

---

## 4. MVP Implementation Steps
1.  **API Client**: Setup `AsyncOpenAI` client with retry logic.
2.  **Cost Control**: Implement token usage tracking per request.
3.  **Image Prep**: Function to download images from MinIO and encode as base64 (if API requires) or pass URL.
4.  **Error Handling**: If OpenAI 500s, log error and emit "processing_failed" event (fail open or closed based on policy).
