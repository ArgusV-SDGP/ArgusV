"""
tests/test_watchdog_trigger.py — Manual test to trigger Watchdog alerts
-------------------------------------------------------------------------
This script simulates a "stale" task by not sending heartbeats
or a camera failure by spoofing the health check.
"""

import time
import logging
from workers.watchdog_worker import record_heartbeat, _task_heartbeats, _last_restart_attempt

# Setup minimal logging to see it in terminal
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-trigger")

def simulate_stale_task():
    print("\n--- Simulating Stale Task ---")
    # 1. Record an initial heartbeat
    record_heartbeat("stream-ingestion")
    print("Recorded heartbeat for 'stream-ingestion'")
    
    # 2. Modify the heartbeat to be 70 seconds old (STALE_THRESHOLD is 60s)
    _task_heartbeats["stream-ingestion"] = time.time() - 70
    print("Spoofed heartbeat to be 70s old.")
    
    # In a real run, the watchdog_worker() loop would now log a CRITICAL error.
    # We can manually run the check once:
    from workers.watchdog_worker import _check_tasks
    _check_tasks()

async def simulate_camera_failure():
    print("\n--- Simulating Camera Failure ---")
    # We mock the cameras_health response to return connected: False
    from unittest.mock import patch
    
    # Mock health reporting offline
    mock_health = [{"camera_id": "cam-01", "connected": False}]
    
    # Mocking time to bypass the 30s threshold immediately
    _last_restart_attempt["cam-01"] = time.time() - 40
    
    with patch("workers.watchdog_worker.cameras_health", return_value=mock_health):
        from workers.watchdog_worker import _check_cameras
        await _check_cameras()

if __name__ == "__main__":
    import asyncio
    
    simulate_stale_task()
    asyncio.run(simulate_camera_failure())
    print("\nCheck the console output above for [Watchdog] log messages.")
