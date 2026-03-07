"""FFMPEG STREAM PACKET GENERATOR + SERVER"""

import os
import time
import json

import subprocess
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)

logger = logging.getLogger("stream-processor")

config_file = 'config.json'
with open(config_file, 'r') as f:
    stream_configs = json.load(f)

# Dictionary to keep our "leashes" on the running processes
active_processes = {}

# start the media server
try:
    active_processes['media_server'] = subprocess.Popen(["python","server.py"])
except Exception as e:
    logger.error(e)

for config in stream_configs:
    name = config["name"]
    file_path = config["file_path"]
    url = config["rtsp_url"]
    if not os.path.exists(file_path):
        logger.warning(f"ERROR: Cannot find video file '{file_path}' for [{name}]. Skipping...")
        continue

    command = ['ffmpeg', '-re']

    # video looping option
    if config["loop"]:
        command.extend(['-stream_loop', '-1'])

    command.extend([
        '-i', file_path,
        '-c', 'copy',  # Use -c:v libx264 if your video isn't already H.264
        '-f', 'rtsp',
        url
    ])

    logger.info(f"Launching [{name}] -> {url}")
    process = subprocess.Popen(
        command
    )
    active_processes[name] = process

logger.info(f"Processes active: {len(active_processes)}")
logger.info(f"[+] Monitoring processes")

try:
    while True:
        for stream_name in list(active_processes.keys()):
            process = active_processes[stream_name]
            status = process.poll()
            if status is not None:
                logger.warning(f"⚠️ [{stream_name}] stopped unexpectedly! (Exit code: {status})")
                # Remove from our active tracking
                del active_processes[stream_name]
        logger.info(f"[+] Waiting for {len(active_processes)} active streams...")

        if len(active_processes) == 0:
            logger.info("All streams have ended. Shutting down manager.")
            break

        time.sleep(0.5)

except KeyboardInterrupt:
    logger.info("Ctrl+C detected! Safely shutting down all FFmpeg streams...")
    for stream_name, process in active_processes.items():
        print(f"   Terminating [{stream_name}]...")
        process.terminate()
        process.wait()
    logger.info("All streams closed. Goodbye!")