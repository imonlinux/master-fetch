<div align="center">

# 🐕 Hound

**Give your AI agent the web. In 2 minutes. For $0.**

<p>
  <img src="https://img.shields.io/badge/cost-$0_forever-brightgreen" alt="$0 forever">
  <img src="https://img.shields.io/badge/bypass-Cloudflare-blue" alt="Cloudflare bypass">
  <img src="https://img.shields.io/badge/search-free_with_key-orange" alt="Free search with key">
  <img src="https://img.shields.io/badge/MCP-stdio-purple" alt="MCP stdio">
  <img src="https://img.shields.io/github/license/dondai1234/master-fetch" alt="MIT">
</p>

*Free MCP server. Fetch any page, search the web, bypass bot protection.*<br>
*No accounts. No Docker. No credit card.*

[Install now](#install) · [How it works](#how-it-works) · [Comparison](#comparison) · [Pi Agent setup](#for-pi-agent-users)

</div>

---

## What is this?

Hound is an MCP server that gives any AI agent web capabilities: fetch pages, bypass bot protection, search the web. You install it, tell your agent about it, and you're done. Two commands, one config entry.

It's built for people new to AI agents who just want their agent to read websites without paying for Exa or Tavily. If you've been copying URLs into chat boxes, this replaces that entirely.

**What you get:**
- Fetch any URL. If the site blocks simple HTTP, Hound automatically retries with a real browser. If that's blocked too, it switches to anti-bot stealth mode. You don't pick. It figures it out.
- Search the web. Free TinyFish API key required (sign up at [tinyfish.ai](https://tinyfish.ai), no credit card). Returns title, URL, and snippet for each result.
- Clean markdown output. Navigation, ads, and footers stripped automatically.
- Content caching. Same URL within an hour? Instant. No re-fetch.
- 40KB smart chunking. Large pages get truncated with a continuation offset so your agent can read the rest.
- Persistent browser sessions. The first dynamic/stealthy fetch takes a few seconds to launch the browser. After that, the browser stays open and every subsequent fetch on that session is fast.

**What you don't get (honest limits):**
- DataDome, Akamai Bot Manager, and Cloudflare Turnstile (interactive captcha): not bypassed. No free tool bypasses these. The next step up is a paid proxy service ($50+/mo).
- Reddit: first page of posts only. No infinite scroll.
- YouTube: minimal text content (it's a video platform, this is expected).

---

## Install

### Option A: Tell your AI agent to do it

Copy this prompt, paste it into your agent (OpenClaw, Hermes Agent, OpenCode, etc.):

**Full package (fetch + search):**
```
Install the Hound MCP server on this machine. Steps:

1. Run: pip install hound-mcp[all]
2. Run: playwright install chromium
3. Add Hound to the MCP config on this machine. The server name is "hound". The command is "hound" with no arguments. Create a backup of the config file before editing.
4. After setup, guide me to get a free TinyFish API key:

   Tell me: "Go to https://tinyfish.ai, click Get API Key, sign up (free, no credit card needed). Copy the key and paste it here."

   Wait for me to paste the key. Do not proceed until I provide it.
5. Once I give you the key, add it as environment variable TINYFISH_API_KEY in the hound server's MCP config.
6. Tell me: "Done. Restart your agent to apply. You should see smart_fetch and smart_search in your tools."
```

**Fetch only (no search, no API key needed):**
```
Install the Hound MCP server (fetch only) on this machine. Steps:

1. Run: pip install hound-mcp
2. Run: playwright install chromium
3. Add Hound to the MCP config on this machine. The server name is "hound". The command is "hound" with no arguments. Create a backup of the config file before editing.
4. Tell me: "Done. Restart your agent to apply. You should see smart_fetch in your tools."
```

That's it. 2 commands + 1 config entry. Your agent handles the rest.

### Option B: Manual

```bash
# Install
pip install hound-mcp[all]        # fetch + search (requires TinyFish API key)
# or
pip install hound-mcp              # fetch only

# Browser (required for JS/anti-bot pages)
playwright install chromium
```

Add to your MCP config file:

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

Get your free TinyFish API key at [tinyfish.ai](https://tinyfish.ai). No credit card needed. Search will not work without this key.

Fetch-only users can omit the `env` block entirely.

### Updating

```bash
pip install --upgrade hound-mcp[all]
```

Restart your agent. MCP servers launch fresh each session, so the new version loads automatically.

---

## How it works

### Smart fetch: one tool, three tiers

`smart_fetch` tries the fastest method first. If blocked, it escalates automatically:

| Tier | Engine | Speed | When it's used |
|------|--------|-------|----------------|
| HTTP | curl_cffi (Chrome TLS fingerprint) | 1-3s | Most websites. Works for anything static. |
| Dynamic | Playwright + Chromium | 3-8s | JS-heavy pages (SPAs, React/Angular sites). |
| Stealthy | Patchright + Cloudflare solver | 5-13s | Pages behind bot protection. |

**How escalation works:**
1. Try HTTP. If the page returns a JS-only shell like "You need to enable JavaScript", escalate to dynamic.
2. Try dynamic. If the page returns a bot challenge or JS-disabled placeholder, escalate to stealthy.
3. Try stealthy. Full anti-bot bypass with fingerprint spoofing and Cloudflare solving.
4. If all tiers fail, return a clear error telling you what was tried.

**First fetch is slower, then it's fast:**
The dynamic and stealthy tiers need to launch a real browser. The first fetch on a fresh session takes 3-13 seconds to start Chromium. After that, the browser stays open and reused, so subsequent fetches are fast. Hound manages this automatically.

**What it remembers:**
- Domain intelligence: after fetching a domain, Hound remembers which tier works. Next time, it skips straight there.
- Content cache: SQLite, 1 hour TTL. Repeat fetches are instant.
- The `error` field signals content quality: `js_shell_detected`, `geo_redirect_detected`, `bot_challenge_detected`. Your agent knows the fetch failed without parsing the content.

### Smart search: web search via TinyFish

`smart_search` searches the web via TinyFish API. Requires a free API key set as `TINYFISH_API_KEY`. Returns title, URL, and snippet for each result. Results cached for 5 minutes.

### Content chunking

Large pages get truncated at 40KB with a continuation notice:

```
[Content truncated: received 40,000 of 60,000 chars (offset 0-40,000).
20,000 chars remaining. Call smart_fetch again with offset=40000 to get the next chunk.]
```

Your agent calls `smart_fetch` with `offset=40000` and gets the next chunk instantly from cache. No re-fetch needed.

---

## Tools

| Tool | What it does |
|------|-------------|
| `smart_fetch` | Fetch any URL. Auto-routes to the right tier. Start here. |
| `smart_search` | Search the web. Requires TINYFISH_API_KEY. |
| `get` / `bulk_get` | HTTP-only fetch. Fast, for known-static sites. |
| `fetch` / `bulk_fetch` | Dynamic fetch. Playwright browser, JS rendering. |
| `stealthy_fetch` / `bulk_stealthy_fetch` | Anti-bot fetch. Patchright + Cloudflare solver. |
| `screenshot` | Full-page screenshot of any URL (needs open session). |
| `open_session` / `close_session` / `list_sessions` | Persistent browser sessions. |
| `cache_clear` | Clear the content cache. |

---

## Comparison

### Runs on your machine (install once, $0 forever, no cloud dependency)

| | Fetches pages | Anti-bot bypass | Web search | Needs account |
|---|---|---|---|---|
| **Hound** | 3-tier auto (HTTP -> browser -> stealth) | Built-in (Cloudflare, basic bot walls) | TinyFish (free key) | No (fetch), key for search |
| **Crawl4AI** | Playwright browser | Stealth mode (fingerprint spoofing, basic) | No | No |
| **Firecrawl** (self-hosted) | HTTP + browser scrape | No | Search endpoint | Yes (API key) |

### Cloud APIs (free tiers, needs account, then pay at scale)

| | Fetches pages | Anti-bot bypass | Web search | Free tier limit |
|---|---|---|---|---|
| **Bright Data** | Scrape with Web Unlocker | Proxy infrastructure | Yes | 5,000 req/month |
| **Exa** | /contents endpoint | No | Yes | 1,000 req/month |
| **Tavily** | /extract endpoint | No | Yes | 1,000 credits/month |
| **Firecrawl** (cloud) | HTTP + browser scrape | Yes | Yes | 1,000 pages/month |
| **Jina Reader** | URL to markdown (r.jina.ai) | No | No | Free tier |

Hound and Bright Data Free are the only options that combine anti-bot bypass, web search, and MCP in one package at $0. Hound runs on your hardware (no cloud, no account for fetch). Bright Data runs on their proxy infrastructure (5K req/mo free, account required).

---

## For Pi Agent Users

### One-prompt install for Pi

Paste this into your Pi agent to install Hound:

```
Install the Hound MCP server for web fetching and search. Steps:

1. Run: pip install hound-mcp[all]
2. Run: playwright install chromium
3. Check if pi-mcp-adapter is installed by looking for "pi-mcp-adapter" in the output of: pi list. If not installed, run: pi install npm:pi-mcp-adapter
4. Open ~/.pi/agent/mcp.json. If the file doesn't exist, create it. If it exists but doesn't have "mcpServers", add the key. Add the hound server entry inside mcpServers:

   "hound": {
     "command": "hound",
     "transport": "stdio",
     "lifecycle": "eager"
   }

   So the file should look like:
   {
     "mcpServers": {
       ...existing servers...,
       "hound": {
         "command": "hound",
         "transport": "stdio",
         "lifecycle": "eager"
       }
     }
   }

   Make a backup before editing.
5. After config, tell me: "Go to https://tinyfish.ai, click Get API Key, sign up (free, no credit card needed). Copy the key and paste it here."
6. Wait for me to paste the TinyFish key. Do not proceed until I provide it.
7. Once I give you the key, add an "env" field inside the hound entry with TINYFISH_API_KEY:

   "hound": {
     "command": "hound",
     "transport": "stdio",
     "lifecycle": "eager",
     "env": {
       "TINYFISH_API_KEY": "<the key I pasted>"
     }
   }

8. Tell me: "Done. Run /reload in Pi, then /mcp to verify hound is connected. You should see smart_fetch and smart_search as tools."
```

Fetch only (no search, no API key):

```
Install the Hound MCP server (fetch only) for web fetching. Steps:

1. Run: pip install hound-mcp
2. Run: playwright install chromium
3. Check if pi-mcp-adapter is installed by looking for "pi-mcp-adapter" in the output of: pi list. If not installed, run: pi install npm:pi-mcp-adapter
4. Open ~/.pi/agent/mcp.json. If the file doesn't exist, create it. If it exists but doesn't have "mcpServers", add the key. Add the hound server entry inside mcpServers:

   "hound": {
     "command": "hound",
     "transport": "stdio",
     "lifecycle": "eager"
   }

   So the file should look like:
   {
     "mcpServers": {
       ...existing servers...,
       "hound": {
         "command": "hound",
         "transport": "stdio",
         "lifecycle": "eager"
       }
     }
   }

   Make a backup before editing.
5. Tell me: "Done. Run /reload in Pi, then /mcp to verify hound is connected. You should see smart_fetch as a tool."
```

---

## Requirements

- Python 3.11+
- Chromium (installed via `playwright install chromium`)
- Search: `pip install hound-mcp[all]` + free TinyFish API key

## License

MIT
