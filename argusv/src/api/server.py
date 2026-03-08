"""
api/server.py — ArgusV consolidated FastAPI application
---------------------------------------------------------
All routes in one place:
  /           → dashboard HTML (the shipped feature)
  /ws/alerts  → live detection feed WebSocket
  /health     → system health
  /api/...    → REST (recordings, incidents, zones, detections)
"""

import asyncio
import logging
import time
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from bus import bus
from api.ws_handler import manager
from api.routes.cameras import router as cameras_router
from api.routes.zones import router as zones_router
from api.routes.incidents import router as incidents_router
from api.routes.recordings import router as recordings_router
from api.routes.auth import router as auth_router
from api.routes.configuration import router as configuration_router
from workers.edge_worker import start_cameras, stop_cameras, cameras_health
from workers.pipeline_worker import (
    stream_ingestion_worker,
    vlm_inference_worker,
    decision_engine_worker,
    notification_worker,
)

logger = logging.getLogger("api.server")

STATIC_DIR = Path(__file__).parent.parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Start all background workers ──────────────────────────────────────
    loop = asyncio.get_running_loop()

    # Bootstrap DB schema (idempotent)
    from db.connection import create_tables
    create_tables()
    logger.info("✅ Database schema ready")

    # Camera threads (sync → async bridge)
    start_cameras(bus.raw_detections, loop)

    # Async pipeline tasks
    tasks = [
        asyncio.create_task(stream_ingestion_worker(),  name="stream-ingestion"),
        asyncio.create_task(vlm_inference_worker(),     name="vlm-inference"),
        asyncio.create_task(decision_engine_worker(),   name="decision-engine"),
        asyncio.create_task(notification_worker(),      name="notification"),
        asyncio.create_task(manager.fan_out_loop(bus.alerts_ws), name="ws-fanout"),
    ]

    logger.info("✅ ArgusV monolith started — all workers running")
    yield  # ──────── app is running ────────

    # ── Graceful shutdown ─────────────────────────────────────────────────
    stop_cameras()
    for t in tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    logger.info("ArgusV stopped.")


app = FastAPI(title="ArgusV", version="1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(cameras_router)
app.include_router(zones_router)
app.include_router(incidents_router)
app.include_router(recordings_router)
app.include_router(configuration_router)

# ── Mount static files ────────────────────────────────────────────────────────
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Dashboard HTML ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    html_file = STATIC_DIR / "dashboard.html"
    if html_file.exists():
        return HTMLResponse(html_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ArgusV</h1><p>Dashboard not found.</p>")


# ── WebSocket: Live Alert Feed ────────────────────────────────────────────────

@app.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep alive — client pings or we just wait for disconnect
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":   "healthy",
        "service":  "argusv-monolith",
        "cameras":  cameras_health(),
        "bus_queue_sizes": bus.stats(),
        "uptime_sec": round(time.time() - _START_TIME, 1),
    }

_START_TIME = time.time()


# ── REST: Recordings + Incidents (from replay_api.py logic) ──────────────────

@app.get("/api/incidents")
async def list_incidents(
    camera_id:    str  = None,
    threat_level: str  = None,
    status:       str  = None,
    limit:        int  = 50,
):
    from db.connection import get_db_sync
    from db.models import Incident
    db = get_db_sync()
    try:
        q = db.query(Incident)
        if camera_id:    q = q.filter(Incident.camera_id    == camera_id)
        if threat_level: q = q.filter(Incident.threat_level == threat_level)
        if status:       q = q.filter(Incident.status       == status)
        incidents = q.order_by(Incident.detected_at.desc()).limit(limit).all()
        return [
            {
                "incident_id":  str(i.incident_id),
                "camera_id":    i.camera_id,
                "zone_name":    i.zone_name,
                "object_class": i.object_class,
                "threat_level": i.threat_level,
                "summary":      i.summary,
                "status":       i.status,
                "detected_at":  i.detected_at.isoformat(),
            }
            for i in incidents
        ]
    finally:
        db.close()


@app.get("/api/detections")
async def search_detections(
    camera_id:    str   = None,
    object_class: str   = None,
    threats_only: bool  = False,
    limit:        int   = 100,
):
    from db.connection import get_db_sync
    from db.models import Detection
    db = get_db_sync()
    try:
        q = db.query(Detection)
        if camera_id:    q = q.filter(Detection.camera_id    == camera_id)
        if object_class: q = q.filter(Detection.object_class == object_class)
        if threats_only: q = q.filter(Detection.is_threat    == True)
        dets = q.order_by(Detection.detected_at.desc()).limit(limit).all()
        return [
            {
                "detection_id":  str(d.detection_id),
                "event_id":      d.event_id,
                "camera_id":     d.camera_id,
                "zone_name":     d.zone_name,
                "object_class":  d.object_class,
                "confidence":    d.confidence,
                "threat_level":  d.threat_level,
                "is_threat":     d.is_threat,
                "vlm_summary":   d.vlm_summary,
                "dwell_sec":     d.dwell_sec,
                "event_type":    d.event_type,
                "detected_at":   d.detected_at.isoformat(),
                "bbox":          {"x1": d.bbox_x1, "y1": d.bbox_y1, "x2": d.bbox_x2, "y2": d.bbox_y2},
            }
            for d in dets
        ]
    finally:
        db.close()
