import subprocess
import os
import time

server_folder = 'artifact'
server_exe = 'mediamtx.exe'

print("Starting MediaMTX Server in the background...")
server_process = subprocess.Popen(
    [os.path.join(server_folder, server_exe)],
    cwd=server_folder
)

time.sleep(2)

print("MediaMTX Server is running! Python is now free to launch FFmpeg streams.")
try:
    while True:
        if server_process.poll() is not None:
            print("Uh oh, the MediaMTX server crashed!")
            break
        time.sleep(1)

except KeyboardInterrupt:
    print("Shutting down server...")
    server_process.terminate()
    server_process.wait()
