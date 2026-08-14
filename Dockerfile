# Build stage
FROM python:3.13-slim-bookworm@sha256:0f16c5d35fe6464ee471792ab3bb9116f911b65b3fbf10120c98d2bdc6332f48 AS builder

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
FROM python:3.13-slim-bookworm@sha256:0f16c5d35fe6464ee471792ab3bb9116f911b65b3fbf10120c98d2bdc6332f48

# Install runtime dependencies before copying wheels so this layer is only
# invalidated by apt changes, not by code or dependency updates.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends \
    libgeos-c1v5 \
    libproj25 \
    proj-data \
    libgdal32 \
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
    libhdf5-103-1 \
    libnetcdf19 \
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
