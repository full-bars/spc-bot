# Build stage
# The digest is the multi-arch INDEX digest (works for amd64 + arm64).
# Pinning a platform-specific sub-manifest digest breaks the other arch
# with "exec format error" (verified 2026-08-14, PR #667 CI).
FROM python:3.13-slim-trixie@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder

# Install build dependencies
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libgeos-dev \
    libproj-dev \
    proj-bin \
    libgdal-dev \
    libfreetype6-dev \
    libpng-dev \
    libjpeg-dev \
    zlib1g-dev \
    libopenblas-dev \
    liblapack-dev \
    libffi-dev \
    libhdf5-dev \
    libnetcdf-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Upgrade pip and install build-time requirements
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip setuptools wheel

COPY requirements.txt .

# Build wheels for all dependencies
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --wheel-dir /build/wheels -r requirements.txt

# Runtime stage
FROM python:3.13-slim-trixie@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

# Install runtime dependencies before copying wheels so this layer is only
# invalidated by apt changes, not by code or dependency updates.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends \
    libgeos3.13.1 \
    libproj25 \
    proj-data \
    libgdal36 \
    libfreetype6 \
    libpng16-16 \
    libjpeg62-turbo \
    zlib1g \
    libopenblas0 \
    liblapack3 \
    libstdc++6 \
    libgfortran5 \
    ca-certificates \
    curl \
    libhdf5-310 \
    libnetcdf22 \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CACHE_DIR=/app/cache \
    LOG_FILE=/app/cache/spc_bot.log

WORKDIR /app

# Install dependencies from wheels built in builder stage
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

# Copy application code
COPY . .

# Create cache directory
RUN mkdir -p /app/cache

# Define the command to run the bot
CMD ["python", "main.py"]
