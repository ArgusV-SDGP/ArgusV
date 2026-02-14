# Service Specification: RAG Chat Service (`rag-chat-service`)

> **Status**: DRAFT  
> **Type**: Microservice (Backend/API)  
> **Language**: Python 3.11 (FastAPI + LangChain)  
> **Responsibility**: Knowledge retrieval and natural language query processing.

---

## 1. Business Logic & Responsibility
The `rag-chat-service` powers the "Chat with Argus" feature. It allows security operators to ask natural language questions about past events. It does *not* process live video; it processes the *memory* of past events.

### Core Capabilities:
1.  **Query Understanding**: Converts "Show me suspicious people at the dock last night" into structured filters (Zone=Dock, Time=Last Night) + Semantic Vector.
2.  **Retrieval**: Queries Qdrant for relevant incident chunks.
3.  **Synthesis**: Uses an LLM to summarize the retrieved incidents into a human-readable answer.

---

## 2. Engineering Requirements

### 2.1 Inputs & Outputs
-   **Input**: HTTP POST `/chat/query` `{ "message": "..." }`.
-   **Output**: HTTP Stream (Text tokens + sourced Incident IDs).

### 2.2 Technical Stack
-   **Framework**: `FastAPI` + `LangChain` (or `LlamaIndex`).
-   **Vector DB**: `Qdrant`.
-   **LLM**: `GPT-4o-mini` (Fast, cheap, good enough for summarization).

### 2.3 RAG Pipeline
1.  **User**: *"Who was at the back door?"*
2.  **Router/Filter**: LLM extracts `{"zone": "back door"}`.
3.  **Retriever**: 
    -   Vector Search: `embedding("Who was at...")`.
    -   Filter: `metadata.zone == "back door"`.
4.  **Generator**: 
    -   Context: *"Incident 1: Person detected at Back Door at 10pm..."*
    -   Prompt: *"Answer the user based on the context."*

---

## 3. Interfaces & APIs

### Endpoint: `POST /api/chat/query`
**Request**:
```json
{
  "query": "Did anyone trigger an alert last night?",
  "history": []
}
```

**Response** (Streaming):
```json
{
  "answer": "Yes, there were two alerts last night at the Loading Dock...",
  "sources": ["inc_555", "inc_556"]
}
```

---

## 4. MVP Implementation Steps
1.  **Qdrant Connection**: Setup connection to Qdrant Cloud or local container.
2.  **Ingestion Listener**: (Optional - if not done by Decision Engine) Listen to Kafka to sync new incidents.
3.  **Chain**: Build a simple LangChain `RetrievalQA` chain.
4.  **API**: Expose the chain via FastAPI endpoint.
