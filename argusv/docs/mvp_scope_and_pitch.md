# ArgusV MVP Scope and VC Pitch Pack

## 1) Product Thesis

ArgusV is an AI-native surveillance platform that combines real-time detection, threat triage, and searchable video intelligence in one operational workflow.

Problem:
- Existing SMB/enterprise setups are fragmented: NVR + alerting + manual review + no natural language intelligence.

ArgusV MVP answer:
- Real-time alerts with threat scoring
- Zone-based behavioral detection (including loitering)
- Incident-centric review and replay
- AI-assisted context and search foundation

## 2) ICP and Buyer Personas

Primary ICP (MVP):
1. Multi-site SMB security teams
2. Warehouses/logistics operators
3. Campuses/private premises with existing RTSP cameras

Buyer roles:
1. Security Manager (budget owner)
2. Operations Lead (daily user)
3. Technical Admin (deployment owner)

## 3) MVP Scope (Must Ship)

### A. Live Detection and Alerting
- RTSP ingest -> motion gate -> YOLO + tracker
- Zone matching + dwell/loiter events
- Tiered VLM triage (`gpt-4o-mini -> gpt-4o`)
- Live WebSocket alert feed
- Incident creation for medium/high threats

### B. Incident Ops Workflow
- Incident list with filters
- Incident detail view
- Incident resolve/annotate action
- Threat metadata retained for audit

### C. Zone Intelligence
- Zone CRUD APIs
- Polygon editor UI
- Validation and hot reload into runtime workers

### D. Recording and Replay
- Segment indexing
- Playlist + timeline APIs
- Replay from incident deep-link
- Event markers on playback

### E. Platform Trust Features
- Health endpoint and runtime queue telemetry
- Basic authentication and role gating
- Deterministic test suite for core flows

## 4) Stretch Scope (Good for Demo, Optional for Day-7)

1. Multi-provider VLM (Gemini/Ollama/LlamaCpp fallback)
2. Semantic RAG chat over detections/incidents
3. MQTT + webhook + PTZ actuation extensions
4. Prometheus `/metrics`

## 5) Out of Scope for MVP Pitch (Can Mention as Roadmap)

1. Full enterprise SSO federation and tenant isolation
2. Full face recognition and LPR production hardening
3. Mobile-native apps
4. Large-scale distributed camera orchestration

## 6) Technical Credibility Narrative (For VC)

Architecture characteristics:
1. Async event-driven pipeline with bounded queues
2. Postgres data model for incident/detection/segment auditability
3. Redis-backed live state and config update propagation
4. Modularity preserved (workers/routes/domains), despite monolith runtime

Defensibility angles:
1. Video + semantic context layer over operational incidents
2. Faster triage loop than pure motion/object-alert systems
3. Data exhaust suitable for continuous policy tuning

## 7) 7-Day Delivery Plan Summary

Execution model:
- 6 developers in 3 rotating pairs
- 3 parallel flow tracks with integration checkpoints twice daily

Artifacts already prepared:
1. `docs/planning/flow_definition_week1.json`
2. `docs/planning/developer_backlog_week1.json`
3. `docs/system_flow_diagram.md`
4. `docs/api_documentation.md`

## 8) MVP Demo Script (VC Pitch)

1. Start live feed from camera/stream.
2. Show person entering restricted zone and loitering alert appears in real time.
3. Show VLM threat summary enrichment on same event.
4. Open incidents page, filter by threat level, resolve one incident.
5. Jump to recordings page, replay that incident with timeline markers.
6. Show zone editor, adjust polygon, demonstrate changed detection behavior.
7. Show health/stats panel and explain reliability controls.
8. Close with roadmap: semantic chat and multi-provider AI.

## 9) KPI Targets for MVP Validation

Product KPIs:
1. Alert-to-incident latency: < 3 seconds (P50)
2. False-positive reduction vs raw detection baseline: >= 30%
3. Incident review completion time reduction: >= 40%
4. Uptime in demo/staging: >= 99%

Business KPIs:
1. 3 design partners onboarding
2. 2 paid pilot LOIs
3. >= 1 multi-site deployment trial

## 10) Risk Register and Mitigations

1. Risk: one-week full-scope compression
- Mitigation: strict feature freeze and must/should cutline

2. Risk: external AI provider instability
- Mitigation: graceful degradation + provider fallback switches

3. Risk: replay path blocked by incomplete segment metadata
- Mitigation: prioritize recording DB write/link tasks on Day 1-2

4. Risk: unfinished auth weakens enterprise narrative
- Mitigation: minimum JWT + RBAC path completed before demo

## 11) Investor-Facing Positioning (Short)

ArgusV is building an AI-native security operations layer on top of existing camera infrastructure, reducing detection noise and collapsing investigation time through real-time triage and contextual video intelligence.
