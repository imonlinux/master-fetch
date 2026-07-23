# Hound MCP Server

[Hound](https://github.com/dondai1234/master-fetch) is a research-focused web fetcher with intelligent content extraction, caching, and anti-bot capabilities. This Home Assistant App runs Hound as an HTTP MCP (Model Context Protocol) server.

## Features

- **Smart Web Fetching** - Extracts clean, readable content from web pages with anti-bot detection
- **PDF Support** - Reads and processes PDF documents from URLs
- **Web Search** - Performs searches with intelligent result extraction and filtering
- **Site Crawling** - Deep-crawls websites for comprehensive content collection
- **Built-in Caching** - Reduces redundant fetches with configurable cache duration
- **MCP Protocol** - Serves as an HTTP MCP server for AI agent integration
- **Configurable** - Adjust log level, cache TTL, and result limits
- **Multi-Architecture** - Supports aarch64 (ARM64) and amd64 (x86_64)

## Installation

### Local Installation

1. **Copy the app directory** to your Home Assistant instance:
   - Via Samba: Navigate to `addons` and copy `hound-mcp/` folder
   - Via SSH: Copy to `/addons/hound-mcp/`

2. **In Home Assistant**:
   - Go to **Settings** → **Apps** → **App Store**
   - Click "Check for updates" in the three-dot menu
   - Look for "Hound MCP Server" in the **Local apps** section
   - Click **Install**, then **Start**

### From Repository (Future)

When published to the Home Assistant App store:
- Go to **Settings** → **Apps** → **App Store**
- Search for "Hound MCP Server" and install

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| **Log Level** | `info` | Logging verbosity: `info`, `debug`, `warning`, `error` |
| **Cache TTL** | `3600` | Cache time in seconds (0-86400, set 0 to disable) |
| **Max Results** | `10` | Maximum search results to return (1-50) |

### Configuration Notes

- **Cache TTL** - Higher values reduce bandwidth but may return stale content
- **Max Results** - Affects token usage for AI agents processing results
- **Log Level** - Use `debug` when troubleshooting connection issues

## Usage

### MCP Endpoint

The server runs on: `http://<home-assistant-ip>:8765/mcp`

For SSE (Server-Sent Events) connection: `http://<home-assistant-ip>:8765/sse`

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `mcp_smart_fetch` | Fetch and extract content from URLs with smart rendering |
| `mcp_smart_crawl` | Deep-crawl websites for multi-page content collection |
| `mcp_search` | Web search with result extraction and filtering |
| `mcp_read_pdf` | Extract text content from PDF documents |

### Example Tool Parameters

**mcp_smart_fetch:**
```yaml
url: "https://example.com/article"
focus: "machine learning"  # Optional: filter content relevance
cache_ttl: 7200            # Optional: override default cache
```

**mcp_search:**
```yaml
query: "Home Assistant automation examples"
num_results: 15             # Optional: override max_results config
```

### Integration with AI Agents

Configure your AI agent (Claude, OpenAI, etc.) to connect to the MCP endpoint:

**Endpoint URL:**
```
http://<your-home-assistant-ip>:8765/sse
```

The server will auto-discover and provide all available tools to the connected agent.

## Web Interface

Access the MCP endpoint directly in your browser:
```
http://<home-assistant-ip>:8765/mcp
```

This returns the MCP server manifest with available tools and schemas.

## Data & Storage

- **Cache Directory**: `/data/.hound` (persistent across restarts)
- **Configuration**: Managed by Home Assistant Supervisor
- **Backups**: Hot backups enabled (no service interruption)

## Troubleshooting

### App won't start

1. Check Supervisor logs:
   - **Settings** → **System** → **Logs** → Select "Supervisor"
2. Verify adequate disk space (>500MB recommended)
3. Ensure port 8765 is not in use by another service

### MCP connection refused

1. Confirm the app is running (green status in Apps)
2. Check firewall rules allow port 8765
3. Verify the URL format: `http://<ip>:8765/sse` (not HTTPS)

### High memory usage

Hound with Chromium browser may use 200-400MB:
- Reduce `max_results` in configuration
- Set shorter `cache_ttl` to limit cache size
- Consider using a dedicated device for HA if resources are limited

### Slow response times

- First fetch after restart may be slow (browser initialization)
- Check network connectivity
- Try `debug` log level to see detailed operation logs

## Support

- **Hound Project**: [github.com/dondai1234/master-fetch](https://github.com/dondai1234/master-fetch)
- **Issues**: Report at [GitHub Issues](https://github.com/dondai1234/master-fetch/issues)
- **Home Assistant Forums**: [community.home-assistant.io](https://community.home-assistant.io/)

## Version Information

- **App Version**: 12.3.0
- **Hound Upstream**: Matches [master-fetch v12.3.0](https://github.com/dondai1234/master-fetch/releases)

## License

This app includes Hound, which is licensed under the MIT License. See [upstream LICENSE](https://github.com/dondai1234/master-fetch/blob/master/LICENSE) for details.
