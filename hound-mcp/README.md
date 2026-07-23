# Hound MCP Server for Home Assistant

[Hound](https://github.com/dondai1234/master-fetch) is a research-focused web fetcher with intelligent extraction, caching, and anti-bot capabilities. This Home Assistant App runs Hound as an HTTP MCP (Model Context Protocol) server, making it accessible to AI agents and assistants.

## Features

- **Smart Web Fetching**: Extracts clean content from web pages with anti-bot detection
- **PDF Support**: Reads and processes PDF documents
- **Search Integration**: Performs web searches with result filtering
- **Caching**: Built-in caching to reduce redundant fetches
- **MCP Protocol**: Serves as an HTTP MCP server for AI agent integration
- **Configurable**: Adjust log level, cache TTL, and result limits

## Installation

1. In Home Assistant, go to **Settings** → **Apps** → **App Store**
2. Click "Check for updates" in the three-dot menu
3. Look for "Hound MCP Server" in the "Local apps" section
4. Click **Install** and then **Start**

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| Log Level | `info` | Logging verbosity (info, debug, warning, error) |
| Cache TTL | `3600` | Cache time in seconds (0 to disable) |
| Max Results | `10` | Maximum search results to return |

## Usage

The MCP server runs on `http://<home-assistant-ip>:8765/mcp`

### For AI Agents

Configure your AI agent to connect to:
```
http://<your-home-assistant-ip>:8765/sse
```

The server provides these MCP tools:
- `mcp_smart_fetch` - Fetch and extract content from URLs
- `mcp_smart_crawl` - Deep-crawl websites
- `mcp_search` - Web search with result extraction
- `mcp_read_pdf` - Extract content from PDF URLs

## Support

For issues and feature requests, please visit:
- [Hound Repository](https://github.com/dondai1234/master-fetch)
- [Home Assistant Forums](https://community.home-assistant.io/)
