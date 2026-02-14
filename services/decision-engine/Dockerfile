FROM python:3.11-slim

WORKDIR /app

# Install uv for fast package management
RUN pip install uv

# Create virtual environment
ENV VIRTUAL_ENV=/app/.venv
RUN uv venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install dependencies via uv
COPY pyproject.toml .
RUN uv pip install -r pyproject.toml --system

COPY . .

CMD ["python", "src/main.py"]
