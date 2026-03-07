FROM python:3.11-slim-bookworm

WORKDIR /app

# System deps: ffmpeg for recording, libGL for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libsm6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY argusv/pyproject.toml .
RUN pip install --no-cache-dir uv && uv pip install . --system

# Copy monolith source
COPY argusv/src/ ./src/
COPY argusv/static/ ./static/

# Shared edge-gateway modules (no ../ needed — context is services/)
COPY edge-gateway/src/ ./vendor/edge-gateway/

ENV PYTHONPATH="/app/src:/app/vendor/edge-gateway"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
