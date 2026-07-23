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
    cargo \
    rust \
    jpeg-dev \
    zlib-dev \
    curl

# Set working directory
WORKDIR /app

# Copy Hound application files
COPY src/ /app/src/
COPY pyproject.toml /app/
COPY README.md /app/

# Install Hound and its dependencies
RUN pip install --no-cache-dir --break-system-packages -e .[all]

# Install Playwright browsers
RUN python3 -c "from playwright.sync_api import sync_playwright; sync_playwright().start(); install()" || \
    pip install --break-system-packages playwright && \
    python3 -m playwright install chromium

# Create data directory for cache
RUN mkdir -p /data/.hound && \
    chown -R root:root /data

# Copy run script
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Labels for Home Assistant
LABEL io.hass.type="app" \
      io.hass.version="12.3.0"

CMD ["/run.sh"]
