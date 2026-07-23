# Hound MCP Server Documentation

## Architecture

This Home Assistant App runs [Hound](https://github.com/dondai1234/master-fetch) as an HTTP MCP server in a containerized environment.

## MCP Tools

### mcp_smart_fetch

Fetches and extracts content from a URL with smart content extraction.

**Parameters:**
- `url` (required): The URL to fetch
- `focus` (optional): Query-focused extraction using BM25
- `cache_ttl` (optional): Override cache duration
- `force_fetcher` (optional): Force specific fetcher (`http` or `stealthy`)

### mcp_smart_crawl

Deep-crawls a website to extract multiple pages.

**Parameters:**
- `url` (required): Starting URL (stays on domain)
- `max_pages` (optional): Maximum pages to crawl (default: 10)
- `max_depth` (optional): Maximum crawl depth (default: 2)

### mcp_search

Performs web search and extracts content from results.

**Parameters:**
- `query` (required): Search query
- `num_results` (optional): Number of results (default: 10)
- `include_links` (optional): Include citation links
- `include_media` (optional): Include image URLs

### mcp_read_pdf

Extracts text content from PDF files.

**Parameters:**
- `url` (required): URL to PDF file
- `pages` (optional): Page range like "1-5" or "1,3,5-7"
- `password` (optional): Password for encrypted PDFs

## Data Storage

- **Cache Directory**: `/data/.hound` (persistent across restarts)
- **Configuration**: `/data/options.json` (managed by Home Assistant)

## Networking

- **Port**: 8765/TCP
- **MCP Endpoint**: `http://<ip>:8765/mcp`
- **SSE Endpoint**: `http://<ip>:8765/sse`

## Troubleshooting

### Container won't start

Check the Supervisor logs for errors:
1. Go to **Settings** → **System** → **Logs**
2. Select "Supervisor" from the dropdown
3. Look for error messages

### MCP connection refused

Ensure:
1. The app is running (green indicator in Apps)
2. Port 8765 is not blocked by firewall
3. Use correct URL format: `http://<ip>:8765/sse`

### High memory usage

Hound with Chromium may use 200-400MB. If running on limited hardware:
1. Reduce `max_results` in configuration
2. Set shorter `cache_ttl` to limit cache size
3. Consider using a dedicated device

## Development

For building and testing locally:

```bash
# Build the app
docker build -t hound-mcp:test hound-mcp/

# Run the container
docker run -d --name hound-test \
  -p 8765:8765 \
  -v hound-cache:/data/.hound \
  hound-mcp:test

# Test MCP endpoint
curl http://localhost:8765/mcp
```
