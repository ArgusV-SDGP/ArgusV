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

# TODO TEST-02: add backpressure test (maxsize exceeded)
