# Build stage
FROM python:3.12-alpine AS builder

# Install build dependencies
RUN apk add --no-cache \
    gcc \
    g++ \
    musl-dev \
    make \
    cmake \
    python3-dev \
    geos-dev \
    proj-dev \
    proj-util \
    gdal-dev \
    freetype-dev \
    libpng-dev \
    jpeg-dev \
    zlib-dev \
    openblas-dev \
    liblapack \
    lapack-dev \
    libffi-dev

# Set working directory
WORKDIR /build

# Set environment variables for build
ENV HDF5_DIR=/usr \
    NETCDF4_DIR=/usr \
    C_INCLUDE_PATH=/usr/include/hdf5 \
    CPATH=/usr/include/hdf5

# Upgrade pip and install build-time requirements
RUN pip install --no-cache-dir --upgrade pip setuptools wheel Cython

# Copy requirements file
COPY requirements.txt .

# Install dependencies
# We use --no-binary for cartopy as it lacks musllinux wheels
RUN pip install --no-cache-dir -r requirements.txt \
    --no-binary cartopy,shapely

# Runtime stage
FROM python:3.12-alpine

# Install runtime dependencies
RUN apk add --no-cache \
    geos \
    proj \
    proj-data \
    gdal \
    freetype \
    libpng \
    jpeg \
    zlib \
    openblas \
    liblapack \
    libstdc++ \
    libgfortran \
    ca-certificates \
    curl \
    libc6-compat \
    hdf5 \
    netcdf

# Install cloudflared for failover
RUN curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared \
    && chmod +x /usr/local/bin/cloudflared

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROJ_DIR=/usr \
    CACHE_DIR=/app/cache \
    LOG_FILE=/app/cache/spc_bot.log

# Create app directory
WORKDIR /app

# Copy installed python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Create cache directory
RUN mkdir -p /app/cache

# Define the command to run the bot
CMD ["python", "main.py"]
