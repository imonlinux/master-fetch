# hound-mcp-pi

Hound web research for the Pi agent. Six native tools, all routed through a single long-lived Hound MCP subprocess:

- **web_fetch** - anti-bot fetch + clean extraction + Internet Archive dead-link recovery
- **web_search** - keyless web search across 10 backends, neural-reranked
- **web_crawl** - deep site crawl, sitemap one-fetch map
- **web_screenshot** - anti-bot screenshot for multimodal agents
- **cache_clear** - clear fetch cache
- **hound_version** - version + update status

## Install

```bash
# 1. Install Hound (the MCP server / engine)
pip install hound-mcp[all]

# 2. Install this Pi extension (npm, auto-updates)
pi install npm:@houndmcp/hound-mcp-pi
```

Alternatively, install from git (pins to a specific tag):

```bash
pi install git:github.com/dondai1234/master-fetch@v10.3.0
```

That's it. The extension auto-discovers `hound` on your PATH, spawns it as a singleton subprocess, and prewarms it at session start. No API key, no account, no config file.

## Updating

```bash
# Update Hound (the MCP server)
hound -u

# Update the Pi extension (if installed via npm, unpinned)
pi update npm:@houndmcp/hound-mcp-pi
```

The extension checks at session start whether the installed Hound version matches the extension version. If they diverge by a major version, it warns you to update both.

## How it works

The extension spawns `hound` (the MCP server) as a stdio subprocess and speaks MCP JSON-RPC to it. The subprocess is a singleton — it stays alive for the whole Pi session, so Hound's startup prewarm (stealthy browser, search engines, neural reranker) runs once and persists. Zero re-launch cost per call.

## Requirements

- [Pi coding agent](https://github.com/earendil-works/pi-coding-agent)
- Python 3.11+ with `hound-mcp[all]` installed (`pip install hound-mcp[all]`)

## License

MIT. Same as Hound itself.
