"""
api/ws_handler.py — WebSocket connection manager
-------------------------------------------------
Manages all connected dashboard clients.
Reads from bus.alerts_ws and fans-out to every connected client.
"""

import asyncio
import json
import logging
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("ws-handler")


class ConnectionManager:
    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        logger.info(f"[WS] Client connected — total={len(self._clients)}")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._clients.discard(ws)
        logger.info(f"[WS] Client disconnected — total={len(self._clients)}")

    async def broadcast(self, message: dict):
        if not self._clients:
            return
        payload = json.dumps(message, default=str)
        dead = set()
        async with self._lock:
            clients = set(self._clients)

        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)

        if dead:
            async with self._lock:
                self._clients -= dead

    async def fan_out_loop(self, alerts_queue: asyncio.Queue):
        """Long-running task: reads bus.alerts_ws → broadcasts to all clients."""
        logger.info("[WS] Fan-out loop started")
        while True:
            msg = await alerts_queue.get()
            await self.broadcast(msg)
            alerts_queue.task_done()


manager = ConnectionManager()
