#!/usr/bin/with-contenv bashio
# Hound MCP Server startup script for Home Assistant

bashio::log.info "Starting Hound MCP Server..."

# Get options from Home Assistant
LOG_LEVEL=$(bashio::config 'log_level')
CACHE_TTL=$(bashio::config 'cache_ttl')
MAX_RESULTS=$(bashio::config 'max_results')

export PYTHONUNBUFFERED=1
export PIP_NO_CACHE_DIR=1
export NO_COLOR=1
export PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Set cache directory in persistent storage
export HOUND_CACHE_DIR=/data/.hound

bashio::log.info "Configuration: log_level=${LOG_LEVEL}, cache_ttl=${CACHE_TTL}, max_results=${MAX_RESULTS}"

# Start Hound MCP server
cd /app

exec python3 -m master_fetch.server \
    --http \
    --host 0.0.0.0 \
    --port 8765
