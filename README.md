<div align="center">

# 🐕 Hound

<img src="https://raw.githubusercontent.com/dondai1234/master-fetch/master/docs/hound-hero.png" alt="Hound - web fetch, anti-bot, PDF, crawl and search for AI agents" width="896">

**Give your AI agent the web. $0. Two commands. ~2K tokens.**

Fetch, crawl, bypass bot walls, read PDFs (even scanned), interact with pages, search the web.
No accounts. No Docker. No API keys. Runs on your machine.

[![PyPI](https://img.shields.io/pypi/v/hound-mcp.svg)](https://pypi.org/project/hound-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/hound-mcp.svg)](https://pypi.org/project/hound-mcp/)
[![License: MIT](https://img.shields.io/pypi/l/hound-mcp.svg)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/dondai1234/master-fetch/ci.yml?label=CI)](https://github.com/dondai1234/master-fetch/actions/workflows/ci.yml)
[![Downloads](https://static.pepy.tech/badge/hound-mcp)](https://pepy.tech/project/hound-mcp)

```bash
pip install hound-mcp[all] && playwright install chromium
```

[Install](#install) · [Tools](#tools) · [Features](#features) · [How it works](#how-it-works) · [Comparison](#comparison-free-tools) · [Honest limits](#honest-limits)

</div>

---

## What it is

Hound is an [MCP](https://modelcontextprotocol.io) server that gives any AI agent (Claude Code, Cursor, OpenCode, Hermes, Pi, anything that speaks MCP) full web research from a single local process: **fetch + crawl + anti-bot + PDF/OCR + page interaction + search**. It is built so the agent masters it the moment it connects, and so every response tells the agent exactly what to do next.

It is **$0 forever**, MIT-licensed, and runs locally. No API keys, no accounts, no per-request billing, no data sent to a third-party scraper. Search is keyless and local too, scraping public engines and reranking on your machine.

> Hound is for the agent itself. You install it once; the agent calls it whenever it needs the web.

---

## Why agents love it

Most MCP servers dump 3 to 5K tokens of tool schema into the agent's context just to exist (some ship 12 to 60+ tools), and return raw blobs the agent has to interpret. Hound is different:

- **Connect-time mastery.** On `initialize`, Hound sends a concise `instructions` block (paid once, not per turn) that gives the agent the 4-tool mental model, the #1 workflow (search → fetch the high-relevance results → synthesize), and the known limits. The agent is effective on turn one.
- **Every response is actionable.** Each fetch returns `content_ok` (trust the content only if true), `next_action` (the obvious next call: paginate, bypass robots, switch sources), `summary` (a one-line status), `fetched_at`, and structured `metadata` (title, author, date, canonical). Each search result returns `relevance_score` + `fetch_relevance`. Agents branch on structured fields instead of guessing from error text.
- **~2K tokens for all 6 tools.** Hand-crafted tool definitions, no Pydantic schema bloat. More capability than tools shipping 5K+ tokens.

---

## Tools

| Tool | What it does |
|------|--------------|
| `smart_fetch` | Fetch any URL. HTTP first, auto-escalates to the anti-detect browser if blocked. Bulk via `urls`. PDFs auto-extracted to structured markdown with `quality_score` + `table_of_contents`; scanned PDFs AND CID-corrupted fonts auto-OCR. Narrow with `css_selector`. Focus with `focus`. Interact with `actions` (click/fill/scroll). Paginate with `offset`. |
| `smart_crawl` | Best-first same-domain crawl: returns each page as markdown with `content_ok` + `page_type` (article/list/js_shell). List/index pages come back as a structured link list. `discover_only=true` for a URL map; `crawl_urls` to fetch a chosen subset. `focus` prioritizes + filters. Caps on pages/depth/tokens/time. |
| `smart_search` | Local keyless web search. No API key, no account. Scrapes DuckDuckGo + Bing + Wikipedia in parallel, merges + ranks. `relevance_score` + `fetch_relevance` (high/med/low) per result. Modes: `keyword` (BM25), `neural` (local ONNX cross-encoder), `deep` (peek real page content, rerank on it), `find_similar` (pass `url=`, find pages similar to it). `expand=N` autoretrieval for niche recall. Research mode (`fetch_content=true`) auto-fetches the top results' full content in one call. |
| `screenshot` | Capture a page as an image. For multimodal agents (canvas, image-of-text, visual layout). Session auto-managed. |
| `cache_clear` | Clear the fetch cache. `all=true` wipes everything. |
| `version` | Installed version + update status. |

---

## Features

### 🕷️ Crawl a whole site (`smart_crawl`)
Read all the docs on a site, or scrape a section, in one call. `smart_crawl` walks same-domain links from a start URL in a **best-first** order: discovered URLs are scored by focus relevance + content-likelihood (docs/guide/api boosted, login/submit/cart penalized) + shallow depth, so content pages are crawled before junk when the budget is tight. Extraction is **content-adaptive** per page: article/docs pages -> trafilatura main content; list/index pages (Hacker News, aggregators, directory pages) -> a structured `* [title](url)` link list (not an empty page); JS shells are detected and reported honestly. URLs are normalized (trailing slash, tracking params) so `/docs` and `/docs/` are never crawled twice. `discover_only=true` returns the URL map; pass `crawl_urls=[...]` to fetch a chosen subset in a second phase. `path_include`/`path_exclude` scope it. `focus="query"` prioritizes relevant pages AND focus-filters each page. Caps on pages/depth/tokens plus an overall time deadline so one slow page can't hang the crawl. Same-domain only by default; network errors report status `-1`; `fetched_at` per page; `cache_ttl=0` forces fresh.

### 🛡️ Anti-bot, built in (and it powers search too)
`smart_fetch` tries plain HTTP first (fast, ~1s). If the site blocks HTTP or serves a JavaScript shell, it auto-escalates to a **Patchright** anti-detect browser with Cloudflare challenge solving. Two tiers, nothing to configure. JS-only SPAs that return an empty shell over HTTP are detected and escalated automatically. The same warm stealthy browser is the search engine scraper's anti-bot tier: when DuckDuckGo/Bing/Google rate-limit or CAPTCHA the HTTP scraper, Hound renders the results page in the warm browser and parses that instead. No keyless search tool does this.

### 🔎 Local keyless search (`smart_search`) + research mode
No API key, no account, no third-party service. `smart_search` scrapes **DuckDuckGo + Bing + Wikipedia** in parallel (add `google` via `engines=`), merges, dedups by normalized URL, and ranks. Every result carries `relevance_score` (0-1) and `fetch_relevance` (high/med/low) plus a `fetch_hint` so the agent fetches 1 to 2 results instead of all 10. Filters: `site`/`exclude_sites` (domain include/exclude), `location`/`language`/`region` (geo), `page`, `freshness` (day/week/month/year).

Four rerank modes:
- **`keyword`** (BM25 over title + snippet) is the baseline, always available, even on the lean install.
- **`neural`** reranks with a local ONNX cross-encoder (`ms-marco-MiniLM-L-6-v2`, Apache-2.0) running on the `onnxruntime` Hound already ships for OCR. The model downloads once on first use (~80MB, cached), not bundled. Exa-style semantic ranking, $0, on your machine.
- **`deep`** (the flagship) peeks each candidate's REAL fetched page content (cheap HTTP + trafilatura) and reranks on the actual page text, not the engine snippet. Only possible because Hound owns the fetch layer. The top results include a `peek` (a short content extract) so the agent can judge relevance before fetching. `auto` is used by default; research mode auto-uses `deep`.
- **`find_similar`** (pass `url=`) fetches a page you like, derives a query from it, and reranks candidates against that source page's content. Exa's find-similar, local.

**`expand=N`** (1-5) is autoretrieval: Hound generates N sub-query variants locally (no external LLM) and runs them in parallel across engines, then merges + dedups. Boosts recall for niche queries. **Research mode** (`fetch_content=true`) searches and bulk-fetches the top-N results' full content in this one call (auto-deep reranked), each with `content_ok`, replacing the 3 to 5 call search → fetch loop with one call.

Honest posture (same as SearXNG/ddgs): public engines may rate-limit or CAPTCHA. Hound mitigates with browser-impersonated TLS, a real user agent, multi-engine fallback, the warm stealthy browser, and result caching. `engine_blocked` in the response tells the agent which engines were blocked.

### 📄 PDF + scanned-PDF OCR + CID-recovery
`smart_fetch` detects a PDF (by content-type **or** `%PDF` magic bytes) and extracts it to **structured markdown** using `pdfplumber` (MIT, no AGPL baggage): multi-column reading order, real **tables as markdown tables**, font-size heading detection, de-hyphenated paragraphs, a metadata header, and `--- Page N ---` markers. Pass `pages="1-5"` to extract a subset.

**The flagship trick — CID-corruption auto-OCR.** Academic papers embed font subsets without a Unicode map, so extractors emit `(cid:71)(cid:302)...` garbage for figures/diagrams/math. But the glyphs *render* correctly. Hound detects CID-garbage pages, renders them via `pypdfium2`, and OCRs them with `rapidocr`, recovering the real text automatically. Scanned/image-only PDFs (and image-only web pages) are auto-OCR'd too. Equations/figures are OCR'd as visible symbols with an honest marker (use a vision tool for precise LaTeX). All pure-pip, no system binary, with `[all]`.

**Honest quality signals.** Every PDF response carries `quality_score` (0.0-1.0, readable-char ratio) and a `content_ok` that reflects it, so a garbled extraction is flagged, not silently trusted. `table_of_contents` is populated from the PDF outline when present, and `metadata` (title/author/subject/keywords/creator/producer/dates) is available programmatically. `include_media=true` reports per-page embedded-image metadata for multimodal agents. Encrypted PDFs accept a `password`. A `.pdf` URL that returns a login/paywall page is reported as `auth_required`, not extracted as content.

### 🎯 Query-focused extraction (`focus`)
`smart_fetch(url, focus="...")` returns only the BM25-relevant blocks (paragraphs, headings, tables) for your query. On a long page this cuts context 80%+ with no re-fetch (it runs post-cache, so one cached page serves any focus query). Re-pass the same `focus` when paginating with `offset`.

### 🖱️ Page interaction (`actions`)
Content behind a click, a search form, a "load more" button, or infinite scroll: `smart_fetch(url, actions=[{click:"button.load-more"}, {fill:{selector:"#q", text:"x"}}, {press:"Enter"}, {wait:500}, {scroll:3}, {wait_selector:".item"}])`. Actions run on the stealthy browser after load, before extraction. Forces the stealthy tier and bypasses cache.

### 🏷️ Metadata on every response
Every HTML fetch carries structured `metadata` for citation and relevance: title, description, site name, type, image, canonical URL, language, published time, author (from OpenGraph, JSON-LD, the canonical link, and `<title>`).

### 🐕 Reddit, optimized
Reddit URLs are auto-rewritten to old.reddit.com (7x smaller pages) and skip straight to the stealthy browser (www.reddit.com walls HTTP). Subreddit listings are parsed into structured posts (title, score, comments, author, domain) from canonical per-post data attributes, with promoted ads filtered out and sticky/NSFW posts tagged.

### ⚡ One warm browser, always ready
A single stealthy Chrome warms up when the server starts and stays alive for the whole session, so stealthy fetches, crawls, screenshots, and engine-scrape escalations skip the 3 to 5s cold start. Only one browser instance ever runs; it closes cleanly when the agent harness closes. Pages are closed after each fetch and resource loading is dropped, so idle memory stays near the browser baseline.

### 💾 Smart caching
SQLite cache keyed by URL + extraction type + css_selector + pages (and by query + filters + mode for search). Uses the lesser of the stored and requested TTL. WAL mode for concurrent access. **Bad content is never cached** (JS shells, bot challenges, error statuses re-fetch instead of freezing broken pages). A size cap evicts the oldest entries so a long-lived agent's cache can't grow unbounded.

---

## Install

```bash
pip install hound-mcp[all]      # fetch + crawl + keyless search + PDF + OCR + neural rerank (recommended)
playwright install chromium     # the anti-detect browser engine
```

Lean install (fetch + crawl + keyless keyword search; no neural rerank, no PDF/OCR):

```bash
pip install hound-mcp
playwright install chromium
```

Verify:

```bash
hound -v        # version + update status
hound -u        # update to latest
```

---

## Tell your agent to install it

Paste this into your agent:

```
Install the Hound MCP server on this machine. Follow every step. Do not skip any.

1. Figure out which agent harness you are running on (OpenCode, Hermes, Pi, etc). Then find: (a) where the MCP config file lives, and (b) what format it expects for adding a local MCP server. Read the harness docs if needed. Do not guess.

2. Run: pip install hound-mcp[all]
   Then run: playwright install chromium (But only if it isnt installed already, verify first about its existence)
   If either fails, stop and tell the user.

3. Find the MCP config file from step 1 and back it up before editing. Add a new MCP server named "hound" with command "hound", no arguments, in the format your harness requires. No API keys or environment variables are needed (search is keyless and local).

4. Save the file. Tell the user to restart the agent. After restart, smart_fetch, smart_crawl, smart_search, screenshot, cache_clear and version should be available.
```

### For Pi agent users only

```
Install the Hound MCP server. Follow every step. Do not skip any.

1. Run: pip install hound-mcp[all]
   Then run: playwright install chromium (But only if it isnt installed already, verify first about its existence)
   If either fails, stop and tell the user.

2. Check pi-mcp-adapter: pi list. If not installed: pi install npm:pi-mcp-adapter

3. Backup ~/.pi/agent/mcp.json, then add this inside mcpServers:
   "hound": { "command": "hound", "transport": "stdio", "lifecycle": "eager" }
   No API keys or environment variables are needed (search is keyless and local).

4. Tell the user: "Run /reload, then /mcp to verify. smart_fetch, smart_crawl and smart_search should be available."
```

---

## How it works

<div align="center">
<img src="https://raw.githubusercontent.com/dondai1234/master-fetch/master/docs/flow.svg" alt="smart_fetch pipeline: HTTP to stealthy escalation, extract, agent-optimized response; PDF and crawl branches" width="760">
</div>

- `smart_fetch` checks cache + robots, tries HTTP, escalates to the stealthy Patchright browser on a block/JS-shell/403/503, then extracts and enriches. PDFs branch to pdfplumber (with CID-garbage + scanned auto-OCR via pypdfium2+rapidocr, quality_score, ToC). `smart_crawl` reuses the same pipeline across a same-domain best-first walk with content-adaptive per-page extraction.
- `smart_search` scrapes DuckDuckGo + Bing + Wikipedia in parallel (browser-impersonated HTTP, escalating to the warm stealthy browser if an engine blocks), merges + dedups, then reranks (keyword BM25 / neural ONNX cross-encoder / deep content-aware / find_similar).
- The stealthy browser is pre-warmed at startup and reused, so escalation is fast.
- Content over 40KB is chunked; the response gives `next_offset` so the agent pages through with one more call (served instantly from cache).

---

## Comparison: free tools

Hound is compared only to other **free** ways to give an agent web research. The free alternatives stop being comparable: none combine crawl + anti-bot + PDF/OCR + interaction + keyless local search + agent-optimized responses in one $0 local install. Only paid services (Bright Data, ZenRows, Firecrawl paid, Exa) can compete on hard anti-bot or neural search at scale, and they cost money, require accounts, and route your data through their servers.

| | **Hound** | Crawl4AI | Jina Reader | Firecrawl (OSS / free tier) | DIY Playwright |
|---|---|---|---|---|---|
| **Price** | $0 forever | $0 (self-host) | free, rate-limited | $0 self-host / 1K pages free | $0 (your time) |
| **License** | MIT (no attribution) | Apache-2.0 (attribution required) | proprietary | AGPL-ish / cloud | n/a |
| **Account / API key** | none | none | optional free key (20 RPM w/o, 200 RPM w/) | cloud needs account + key | none |
| **Runs locally** | yes | yes | no (their API) | self-host: yes (Redis + Docker) | yes |
| **Anti-bot / Cloudflare** | built-in (Patchright) | limited (stealth mode) | none | not by default | none |
| **Deep crawl (site walk)** | yes (best-first, depth/budget, map mode) | yes (deep crawl) | no | yes (cloud) | build it |
| **PDF → structured markdown** | yes (tables, headings, pages subset) | partial (buggy on some PDFs) | yes (native) | yes (cloud: + OCR) | build it |
| **Scanned-PDF / image OCR** | yes (rapidocr, pure-pip) | no | no | yes (cloud paid) | build it |
| **Page interaction (click/fill/scroll)** | yes (`actions`) | hooks (code) | no | yes (cloud `/interact`) | build it |
| **Query-focused extraction** | yes (`focus`, BM25) | yes (BM25 filter) | no | no | build it |
| **Web search** | yes (keyless local, no key) | no | yes | no | no |
| **Search rerank** | neural + deep (content-aware) + find_similar + autoretrieval | BM25 | none | none | n/a |
| **Search anti-bot (engine scraping)** | yes (warm stealthy browser) | n/a | n/a | n/a | n/a |
| **Metadata (OG/JSON-LD/date/author)** | yes | partial | partial | yes | build it |
| **Agent signals (`content_ok`/`next_action`/`summary`/`relevance_score`)** | yes | no | no | no | no |
| **Connect-time `instructions`** | yes | no | no | no | no |
| **MCP server** | yes (official) | community | yes (official) | yes (official) | build it |
| **Token cost (tools/list)** | ~2K (6 tools) | varies | n/a | varies (12 tools) | n/a |

**The takeaway:** Crawl4AI is the closest free competitor (self-host Python, local, no key, has crawl + BM25), but it has no web search, no scanned-PDF OCR, no page interaction, no agent-optimized response signals, and it's Apache-2.0 (attribution required) where Hound is MIT. Jina Reader is the easiest (prefix a URL) and handles PDFs, but has **no anti-bot, no crawl, no interaction, no local search**, routes everything through Jina's servers, and is rate-limited. Firecrawl's open-source version has no anti-bot by default and the generous features live in the paid cloud. **Hound is the only free tool that combines crawl, built-in Cloudflare bypass, scanned-PDF OCR, page interaction, query-focused extraction, keyless local search with neural + content-aware + find-similar rerank, and a response shape designed for agents**, all local, MIT, $0, no accounts, no API keys.

### When a paid service makes sense

Paid scrapers (Bright Data, ZenRows, Firecrawl paid, Spider.cloud) can beat free tools on the hardest anti-bot (DataDome, Akamai, Cloudflare Turnstile) and on massive scale, because they run large residential-proxy networks. Paid search APIs (Exa, Tavily) offer hosted neural search. They cost $16 to $500+/month, require accounts and API keys, and send your queries and content through their servers. Use Hound when you want $0 local web research for an agent with no accounts and no keys; reach for a paid service only for enterprise scale, sites Hound explicitly can't crack, or hosted neural search at scale.

---

## Token cost

<div align="center">
<img src="https://raw.githubusercontent.com/dondai1234/master-fetch/master/docs/tokens.svg" alt="MCP tool count comparison: Hound 6 tools vs Firecrawl 12, Jina 19, Bright Data 60+" width="760">
</div>

Most MCP servers cost 3 to 5K tokens just to exist. Hound's 6 tools cost **~2K tokens**, and the connect-time `instructions` are injected once at handshake, not repeated every turn. Your context window is expensive; Hound respects it.

---

## Honest limits

Hound is honest about what it can't do (no free tool can):

- **DataDome, Akamai, Cloudflare Turnstile (interactive)**: not bypassed. If `smart_fetch` fails on one, the `next_action` tells the agent to switch sources instead of retrying.
- **Search engine rate-limits / CAPTCHAs**: scraping public engines can be rate-limited under heavy use. Hound mitigates with browser-impersonated TLS, multi-engine fallback, the warm stealthy browser, and caching, and reports `engine_blocked`. It is the same gray-area posture as SearXNG/ddgs; no search-engine ToS compliance is claimed.
- **Neural/deep/find_similar search**: need `hound-mcp[all]` (the ONNX reranker runs on the same `onnxruntime` as OCR; the model downloads once on first use). Lean installs get keyword BM25 search.
- **Sites requiring login**: out of scope (Hound does page interaction, not authenticated sessions).
- **Shadow-DOM / hard SPAs**: `actions` (scroll, click, wait_selector) reach most of it; deep shadow-DOM piercing is not yet wired.
- **YouTube**: minimal text.

When a fetch or search fails, the response says exactly why and what to try next, so the agent doesn't waste calls guessing.

---

## For Pi agent users

See the Pi-specific install block above. After `/reload` and `/mcp`, `smart_fetch`, `smart_crawl`, and `smart_search` should be available.

---

<div align="center">

**MIT** · [GitHub](https://github.com/dondai1234/master-fetch) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/dondai1234/master-fetch/issues)

</div>
