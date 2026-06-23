# Hound v7 — Local Search Flagship (plan)

Goal: remove the TinyFish dependency. Make hound 100% local + keyless + no-account.
Build a web search that exists for ONE job: feed the fetch tool. Purpose-built,
tight integration, Exa-inspired flagship features, lightweight, no new hard deps.

"Hound v7 plan — local search flagship" is a discussion doc, not a build order.
Dondai approves scope before any code.

## The core insight (what "100% local search" really means)

You cannot index the web on a laptop. So "100% local" means: **keyless, no
account, no third-party API, bundled inside hound, self-contained.** The search
still hits public search engines (DuckDuckGo, Bing, Google, Wikipedia) over HTTP.
That is the same posture as SearXNG and ddgs, and it is the only honest way to do
keyless web search. The differentiator is NOT "we index the web" — it is:

1. **Hound's anti-bot IS the search-engine scraper's transport.** Every keyless
   search lib (ddgs, SearXNG) gets blocked/CAPTCHA'd by Google/Bing under load
   because they fetch engines with plain HTTP. Hound already keeps a warm
   stealthy Patchright browser alive. When an engine blocks the HTTP scraper,
   hound escalates to that browser to render + parse the SERP. No keyless search
   tool does this. This is the reliability moat.
2. **Live content-aware neural rerank (the Exa-killer).** Exa ranks on
   precomputed embeddings of crawled docs. We can't precompute a web index. But
   we can do something Exa's API does not do per query: actually PEEK the top
   candidates' real content (cheap HTTP, first ~8KB) and rerank with a local
   neural cross-encoder on the ACTUAL page text, not the engine's snippet. Live,
   per-query, more accurate for the agent's real intent. Only possible because
   hound owns the fetch layer. This is the flagship.

## Architecture (5 layers, all reusing hound primitives)

```
query
 |
 v
[1] Engine scrapers  (hound-native: DDG html + Bing + Google + Wikipedia API)
     each engine: HTTP tier first  ->  on block, escalate to warm stealthy browser
     (reuses _http_with_retry + _ensure_auto_session + _auto_session_lock)
 |
 v
[2] Merge + dedup + normalize   (reuse normalize_url from crawl.py)
 |
 v
[3] Rerank
     - keyword mode (default, lean install): BM25 over title+snippet
     - neural mode ([all] + model): ONNX cross-encoder ms-marco-MiniLM-L-6-v2
       on (query, snippet) pairs, runs on the onnxruntime we already ship
     - deep mode (flagship): peek top-N candidates' real content (cheap HTTP),
       rerank on (query, page_peek) with the cross-encoder
 |
 v
[4] Fetch-shaping   fetch_relevance tiers + semantic_score + peek + fetch_hint
     (research mode bulk-fetches the RERANKED top-N, not engine-ranked)
 |
 v
[5] Cache   (existing SQLite cache, key = query+filters+mode, TTL)
```

## The flagship moves (Exa-inspired, all local)

1. **Anti-bot engine scraping** — stealthy browser escalation for blocked SERPs.
   Novel for keyless search. Reliability moat.
2. **Live content-aware neural rerank (`mode=deep`)** — peek + rerank on real
   page content. Better-than-Exa accuracy for the agent's specific query, live.
   The killer feature.
3. **Neural rerank (`mode=neural`)** — Exa's core semantic ranking, locally, via
   ONNX cross-encoder on the already-shipped onnxruntime.
4. **`find_similar` (`mode=find_similar`, pass a URL)** — fetch the source URL
   with hound, extract key terms, build a query, search, rerank candidates
   against the source page's content. Exa's find-similar without their index.
5. **Autoretrieval / query expansion (`expand=N`)** — generate N sub-query
   variants locally (template + keyphrase expansion, NO external LLM call), run
   all in parallel across engines, merge, dedup, rerank. Exa's autoretrieval,
   local + cheap. Boosts recall for niche queries (the "Exa for niche" angle).
6. **Niche search** — multi-query expansion + multi-engine + neural rerank
   surfaces results engines bury on page 2-3.
7. **Fetch-ready output** — every result carries `semantic_score` (or
   `relevance_score` in keyword mode), `fetch_relevance` tiers, `peek` (deep
   mode), `fetch_hint`. Built so the agent calls `smart_fetch` on the top 1-2.
   One job.

## Dependencies (the lightweight promise)

- **Hard deps added: ZERO.** Engine scrapers use existing `requests` + the warm
  `scrapling` stealthy browser. No ddgs dependency, no SearXNG server.
- **Optional `[all]` adds: `tokenizers` (Apache-2.0, ~3-5MB, prebuilt wheels,
  no compiler).** onnxruntime is ALREADY in `[all]` for OCR — reused for the
  reranker. No new runtime.
- **Reranker model: downloaded ONCE on first neural/deep search** into
  `~/.master_fetch_cache/models/msmarco-minilm-l6-v2/` (~80MB, Apache-2.0,
  pinned to a HF revision + hash-checked). NOT bundled in the wheel — keeps the
  lean install small.
- **Two-tier, mirrors the OCR pattern exactly:**
  - Lean install (`hound-mcp`): fully functional keyless local search, keyword
    BM25 rerank, multi-engine, anti-bot, find_similar, autoretrieval, filters.
    No model, no onnxruntime, no tokenizers.
  - `[all]`: + neural rerank + deep content-aware rerank (the Exa-tier stuff).
    Graceful fallback to keyword mode if the model download fails / offline.

## Tool surface (stays ONE tool: `smart_search`)

