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
COPY pyproject.toml .
RUN pip install --no-cache-dir uv && uv pip install . --system

# Copy monolith source
COPY src/ ./src/
COPY static/ ./static/

ENV PYTHONPATH="/app/src"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
