# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ArgusV Backend — multi-stage build                                      ║
# ║                                                                          ║
# ║  Stage 1 (builder) — installs all Python packages                       ║
# ║    Uses build tools (gcc, libpq-dev) needed to compile C extensions.    ║
# ║    Final image does NOT carry compilers — smaller, safer runtime.        ║
# ║                                                                          ║
# ║  Stage 2 (runtime) — lean production image                              ║
# ║    Only system libs needed at run-time (ffmpeg, OpenCV, libpq).         ║
# ║    Copies compiled venv from the builder stage.                          ║
# ║    Runs as non-root user "argus".                                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# Build-time system deps (compiler + libpq headers for psycopg2-binary)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libpq-dev \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast pip resolver/installer)
RUN pip install --no-cache-dir uv

# Copy dependency manifest first (layer-cache friendly)
COPY pyproject.toml .

# Create isolated venv and install all production deps into it
RUN uv venv /build/.venv \
    && uv pip install --python /build/.venv/bin/python .

# ── Stage 2: production runtime ───────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app

# Runtime-only system deps:
#   ffmpeg      — video segment recording + frame extraction
#   libgl1      — OpenCV headless GPU-path fallback
#   libglib2.0  — GLib runtime (OpenCV dep)
#   libgomp1    — OpenMP (YOLO parallel inference)
#   libsm6/xext6 — X11 stubs required by some OpenCV builds
#   libpq5      — PostgreSQL client shared library (psycopg2 runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy the fully-built virtualenv from builder (no compiler/headers needed)
COPY --from=builder /build/.venv /app/.venv

# Copy application source
COPY src/    ./src/
COPY static/ ./static/

# Activate venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create non-root user for security
RUN groupadd -r argus && useradd -r -g argus -d /app -s /sbin/nologin argus \
    && chown -R argus:argus /app

# Writable dirs for YOLO model cache and recordings
RUN mkdir -p /tmp/argus_segments /recordings \
    && chown -R argus:argus /tmp/argus_segments /recordings

USER argus

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