New params (add ~300 tokens, still lean):
- `mode`: `auto` (default: neural if [all] else keyword) | `neural` | `deep` |
  `find_similar` (requires `url` instead of `query`)
- `engines`: list, default `["duckduckgo","bing","google"]` or specific
- `expand`: int (autoretrieval sub-query count, default 1 = off)
- `freshness`: `day|week|month|year` (engine timelimit where supported)
- keep: `query`, `site`, `exclude_sites`, `location`, `language`, `page`,
  `max_results`, `fetch_content` (research mode)

New result fields: `semantic_score` (float, neural/deep) or `relevance_score`
(float, keyword), `peek` (str, deep mode only), `engines_used` (list),
`rerank_mode` (keyword|neural|deep), `engine_blocked` (list, honest signals).
`fetch_relevance` tiers derived from the score. `fetch_hint` updated.

TinyFish: HARD-REMOVED. No TinyFish code, no key, no env, no optional backend.
The entire server is local + keyless after v7. The TinyFish key in keys.md stays
for personal/external use but is no longer referenced by hound.

## Failure paths (planned first)

1. **Engine HTML changes break a parser.** Per-engine parse returns [] on
   failure (never crashes). 3 engines with fallback. `engines_used` shows which
   succeeded. Fixture-HTML tests + live smoke tests (gated, not in CI).
2. **Rate-limit / CAPTCHA on an engine.** Stealthy browser escalation (move #1).
   UA rotation, backoff, multi-engine fallback, result cache (TTL) to reduce
   hits. Honest `engine_blocked` + `next_action` (retry / rephrase).
3. **Reranker model download fails / offline.** Graceful BM25 fallback +
   `next_action` "retry when online for neural rerank". Model cached after first
   success.
4. **[all] not installed (lean).** Keyword BM25 mode, fully functional.
   `next_action` "install hound-mcp[all] for neural rerank" (mirrors OCR).
5. **Latency (multi-engine + deep peek + rerank).** Parallel engine requests
   (asyncio.gather), deep mode opt-in (default off), rerank cap at ~20-50
   candidates, overall `deadline_ms` (reuse crawl's pattern), cache.
6. **ToS / legal (scraping engines).** Same gray-area posture as SearXNG/ddgs:
   honest "metasearch, engines may rate-limit", no compliance claim. State the
   limit in README + tool description.
7. **Model license / availability drift.** Pin HF revision (commit hash) +
   hash-check. Apache-2.0 confirmed. Ettin-17M kept as a documented future swap.
8. **ONNX session / tokenizer thread safety.** One warm InferenceSession +
   Tokenizer created at first neural search, reused (thread-safe for run).
9. **Result quality regression vs TinyFish.** Live A/B on a query set before
   cutover. Optional TinyFish backend as escape hatch.

## Build phasing (each phase tested + live-verified, no push until all done)

- **Phase 1 — Engine scrapers (hound-native).** DDG html endpoint, Bing, Google,
  Wikipedia API. HTTP tier + stealthy escalation. Merge/dedup/normalize. Real
  queries live-verified. Keyword BM25 rerank. This alone = a working keyless
  local search (replaces TinyFish at the baseline level).
- **Phase 2 — Neural rerank.** Model download + cache, ONNX session, tokenizer,
  (query, snippet) scoring. `mode=neural`. Live-verified vs keyword on a query
  set (neural should win on semantic/ambiguous queries).
- **Phase 3 — Deep content-aware rerank (flagship).** Cheap HTTP peek of top-N
  candidates, rerank on page text. `mode=deep`. Live-verified: does it pick the
  better URL than snippet-rerank? This is the headline feature.
- **Phase 4 — Exa-inspired extras.** `find_similar`, `expand` (autoretrieval),
  niche via multi-query. Live-verified.
- **Phase 5 — Integration + polish.** Research mode bulk-fetches reranked top-N.
  Cache keys, `engines_used`/`engine_blocked`/`semantic_score` fields,
  `fetch_hint` rewrite, tool-def tokens, HOUND_INSTRUCTIONS update (search is now
  local + keyless), README rewrite, TinyFish → optional backend, regression
  tests, clean-venv verify (the OCR/pdfplumber lesson), bump 6.0.0 -> 7.0.0,
  publish.

## Decisions (locked by Dondai, 2026-06-23)

1. **Hand-roll engines.** Hound-native engine scrapers, zero ddgs dependency,
   built from the ground up for hound. ddgs/SearXNG used only as endpoint
   reference. (Dondai: our biggest wins come from owning our own stuff.)
2. **TinyFish: HARD-REMOVED.** No key, no env, no optional backend. 100% local.
3. **Reranker: MiniLM-L-6-v2 (safe).** Ship now. Ettin-17M swap deferred until
   its ONNX is confirmed + tested.
4. **Deep mode: off by default, auto-on when `fetch_content=true`** (research
   mode already pays the fetches).
5. **Model download: lazy on first neural/deep search**, one-time status line,
   graceful BM25 fallback on failure/offline.
6. **Text-only for v7.** News/images/videos/books deferred. Search = one job.

## Why this wins (the honest pitch)

- Only free search that is keyless, no-account, fully bundled, AND uses an
  anti-detect browser to scrape engines that block other keyless tools.
- Only search that reranks candidates on their REAL fetched content with a local
  neural reranker (Exa-style, live, free). Exa charges for this; we do it on the
  onnxruntime we already ship.
- One tool, one job, ~2K tokens, MIT, zero new hard deps. Lean install gets a
  working keyless local search; [all] gets the neural flagship.
- Closes the last non-local piece of hound. After v7, the entire server is $0,
  no accounts, no third-party APIs, nothing routing through someone else's cloud.
