#!/usr/bin/env python3
"""Sync all tool descriptions from server.py to pi-extension hound.ts."""
import re

SERVER = "src/master_fetch/server.py"
HOUND = "pi-extension/extensions/hound.ts"

with open(SERVER, "r", encoding="utf-8") as f:
    server_src = f.read()

with open(HOUND, "r", encoding="utf-8") as f:
    hound_src = f.read()

# Extract descriptions from server.py
fetch_match = re.search(r'"description": "(Fetch any URL.*?)"', server_src, re.DOTALL)
search_match = re.search(r'"description": "(Keyless web search.*?)"', server_src, re.DOTALL)
crawl_match = re.search(r'"description": "(Deep-crawl a site.*?)"', server_src, re.DOTALL)

if not fetch_match:
    print("ERROR: Could not find fetch description in server.py")
    exit(1)
if not search_match:
    print("ERROR: Could not find search description in server.py")
    exit(1)
if not crawl_match:
    print("ERROR: Could not find crawl description in server.py")
    exit(1)

# Adapt for hound.ts: replace smart_fetch -> web_fetch, smart_crawl -> web_crawl
fetch_desc = fetch_match.group(1).replace("smart_fetch", "web_fetch").replace("smart_crawl", "web_crawl")
search_desc = search_match.group(1).replace("smart_fetch", "web_fetch").replace("smart_crawl", "web_crawl")
crawl_desc = crawl_match.group(1).replace("smart_fetch", "web_fetch").replace("smart_crawl", "web_crawl")

# Replace in hound.ts
replacements = [
    (r'description: "(Fetch any URL.*?)"', fetch_desc, "fetch"),
    (r'description: "(Keyless web search.*?)"', search_desc, "search"),
    (r'description: "(Deep-crawl a site.*?)"', crawl_desc, "crawl"),
]

for pattern, replacement, name in replacements:
    match = re.search(pattern, hound_src, re.DOTALL)
    if match:
        hound_src = hound_src.replace(match.group(1), replacement)
        print(f"Updated {name} description ({len(replacement)} chars)")
    else:
        print(f"WARNING: Could not find {name} description in hound.ts")

# Update web_search options to include include_content and content_chars
old_opts = "max_results (1-50,6), cache_ttl (300), mode (auto|neural|find_similar), engines (list), site, exclude_sites, location, language, region, page, freshness, url (for find_similar)"
new_opts = "max_results (1-50,6), cache_ttl (300), mode (auto|neural|find_similar), engines (list), site, exclude_sites, location, language, region, page, freshness, url (for find_similar), include_content (bool,false: fetch BM25-filtered page content for top results in one call, saves round trips for multi-fact questions), content_chars (int,2000: max chars per result when include_content=true)"

if old_opts in hound_src:
    hound_src = hound_src.replace(old_opts, new_opts)
    print("Updated search options (added include_content, content_chars)")
else:
    # Try without exact match - search for the line
    match = re.search(r'description: "max_results.*?find_similar\)"', hound_src)
    if match:
        hound_src = hound_src.replace(match.group(0), f'description: "{new_opts}"')
        print("Updated search options (regex fallback)")
    else:
        print("NOTE: search options text not found in hound.ts")

# Update web_search promptSnippet to mention include_content
old_snippet = "web_search(query) - keyless search across 10 backends. Don't search when you have a URL - web_fetch with focus= instead. After search, web_fetch high-relevance results with focus='your question'."
new_snippet = "web_search(query, options={include_content:true}) - keyless search across 10 backends. Set include_content=true for multi-fact/research questions to get page content in one call. Don't search when you have a URL - web_fetch with focus= instead."

if old_snippet in hound_src:
    hound_src = hound_src.replace(old_snippet, new_snippet)
    print("Updated web_search promptSnippet")
else:
    print("NOTE: promptSnippet not found in hound.ts (may already be updated)")

with open(HOUND, "w", encoding="utf-8") as f:
    f.write(hound_src)

print("Done. hound.ts synced.")
