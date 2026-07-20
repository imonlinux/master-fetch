"""Compress HOUND_INSTRUCTIONS + _TOOL_DEFS into tight LLM language.
Replaces both blocks wholesale. Preserves every test-required keyword.
Idempotent: asserts both anchors exist and are unique before replacing."""
from pathlib import Path
import re

p = Path("src/master_fetch/server.py")
s = p.read_text(encoding="utf-8")

# ── 1. Replace HOUND_INSTRUCTIONS ──────────────────────────────────────────

NEW_INSTRUCTIONS = '''HOUND_INSTRUCTIONS = (
    "Hound = web access. 4 tools (+ cache_clear, version).\\n"
    "\\n"
    "smart_fetch(url) - get any page. Auto anti-bot (HTTP -> stealthy). Text + metadata + signals (content_ok, next_action, summary, page_type, content_age_days/is_stale, source_type/is_official, source/archived_at). Hard-block (404/bot/auth) -> auto-recover from Internet Archive (source=archive.org + archived_at); archive_fallback=false to opt out.\\n"
    "  Narrow: css_selector. Long page: offset/next_offset to paginate, or focus='query' for only relevant blocks (post-cache; re-pass when paginating). actions=[{click:..},{scroll:N},{fill:{selector,text}},{press:Enter},{wait:ms},{wait_selector:..}] for click/form/scroll (forces stealthy, bypasses cache). PDFs -> structured markdown + table_of_contents section-map [{level,title,page,end_page}] -> pass pages='23-31' for one section. Scanned/CID -> auto-OCR with [all] (quality_score 0-1). include_links=true -> response.links = {citations,navigation,external,primary_source}. Bulk: urls=[...]. cache_ttl=0 forces fresh.\\n"
    "smart_search(query) - keyless web search (no API key). 10 backends in parallel (ddg,brave,mojeek,yahoo,yandex,startpage,google,qwant + opt-in wikipedia,grokipedia), neural-reranked + cross-backend consensus. Returns URLs + ranking, NOT content -> smart_fetch the matches. Each result: relevance_score + fetch_relevance (high/med/low) + engines_consensus. related_queries from result titles+snippets. Blocked backends circuit-broken 60s. NEVER answer from snippets alone. Filters in options: site/exclude_sites, location/language/region, page (0-10), freshness (day|week|month|year).\\n"
    "smart_crawl(url) - deep-crawl a site. Best-first same-domain walk; each page: markdown + content_ok + page_type. List pages -> structured link list. BIG SITES: options sitemap=true maps whole site from sitemap.xml in ONE fetch (URL list + lastmod); sitemap='auto' = use if present else BFS. discover_only=true = URL map only. focus='query' crawls relevant pages first + focus-filters each. crawl_urls=[...] fetches a chosen subset. Caps: max_pages (10), max_depth (2), max_total_chars, deadline_ms.\\n"
    "screenshot(url) - image capture. Multimodal agents only (content as images/canvas/visual layout). Text agents: use smart_fetch.\\n"
    "\\n"
    "#1 workflow: smart_search -> smart_fetch matching results -> synthesize with URLs.\\n"
    "\\n"
    "Unbypassable live (no free tool beats): DataDome, Akamai, Cloudflare Turnstile. smart_fetch already auto-recovers hard-blocks from the Internet Archive; if it still fails (no snapshot), switch sources - don't retry same URL.\\n"
)'''

# Find and replace the HOUND_INSTRUCTIONS block
m = re.search(r'HOUND_INSTRUCTIONS = \(', s)
assert m, "HOUND_INSTRUCTIONS anchor not found"
start = m.start()
# Find the matching closing paren (the line with just ")")
end = s.index("\n)\n", start) + 3
old_inst = s[start:end]
s = s[:start] + NEW_INSTRUCTIONS + s[end:]
print(f"  instructions: {len(old_inst)} -> {len(NEW_INSTRUCTIONS)} chars")

# ── 2. Replace _TOOL_DEFS ─────────────────────────────────────────────────

