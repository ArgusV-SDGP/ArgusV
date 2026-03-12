"""tests/test_bus.py — Task TEST-02"""
import pytest, asyncio
from bus import EventBus

@pytest.mark.asyncio
async def test_put_get(mock_bus):
    await mock_bus.raw_detections.put({"test": 1})
    item = await mock_bus.raw_detections.get()
    assert item == {"test": 1}

@pytest.mark.asyncio
async def test_stats_reflects_queue_size(mock_bus):
    await mock_bus.vlm_requests.put({"x": 1})
    stats = mock_bus.stats()
    assert stats["vlm_requests"] == 1


# ── Backpressure: raw_detections (maxsize=1000) ───────────────────────────────

@pytest.mark.asyncio
async def test_raw_detections_backpressure():
    """raw_detections queue raises QueueFull when maxsize=1000 is exceeded."""
    bus = EventBus()
    for i in range(1000):
        bus.raw_detections.put_nowait({"i": i})
    assert bus.raw_detections.full()
    with pytest.raises(asyncio.QueueFull):
        bus.raw_detections.put_nowait({"overflow": True})
