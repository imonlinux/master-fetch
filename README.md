# Hound

<p align="center">
  <img src="https://img.shields.io/badge/cost-$0%20forever-brightgreen" alt="$0 forever">
  <img src="https://img.shields.io/badge/bypass-Cloudflare-blue" alt="Cloudflare bypass">
  <img src="https://img.shields.io/badge/mcp-stdio-purple" alt="MCP">
  <img src="https://img.shields.io/github/license/dondai1234/master-fetch" alt="MIT">
</p>

<p align="center">
  <strong>Web research for AI agents. Fetch any page. Search the web. $0 forever.</strong>
</p>

<p align="center">
  Bypasses Cloudflare bot protection. Extracts clean content. Built-in web search.<br>
  No API keys needed. No accounts. No Docker. One MCP server.
</p>

> The fetch engine is called Master Fetch internally (`master_fetch` module). The product is Hound. Repo: `dondai1234/master-fetch`.

---

## Tools

| Tool | Does |
|------|------|
| `smart_fetch` | Fetches any URL. Auto-escalates if blocked. Returns clean markdown. |
| `smart_search` | Searches the web. Returns title, URL, snippet. Free, 30/min. |
| `fetch` / `stealthy_fetch` | Manual tier selection. |
| `screenshot` | Full-page screenshot of any URL. |
| `open_session` / `close_session` | Persistent browser sessions. |
| `bulk_fetch` / `bulk_stealthy_fetch` | Parallel fetching. |
| `cache_clear` | Clear the cache. |

---

## Install via AI agent

Paste the prompt below. The agent does the work. You just provide an API key when asked.

### Full package (fetch + search)

```
Install Hound MCP server on this machine. Steps:

1. Run: pip install hound-mcp[all]
2. Run: playwright install chromium
3. Add Hound to the MCP config on this machine. The server name is "hound". The command is "hound" with no arguments. Create a backup of the config file before editing.
4. After setup, guide me to get a free TinyFish API key:

   Tell me: "Go to https://tinyfish.ai, click Get API Key, sign up (free, no credit card needed). Copy the key and paste it here."

   Wait for me to paste the key. Do not proceed until I provide it.
5. Once I give you the key, add it as an environment variable TINYFISH_API_KEY in the hound server's MCP config.
6. Tell me: "Done. Restart your agent to apply. You should see smart_fetch and smart_search in your tools."
```

### Fetch only

```
Install Hound MCP server (fetch only) on this machine. Steps:

1. Run: pip install hound-mcp
2. Run: playwright install chromium
3. Add Hound to the MCP config on this machine. The server name is "hound". The command is "hound" with no arguments. Create a backup of the config file before editing.
4. Tell me: "Done. Restart your agent to apply. You should see smart_fetch in your tools."
```

---

## Updating

```bash
pip install --upgrade hound-mcp[all]
```

Then restart your agent. MCP servers launch fresh on each session, so the new version is picked up automatically. No config changes needed.

---

## Manual install

```bash
# Full package
git clone https://github.com/dondai1234/master-fetch.git
cd master-fetch
pip install -e .[all]
playwright install chromium

# Fetch only
git clone https://github.com/dondai1234/master-fetch.git
cd master-fetch
pip install -e .
playwright install chromium
```

Add to MCP config (`mcpServers` / `mcp.servers`):

```json
{
  "mcpServers": {
    "hound": {
      "command": "hound",
      "env": {
        "TINYFISH_API_KEY": "sk-tinyfish-..."
      }
    }
  }
}
```

A built-in API key is included and works out of the box. Get your own free key at [tinyfish.ai](https://tinyfish.ai) for higher limits.

---

## How fetching works

`smart_fetch` tries the fastest method first, escalates only if blocked:

| Tier | Engine | Speed | Best for |
|------|--------|-------|----------|
| HTTP | curl_cffi (Chrome TLS) | 1-3s | Most websites |
| Dynamic | Playwright/Chromium | 3-8s | JS-heavy pages |
| Stealthy | Patchright + solver | 5-13s | Pages behind bot protection |

It remembers which tier works per domain. Results are cached (SQLite, 1hr TTL) so repeat fetches are instant.

---

## Fetch comparison

Hound's fetch engine (Master Fetch) vs every alternative. Tested live, June 2026.

| | Hound | Exa | Tavily | Crawl4AI | Jina |
|---|---|---|---|---|---|
| **Cloudflare bypass** | ✅ Built-in | ❌ | ❌ | ⚠️ Needs external proxies | ❌ |
| **Auto-escalation** | ✅ HTTP→Browser→Stealth | ❌ | ❌ | ⚠️ Proxy rotation | ❌ |
| **Domain intelligence** | ✅ Remembers per-domain | ❌ | ❌ | ❌ | ❌ |
| **Content caching** | ✅ SQLite, instant hits | ❌ | ❌ | ❌ | ❌ |
| **Persistent sessions** | ✅ 2x speed on repeats | ❌ | ❌ | ❌ | ❌ |
| **Retry logic** | ✅ Exponential backoff | ❌ | ❌ | ✅ Via proxies | ❌ |
| **Runs on your hardware** | ✅ | ❌ | ❌ | ✅ | ❌ |
| **Cost** | $0 forever | 1K/mo free | 1K/mo free | $0 | Free tier |
| **MCP native** | ✅ Stdio | ❌ | ❌ | ❌ | ❌ |

**Real-world example:** Fetching a Cloudflare-protected page:
- Hound: escalates to stealthy, solves challenge, returns full content (5-13s)
- Exa/Tavily: HTTP 403. No fallback. Returns error.
- Crawl4AI: 403 unless you bring your own proxy service. Returns error or empty.
- Jina: 403. Returns error.

## Full package comparison

| | Hound | Exa | Tavily |
|---|---|---|---|
| **Web search** | ✅ 30/min | ✅ | ✅ |
| **Content fetching** | ✅ + anti-bot bypass | ✅ | ✅ |
| **All-in-one MCP** | ✅ One server | ❌ Two APIs | ❌ Two APIs |
| **Cost** | $0 forever | 1K/mo free | 1K/mo free |

---

## Limits

- DataDome, Akamai Bot Manager, and Cloudflare Turnstile (interactive): not bypassed. No free tool can bypass these. Hound is the closest you can get at $0. The next step up is paid proxy services or enterprise scraping APIs.
- Reddit: stealthy tier, first page only (no infinite scroll)
- YouTube: minimal text (expected for video pages)

## Requirements

- Python 3.11+
- Chromium: `playwright install chromium`
- Search: `pip install hound-mcp[all]`

## License

MIT
