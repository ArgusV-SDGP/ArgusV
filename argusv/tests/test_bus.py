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


# ── Backpressure: all remaining channels ─────────────────────────────────────

@pytest.mark.asyncio
async def test_vlm_requests_backpressure():
    """vlm_requests raises QueueFull at maxsize=200."""
    bus = EventBus()
    for i in range(200):
        bus.vlm_requests.put_nowait({"i": i})
    assert bus.vlm_requests.full()
    with pytest.raises(asyncio.QueueFull):
        bus.vlm_requests.put_nowait({"overflow": True})


@pytest.mark.asyncio
async def test_vlm_results_backpressure():
    """vlm_results raises QueueFull at maxsize=200."""
    bus = EventBus()
    for i in range(200):
        bus.vlm_results.put_nowait({"i": i})
    assert bus.vlm_results.full()
    with pytest.raises(asyncio.QueueFull):
        bus.vlm_results.put_nowait({"overflow": True})


@pytest.mark.asyncio
async def test_actions_backpressure():
    """actions raises QueueFull at maxsize=500."""
    bus = EventBus()
    for i in range(500):
        bus.actions.put_nowait({"i": i})
    assert bus.actions.full()
    with pytest.raises(asyncio.QueueFull):
        bus.actions.put_nowait({"overflow": True})


@pytest.mark.asyncio
async def test_alerts_ws_backpressure():
    """alerts_ws raises QueueFull at maxsize=2000."""
    bus = EventBus()
    for i in range(2000):
        bus.alerts_ws.put_nowait({"i": i})
    assert bus.alerts_ws.full()
    with pytest.raises(asyncio.QueueFull):
        bus.alerts_ws.put_nowait({"overflow": True})


@pytest.mark.asyncio
async def test_snapshots_backpressure():
    """snapshots raises QueueFull at maxsize=1000."""
    bus = EventBus()
    for i in range(1000):
        bus.snapshots.put_nowait({"i": i})
    assert bus.snapshots.full()
    with pytest.raises(asyncio.QueueFull):
        bus.snapshots.put_nowait({"overflow": True})


@pytest.mark.asyncio
async def test_clips_backpressure():
    """clips raises QueueFull at maxsize=200."""
    bus = EventBus()
    for i in range(200):
        bus.clips.put_nowait({"i": i})
    assert bus.clips.full()
    with pytest.raises(asyncio.QueueFull):
        bus.clips.put_nowait({"overflow": True})


@pytest.mark.asyncio
async def test_rag_indexing_backpressure():
    """rag_indexing raises QueueFull at maxsize=500."""
    bus = EventBus()
    for i in range(500):
        bus.rag_indexing.put_nowait({"i": i})
    assert bus.rag_indexing.full()
    with pytest.raises(asyncio.QueueFull):
        bus.rag_indexing.put_nowait({"overflow": True})
