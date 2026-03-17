#!/usr/bin/env python3
"""
Video Pipeline Diagnostic Tool
Tests: Windows FFmpeg → MediaMTX → ArgusV recording chain
"""

import subprocess
import time
import requests
import sys

def check_step(name, test_func):
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print('='*60)
    try:
        result = test_func()
        status = "[PASS]" if result else "[FAIL]"
        print(f"{status}\n")
        return result
    except Exception as e:
        print(f"[ERROR]: {e}\n")
        return False

def test_mediamtx_http():
    """Test if MediaMTX HTTP server is accessible"""
    try:
        r = requests.get('http://localhost:8888/', timeout=2)
        print(f"MediaMTX HTTP responding: {r.status_code}")
        return True
    except Exception as e:
        print(f"MediaMTX HTTP not accessible: {e}")
        return False

def test_argusv_api():
    """Test if ArgusV API is responding"""
    try:
        r = requests.get('http://localhost:8000/health', timeout=2)
        data = r.json()
        print(f"ArgusV API Status: {r.status_code}")
        if 'cameras' in data:
            for cam in data['cameras']:
                print(f"  Camera {cam['camera_id']}: connected={cam.get('connected')}, frames={cam.get('frame_count')}, recording={cam.get('recording')}")
        return r.status_code == 200
    except Exception as e:
        print(f"ArgusV API not accessible: {e}")
        return False

def test_rtsp_connection():
    """Test if we can connect to MediaMTX RTSP"""
    print("Testing RTSP connection with ffprobe...")
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1',
        'rtsp://localhost:8554/cam-01'
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 or 'duration' in result.stdout:
            print("[OK] RTSP stream accessible at rtsp://localhost:8554/cam-01")
            return True
        else:
            print(f"[X] RTSP connection failed: {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        print("[X] RTSP connection timeout")
        return False
    except FileNotFoundError:
        print("[X] ffprobe not found (FFmpeg not installed or not in PATH)")
        return False

def test_hls_stream():
    """Test if HLS stream is available"""
    try:
        r = requests.get('http://localhost:8888/cam-01/index.m3u8', timeout=2)
        if r.status_code == 200:
            print(f"[OK] HLS stream available")
            print(f"Playlist preview:\n{r.text[:200]}")
            return True
        else:
            print(f"[X] HLS stream not found: {r.status_code}")
            return False
    except Exception as e:
        print(f"[X] HLS stream check failed: {e}")
        return False

def test_segments_directory():
    """Check if recording segments exist"""
    import os
    import glob

    tmp_dir = "tmp/argus_segments/cam-01"
    final_dir = "recordings/cam-01"

    print(f"Checking {tmp_dir}...")
    tmp_files = glob.glob(f"{tmp_dir}/*.ts")
    print(f"  Temporary segments: {len(tmp_files)}")
    if tmp_files:
        for f in sorted(tmp_files)[-3:]:
            size = os.path.getsize(f) / 1024
            print(f"    {os.path.basename(f)}: {size:.1f} KB")

    print(f"\nChecking {final_dir}...")
    final_files = glob.glob(f"{final_dir}/*.ts")
    print(f"  Final recordings: {len(final_files)}")
    if final_files:
        for f in sorted(final_files)[-3:]:
            size = os.path.getsize(f) / 1024
            print(f"    {os.path.basename(f)}: {size:.1f} KB")

    return len(tmp_files) > 0 or len(final_files) > 0

def main():
    print("""
===========================================================
       ArgusV Video Pipeline Diagnostic Tool

  Expected Flow:
  processor.py -> MediaMTX -> ArgusV FFmpeg Recorder
===========================================================
""")

    tests = [
        ("MediaMTX HTTP Server", test_mediamtx_http),
        ("ArgusV API Health", test_argusv_api),
        ("HLS Stream Availability", test_hls_stream),
        ("RTSP Stream Connection", test_rtsp_connection),
        ("Recording Segments", test_segments_directory),
    ]

    results = {}
    for name, test_func in tests:
        results[name] = check_step(name, test_func)

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    passed = sum(results.values())
    total = len(results)
    print(f"Tests passed: {passed}/{total}")

    if passed == total:
        print("\nAll systems operational!")
    else:
        print("\nIssues detected. Check failed tests above.")
        print("\nTroubleshooting tips:")
        if not results.get("MediaMTX HTTP Server"):
            print("  • Start MediaMTX: docker-compose up -d mediamtx")
        if not results.get("ArgusV API Health"):
            print("  • Start ArgusV: docker-compose up -d argusv")
        if not results.get("HLS Stream Availability"):
            print("  • Start video streamer: cd RTSP-video-streamer && python processor.py")
        if not results.get("RTSP Stream Connection"):
            print("  • Check processor.py is running and publishing to MediaMTX")
        if not results.get("Recording Segments"):
            print("  • Check ArgusV logs: docker-compose logs -f argusv | grep FFmpeg")

    sys.exit(0 if passed == total else 1)

if __name__ == "__main__":
    main()
