from pathlib import Path
import os
from src import config as cfg
print(f"Current Dir: {os.getcwd()}")
print(f"Segment Tmp: {os.path.abspath(cfg.SEGMENT_TMP_DIR)}")
print(f"Local Recs:  {os.path.abspath(cfg.LOCAL_RECORDINGS_DIR)}")
