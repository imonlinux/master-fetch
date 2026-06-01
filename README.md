# Master Fetch

MCP server for web fetching with anti-bot bypass. Handles Cloudflare, DataDome, and Akamai. Extracts clean content via Trafilatura. Costs nothing.

Built on Scrapling. Uses three fetcher tiers — HTTP (curl_cffi), Dynamic (Playwright), Stealthy (Patchright) — with auto-escalation.

## Quick Start

```bash
git clone https://github.com/dondai1234/master-fetch.git
cd master-fetch
pip install -e .
playwright install chromium
```

Then point your agent at it:

```json
{
  "mcpServers": {
    "master-fetch": {
      "command": "master-fetch"
    }
  }
}
```

## How It Works

`smart_fetch` tries the fastest method first, then escalates:

1. **HTTP** — curl_cffi with TLS impersonation. Fast (0.7-2s). Works for most sites.
2. **Dynamic** — Playwright/Chromium. For JS-rendered content. Slower (~20s).
3. **Stealthy** — Patchright with Cloudflare solver. For protected sites. Slowest (~40s).

Content goes through Trafilatura for clean markdown extraction. Results are cached in SQLite (1hr TTL) so repeat fetches are instant.

## Tools

| Tool | What it does |
|------|-------------|
| `smart_fetch` | Auto-routed fetch. Use this 95% of the time. |
| `fetch` | HTTP-level fetch |
| `stealthy_fetch` | Full stealth with Cloudflare bypass |
| `bulk_fetch` | Parallel fetch |
| `bulk_stealthy_fetch` | Parallel stealth fetch |
| `screenshot` | Page screenshot |
| `open_session` / `close_session` | Persistent browser sessions |
| `cache_clear` | Clear cache |

## Requirements

- Python 3.11+
- Chromium (installed via `playwright install chromium`)
- No API keys, no accounts, no Docker.

## Limits

- DataDome + Cloudflare dual protection blocks all fetchers
- Reddit returns first-load content only (no infinite scroll)
- Dynamic/Stealthy tiers are slow due to browser startup
- Domain extraction uses simple heuristic (doesn't handle .co.uk/.com.au)

## License

MIT
