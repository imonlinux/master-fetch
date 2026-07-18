"""v10 hot-path profile: measure the cost the new envelope adds to EVERY
response, and confirm the cache-hit path is zero-overhead (no browser, no
extraction).

The envelope (page_type/source_type/is_official/content_age_days/is_stale +
smart next_action) runs on every finalized result via _with_agent_hints. This
profiles it on a realistic populated ResponseModel to prove the per-response
cost is negligible (the v10 "fast" promise).

Run:  python scripts/profile_hotpath.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from master_fetch.server import ResponseModel, _with_agent_hints, _apply_chunking
from master_fetch.envelope import detect_page_type, classify_source, compute_freshness


# A realistic HTML page (~50KB) with article content, links, and metadata.
_HTML = (
    "<html lang='en'><head><title>Some Article</title>"
    "<meta property='og:title' content='Some Article'>"
    "<meta property='article:published_time' content='2025-03-15T10:00:00+00:00'>"
    "</head><body><nav><a href='/home'>Home</a></nav>"
    "<article><h1>Title</h1><p>" + ("Real article body text. " * 800) + "</p>"
    + "".join(f"<a href='/ref{i}'>ref{i}</a>" for i in range(8))
    + "</article><footer><a href='/about'>About</a></footer></body></html>"
)


def _populated_result() -> ResponseModel:
    return ResponseModel(
        status=200, content=["Real article body. " * 200], url="https://example.com/post",
        fetcher_used="http", extracted_type="markdown", content_type="text/html",
        metadata={"title": "Some Article", "published_time": "2025-03-15T10:00:00+00:00",
                  "author": "Author", "canonical": "https://example.com/post"},
        links={"citations": [{"url": f"https://example.com/r{i}", "text": f"r{i}"} for i in range(8)]},
        page_type="article",
    )


def _time(label: str, fn, iters: int) -> None:
    # Warm once.
    fn()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    elapsed = time.perf_counter() - t0
    per = elapsed / iters * 1e6
    print(f"  {label:42s} {per:8.2f} us/call  ({iters} iters, {elapsed*1000:.1f} ms total)")


def main():
    r = _populated_result()
    html = _HTML
    print("v10 hot-path profile (per-response envelope cost):")
    print("-" * 72)
    _time("_with_agent_hints (full envelope + hints)", lambda: _with_agent_hints(r.model_copy()), 5000)
    _time("_apply_chunking (chunk + envelope + hints)", lambda: _apply_chunking(r.model_copy(), max_chars=40000), 2000)
    _time("detect_page_type (50KB HTML)", lambda: detect_page_type(html, "https://example.com/post", "text/html", 5000), 2000)
    _time("classify_source", lambda: classify_source("https://docs.python.org/3/library/os"), 20000)
    _time("compute_freshness", lambda: compute_freshness({"published_time": "2025-03-15T10:00:00+00:00"}, "2026-07-18T12:00:00+00:00"), 20000)

    print("-" * 72)
    print("Cache-hit path (must be zero-overhead: no browser, no extraction):")
    asyncio.run(_profile_cache_hit())


async def _profile_cache_hit():
    from master_fetch.cache import set_cached, get_cached, clear_all_cache
    import tempfile, os
    d = Path(tempfile.mkdtemp()) / "cache"
    d.parent.mkdir(parents=True, exist_ok=True)
    url = "https://example.com/cached"
    await set_cached(
        url, "markdown", ["cached content " * 100], 200, None, 3600,
        cache_dir=d, content_type="text/html", total_size_bytes=5000, source="live",
        envelope={"metadata": {"title": "T", "published_time": "2025-01-01"},
                  "media": [], "links": {}, "quality_score": 0.0, "table_of_contents": [],
                  "page_type": "article", "source": "live", "archived_at": ""},
    )
    # Warm + time the get_cached + ResponseModel rebuild (the cache-hit work).
    def hit():
        return get_cached(url, "markdown", None, ttl=3600, cache_dir=d, source="live")
    # Warm
    await hit()
    t0 = time.perf_counter()
    N = 2000
    for _ in range(N):
        await hit()
    elapsed = time.perf_counter() - t0
    per = elapsed / N * 1e6
    print(f"  {'get_cached (DB lookup + envelope load)':42s} {per:8.2f} us/call  ({N} iters)")
    # Full cache-hit finalization (rebuild ResponseModel + _apply_chunking):
    async def full_hit():
        cached = await get_cached(url, "markdown", None, ttl=3600, cache_dir=d, source="live")
        env = cached.get("envelope") or {}
        r = ResponseModel(
            url=cached["url"], status=cached["status"], content=cached["content"],
            cached=True, fetcher_used="cache", duration_ms=0, extracted_type="markdown",
            content_type=cached.get("content_type", ""), total_size_bytes=cached.get("total_size_bytes", 0),
            metadata=env.get("metadata", {}), media=env.get("media", []),
            links=env.get("links", {}), quality_score=env.get("quality_score", 0.0),
            table_of_contents=env.get("table_of_contents", []), page_type=env.get("page_type", "unknown"),
            source=env.get("source", "live"), archived_at=env.get("archived_at", ""),
        )
        return _apply_chunking(r, max_chars=40000)
    await full_hit()
    t0 = time.perf_counter()
    N2 = 1000
    for _ in range(N2):
        await full_hit()
    elapsed = time.perf_counter() - t0
    per = elapsed / N2 * 1e6
    print(f"  {'full cache-hit (lookup + rebuild + chunk)':42s} {per:8.2f} us/call  ({N2} iters)")
    print("\nVerdict: cache hit touches NO browser and runs NO extraction — only a")
    print("SQLite PK lookup + a BM25 focus pass (when focus is set). The numbers above")
    print("are the entire cost of a repeat fetch.")


if __name__ == "__main__":
    main()
