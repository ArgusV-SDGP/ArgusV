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


# ── Concurrency: parallel producers ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_producers_no_data_corruption():
    """Multiple coroutines writing to raw_detections concurrently
    should all succeed and every item should be retrievable intact."""
    bus = EventBus()
    n = 50

    async def producer(tag: int):
        for i in range(10):
            await bus.raw_detections.put({"producer": tag, "seq": i})

    await asyncio.gather(*[producer(t) for t in range(n)])

    assert bus.raw_detections.qsize() == n * 10

    seen = []
    while not bus.raw_detections.empty():
        item = bus.raw_detections.get_nowait()
        assert "producer" in item and "seq" in item
        seen.append((item["producer"], item["seq"]))

    assert len(seen) == n * 10


# ── Ordering: FIFO guarantee ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fifo_order_raw_detections():
    """Items must come out of raw_detections in the exact order they went in."""
    bus = EventBus()
    events = [{"seq": i} for i in range(20)]
    for e in events:
        bus.raw_detections.put_nowait(e)

    received = []
    while not bus.raw_detections.empty():
        received.append(bus.raw_detections.get_nowait())

    assert received == events


@pytest.mark.asyncio
async def test_fifo_order_actions():
    """actions channel preserves insertion order."""
    bus = EventBus()
    payloads = [{"action": f"act-{i}"} for i in range(10)]
    for p in payloads:
        bus.actions.put_nowait(p)

    received = []
    while not bus.actions.empty():
        received.append(bus.actions.get_nowait())

    assert received == payloads


# ── Stats accuracy ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stats_all_channels_start_at_zero():
    """A freshly created EventBus should report 0 for every channel."""
    bus = EventBus()
    stats = bus.stats()
    for channel, depth in stats.items():
        assert depth == 0, f"{channel} should be 0 on fresh bus, got {depth}"


@pytest.mark.asyncio
async def test_stats_accuracy_after_mixed_puts_and_gets():
    """stats() must reflect the live queue state after interleaved puts/gets."""
    bus = EventBus()

    for i in range(5):
        bus.raw_detections.put_nowait({"i": i})
    for i in range(3):
        bus.vlm_requests.put_nowait({"i": i})

    # consume 2 from raw_detections
    bus.raw_detections.get_nowait()
    bus.raw_detections.get_nowait()

    stats = bus.stats()
    assert stats["raw_detections"] == 3
    assert stats["vlm_requests"] == 3
    assert stats["vlm_results"] == 0
    assert stats["actions"] == 0


@pytest.mark.asyncio
async def test_stats_returns_zero_after_full_drain():
    """After draining a channel completely, its stat should return to 0."""
    bus = EventBus()
    for i in range(10):
        bus.alerts_ws.put_nowait({"i": i})

    assert bus.stats()["alerts_ws"] == 10

    while not bus.alerts_ws.empty():
        bus.alerts_ws.get_nowait()

    assert bus.stats()["alerts_ws"] == 0
