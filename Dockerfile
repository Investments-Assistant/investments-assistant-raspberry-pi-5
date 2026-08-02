# ARM64-friendly build for Raspberry Pi 5.
# Pin the Debian family, not individual package versions, so Pi builds do not
# break when the base image receives normal security updates.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENBLAS_NUM_THREADS=4 \
    OMP_NUM_THREADS=4

# System deps
# hadolint global ignore=DL3008
# I intentionally do not pin apt package patch versions because this image targets
# ARM64/Raspberry Pi builds and should receive normal Debian Bookworm security updates.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    libopenblas-dev \
    libpq-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libfontconfig1 \
    pkg-config \
    shared-mime-info \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install poetry
RUN pip install --no-cache-dir poetry==2.1.1

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install runtime dependencies, including llama-cpp-python.
# llama-cpp-python compiles from source on the Pi so it links against OpenBLAS.
# This takes several minutes, but gives materially better CPU throughput.
ENV CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS" \
    FORCE_CMAKE=1
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-root --no-interaction

# Copy source
COPY src/ ./src/
COPY .env.example ./.env.example
RUN mkdir -p /app/scripts
COPY scripts/create_user.py ./scripts/create_user.py
COPY scripts/create_broker_key.py ./scripts/create_broker_key.py

# Create directories and set up non-root user for security
RUN mkdir -p /app/reports /app/models \
    && useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
