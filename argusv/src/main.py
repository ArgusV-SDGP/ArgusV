"""
main.py — ArgusV Monolith Entry Point
--------------------------------------
Run with: uvicorn main:app --host 0.0.0.0 --port 8000
"""

import logging
import sys
from pathlib import Path

# Automatically add the src/ directory to the Python path
# so that imports like 'config' and 'api' work when running from the root folder
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
import config as cfg

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)-20s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)

# Import the FastAPI app to expose to uvicorn
from api.server import app  # noqa: E402, F401
