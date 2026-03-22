"""
bus.py — ArgusV Monolith Internal Event Bus
---------------------------------------------
Replaces Kafka entirely with asyncio.Queue.
Same channel names as before — drop-in mental model.

Channels:
  raw_detections  edge → pipeline (detection events)
  vlm_requests    pipeline → VLM inference
  vlm_results     VLM → decision engine
  actions         decision → notification + actuation
  alerts_ws       decision → WebSocket fan-out (dashboard)
  segments        recording_worker → segment linker (completed .ts files)
"""

import asyncio
from dataclasses import dataclass, field

@dataclass
class EventBus:
    raw_detections : asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    vlm_requests   : asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    vlm_results    : asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    actions        : asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=500))
    alerts_ws      : asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=2000))

    rag_indexing   : asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=500))
    snapshots      : asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    clips          : asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    segments       : asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=500))

    def stats(self) -> dict:
        return {
            "raw_detections" : self.raw_detections.qsize(),
            "vlm_requests"   : self.vlm_requests.qsize(),
            "vlm_results"    : self.vlm_results.qsize(),
            "actions"        : self.actions.qsize(),
            "alerts_ws"      : self.alerts_ws.qsize(),
            "rag_indexing"   : self.rag_indexing.qsize(),
            "snapshots"      : self.snapshots.qsize(),
            "clips"          : self.clips.qsize(),
            "segments"       : self.segments.qsize(),
        }

bus = EventBus()
