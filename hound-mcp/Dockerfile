ARG BUILD_FROM
FROM ${BUILD_FROM:-ghcr.io/home-assistant/base:latest}

# Install Python and build dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    jpeg-dev \
    zlib-dev \
    curl

# Install runtime libraries for dependencies
RUN apk add --no-cache \
    libgcc \
    libstdc++ \
    libffi \
    openssl \
    jpeg

# Set working directory
WORKDIR /app

# Copy Hound application files
COPY src/ /app/src/
COPY pyproject.toml /app/

# Install Hound with core dependencies only
# Browser and OCR deps (onnxruntime, etc.) unavailable on Alpine
# Hound will fall back to HTTP-only mode automatically
RUN pip install --no-cache-dir --break-system-packages -e .

# Copy run script
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Create data directory for cache
RUN mkdir -p /data/.hound && \
    chown -R root:root /data

# Labels for Home Assistant
LABEL io.hass.type="app" \
      io.hass.version="12.3.0"

CMD ["/run.sh"]