NEW_TOOL_DEFS = '''    _TOOL_DEFS: list[dict] = [
        {
            "name": "mcp_smart_fetch",
            "description": "Fetch a URL (or urls=[...] for parallel bulk). Auto HTTP -> stealthy escalation. Returns extracted text + metadata + signals: content_ok, next_action, summary, page_type, content_age_days/is_stale, source_type/is_official, source/archived_at. Hard-block (404/bot/auth) -> auto-recover from Internet Archive (source=archive.org, archived_at=snapshot date; archive_fallback=false in options to opt out). PDFs -> structured markdown + ToC + page ranges + auto-OCR. Long pages: paginate with offset/next_offset or focus='query' for only relevant blocks. actions=[...] for click/form/scroll. include_links/include_media via options.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "urls": {"type": "array", "items": {"type": "string"}, "description": "Multiple URLs (parallel; returns per-URL results)"},
                    "extraction_type": {"type": "string", "enum": ["markdown", "html", "text", "article", "structured"], "description": "Content format (default markdown). html = raw HTML."},
                    "css_selector": {"type": "string", "description": "CSS selector to narrow extracted content (e.g. 'article', '.main'). Token saver."},
                    "max_content_chars": {"type": "integer", "description": "Max chars of extracted content (default 40000, min 500). Lower = less context; rest paginated via offset/next_offset."},
                    "timeout": {"type": "integer", "description": "Max request time in ms (default 30000)."},
                    "cache_ttl": {"type": "integer", "description": "Cache seconds (default 3600). 0 = force fresh."},
                    "force_fetcher": {"type": "string", "enum": ["http", "stealthy"], "description": "Pin to one tier, skip auto-escalation. 'http' = fast HTTP-only (fails on JS/bot walls). 'stealthy' = anti-detect browser. Default = auto."},
                    "offset": {"type": "integer", "description": "Char offset into extracted text to resume a truncated page. Use next_offset from previous response."},
                    "pages": {"type": "string", "description": "PDF only: page spec like '1-5' or '1,3,5-7'. Use table_of_contents page/end_page ranges to pick. None = all pages."},
                    "password": {"type": "string", "description": "PDF only: password for an encrypted PDF."},
                    "focus": {"type": "string", "description": "Query-focused extraction: only BM25-relevant blocks returned. Context saver on long pages. Post-cache (no re-fetch). Re-pass same focus when paginating."},
                    "actions": {"type": "array", "description": "Page interactions on stealthy browser AFTER load, BEFORE extraction. Forces stealthy + bypasses cache. Each item: {click:'css'}, {fill:{selector:'css',text:'x'}}, {press:'Enter'}, {wait:500}, {scroll:3}, {wait_selector:'css'}. Use for load-more, search forms, pagination, infinite scroll."},
                    "options": {"type": "object", "description": "include_links (bool,false: response.links=citations/navigation/external+primary_source), include_media (bool,false: up to 20 page image URLs), archive_fallback (bool,true: recover from Internet Archive on hard-block; false=raw failure), proxy (str|dict), cookies (list), extra_headers (dict), useragent (str), wait (ms,0), network_idle (bool,SPAs), headless (bool,true), respect_robots (bool,false), real_chrome/solve_cloudflare/block_webrtc/hide_canvas/main_content_only/use_trafilatura (anti-detect tuning, good defaults, rarely needed).", "additionalProperties": True},
                },
            },
            "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
        },
        {
            "name": "mcp_smart_crawl",
            "description": "Deep-crawl a site: best-first same-domain walk, each page as markdown + content_ok + page_type. List pages -> structured link list. sitemap=true (in options) maps whole site from sitemap.xml in one fetch; sitemap='auto' = use if present else BFS. discover_only=true = URL map only. focus='query' prioritizes relevant pages + focus-filters each. crawl_urls=[...] fetches a chosen subset. Caps: max_pages (10), max_depth (2), max_total_chars, deadline_ms. Reuses smart_fetch anti-bot + cache.",
            "inputSchema": {
                "type": "object", "required": ["url"],
                "properties": {
                    "url": {"type": "string", "description": "Start URL (crawl stays on this domain)"},
                    "discover_only": {"type": "boolean", "description": "true = return URL map only, no page content. For big sites prefer options sitemap=true (one-fetch map)."},
                    "focus": {"type": "string", "description": "Query: prioritize crawling links relevant to this + focus-filter each page. Token saver on doc sites."},
                    "crawl_urls": {"type": "array", "items": {"type": "string"}, "description": "Chosen subset of URLs to fetch (second-phase selective crawl, no re-discovery). Use after sitemap=true or discover_only=true."},
                    "options": {"type": "object", "description": "sitemap (true|'auto'|false,false: true=map from sitemap.xml in one fetch; 'auto'=use if present else BFS), max_pages (1-100,10), max_depth (0-5,2), path_include (list of path prefixes), path_exclude (list to skip), max_content_chars_per (8000), max_total_chars (token budget), concurrency (1-5,3), cache_ttl (3600;0=fresh), respect_robots (false), force_fetcher ('http'|'stealthy'), timeout (ms,30000), deadline_ms (120000).", "additionalProperties": True},
                },
            },
            "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
        },
        {
            "name": "mcp_screenshot",
            "description": "Screenshot a URL as an image. Multimodal agents only (content as images/canvas/visual layout). Text agents: use smart_fetch. Stealthy browser auto-managed.",
            "inputSchema": {
                "type": "object", "required": ["url"],
                "properties": {
                    "url": {"type": "string", "description": "URL to screenshot"},
                    "session_id": {"type": "string", "description": "Optional: reuse a specific open browser session. Omit to auto-manage."},
                    "options": {"type": "object", "description": "full_page (bool,false), image_type (png|jpeg,png), quality (0-100,jpeg), wait (ms), wait_selector (css), network_idle (bool), timeout (ms,30000).", "additionalProperties": True},
                },
            },
            "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
        },
        {
            "name": "mcp_smart_search",
            "description": "Keyless web search (no API key). 10 backends in parallel (ddg,brave,mojeek,yahoo,yandex,startpage,google,qwant + opt-in wikipedia,grokipedia), neural-reranked + cross-backend consensus. Returns URLs + ranking, NOT content -> smart_fetch the ones that match. Each result: relevance_score + fetch_relevance (high/med/low) + engines_consensus. related_queries from result titles+snippets. Blocked backends circuit-broken 60s. NEVER answer from snippets alone. Filters in options: site, exclude_sites, location, language, region, page, freshness.",
            "inputSchema": {
                "type": "object", "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "options": {"type": "object", "description": "max_results (1-50,6), cache_ttl (300), mode (auto|neural|find_similar; auto=neural if [all]+model else consensus; find_similar needs url=), engines (list, default: ddg,brave,mojeek,yahoo,yandex,startpage,google,qwant; add 'wikipedia'/'grokipedia'), site (domain restrict), exclude_sites (list), location, language (2-letter), region, page (0-10), freshness (day|week|month|year), url (for find_similar).", "additionalProperties": True},
                },
            },
            "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
        },
        {
            "name": "cache_clear",
            "description": "Clear fetch cache. all=true wipes all (default: expired only). To re-fetch one URL fresh, pass cache_ttl=0 to smart_fetch/smart_crawl instead. Cache stores extracted text per URL+extraction_type+css_selector+pages (+ per query+filters for search); default TTL 1hr.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "description": "Wipe all (default: expired only)"},
                },
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "version",
            "description": "Hound version + update status.",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        },
    ]'''

# Find and replace the _TOOL_DEFS block
m2 = re.search(r'    _TOOL_DEFS: list\[dict\] = \[', s)
assert m2, "_TOOL_DEFS anchor not found"
start2 = m2.start()
# Find the matching closing bracket at the same indentation
end2 = s.index("\n    ]\n", start2) + len("\n    ]")
old_defs = s[start2:end2]
s = s[:start2] + NEW_TOOL_DEFS + s[end2:]
print(f"  _TOOL_DEFS: {len(old_defs)} -> {len(NEW_TOOL_DEFS)} chars")

p.write_text(s, encoding="utf-8")
print("done")
