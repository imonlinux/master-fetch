"""Hound local web search (v7 flagship: keyless, no-account, fully local).

Scrapes public search engines (DuckDuckGo, Bing, Google, Wikipedia) via the
hound-native engine layer in search_engines.py — no third-party API, no key, no
account. Results are merged across engines, deduped by normalized URL, and
ranked by BM25 over (title + snippet). Every result carries a relevance_score
and a fetch_relevance tier so the agent fetches the right 1-2 URLs via
smart_fetch. Research mode (fetch_content=True) bulk-fetches the reranked top-N.

Phase 1 (this file): keyword BM25 rerank over engine snippets. Neural rerank
(mode=neural) and content-aware deep rerank (mode=deep) arrive in later phases
on the same onnxruntime we already ship for OCR.
"""

from __future__ import annotations

import asyncio
import json
import logging
from time import time
from typing import Optional, Union

from pydantic import BaseModel, Field

from master_fetch.cache import get_cached, set_cached
from master_fetch.security import validate_search_query, redact_api_key, SecurityError
from master_fetch.search_engines import (
    RawResult, multi_search, EngineReport, DEFAULT_ENGINES, bm25_rerank,
)
from master_fetch.reranker import rerank as neural_rerank, unavailable_reason

logger = logging.getLogger("master-fetch.search")

SEARCH_CACHE_TTL = 300  # 5 minutes


# ─── response models ─────────────────────────────────────────────────────────

class SearchResult(BaseModel):
    title: str = Field(description="Result title")
    url: str = Field(description="Result URL")
    snippet: str = Field(default="", description="Result snippet from the engine")
    source: str = Field(default="", description="Engine that returned this result (duckduckgo/bing/google/wikipedia)")
    position: int = Field(default=0, description="1-indexed rank after merge + rerank")
    relevance_score: float = Field(default=0.0, description="0.0-1.0 relevance to the query (BM25 over title+snippet). 1.0 = most relevant in this set.")
    fetch_relevance: str = Field(default="", description="high|med|low - fetch 'high' first (1-2), then 'med' if needed, skip 'low'.")


class SearchResponseModel(BaseModel):
    query: str = Field(description="Search query")
    results: list[SearchResult] = Field(description="Ranked search results")
    total_results: int = Field(default=0, description="Results returned")
    engines_used: list[str] = Field(default=[], description="Engines that returned results")
    engine_blocked: list[str] = Field(default=[], description="Engines that were rate-limited/CAPTCHA'd (results still came from the others)")
    rerank_mode: str = Field(default="keyword", description="Rerank used: keyword (BM25). neural/deep arrive in later phases.")
    cached: bool = Field(default=False, description="Served from cache?")
    duration_ms: float = Field(default=0, description="Duration ms")
    error: str = Field(default="", description="Error message (empty = ok)")
    fetch_hint: str = Field(default="", description="How many high/med/low results + which to smart_fetch first")


class ResearchResult(SearchResult):
    """A search result with its full fetched content attached (research mode)."""
    content: list[str] = Field(default=[], description="Fetched page content (truncated per max_content_chars_per)")
    content_ok: bool = Field(default=False, description="True = fetched content is real. False = fetch failed/blocked/empty.")
    fetched_summary: str = Field(default="", description="One-line status of the fetch for this result")
    is_truncated: bool = Field(default=False, description="True = more content; smart_fetch with offset=next_offset.")
    next_offset: int = Field(default=0, description="Next offset if is_truncated; 0 = no more.")
    fetch_error: str = Field(default="", description="Error from the fetch, if content_ok is False.")


class ResearchResponseModel(BaseModel):
    """Response from research mode: search + auto-fetched top results in one call."""
    query: str = Field(description="Search query")
    results: list[ResearchResult] = Field(description="Top results with full content attached")
    total_results: int = Field(default=0, description="Total search results found (only the top fetch_top were fetched)")
    fetched_count: int = Field(default=0, description="How many results were fetched")
    engines_used: list[str] = Field(default=[], description="Engines that returned results")
    engine_blocked: list[str] = Field(default=[], description="Engines rate-limited/CAPTCHA'd")
    rerank_mode: str = Field(default="keyword", description="Rerank used")
    cached: bool = Field(default=False, description="Search served from cache?")
    duration_ms: float = Field(default=0, description="Duration ms")
    error: str = Field(default="", description="Error message")
    fetch_hint: str = Field(default="", description="High/med/low breakdown of the full search")
    summary: str = Field(default="", description="One-line status of the research call")


# ─── tier derivation + hint ──────────────────────────────────────────────────

def _tier(score: float, rank: int, total: int) -> str:
    """Derive high|med|low from BM25 score + rank. Top result is never 'low'."""
    if score >= 0.5 or rank == 1:
        return "high"
    if score >= 0.15:
        return "med"
    if rank <= max(2, total // 3):
        return "med"
    return "low"


def compute_fetch_hint(results: list[SearchResult]) -> str:
    if not results:
        return ""
    high = sum(1 for r in results if r.fetch_relevance == "high")
    med = sum(1 for r in results if r.fetch_relevance == "med")
    low = sum(1 for r in results if r.fetch_relevance == "low")
    return (f"{high} high, {med} med, {low} low - smart_fetch the 'high' results "
            f"first (then 'med' if needed). Skip 'low' unless nothing else helps.")


# ─── filter validation (kept from v5; site/exclude/location/language/page) ────

def _validate_filters(site, exclude_sites, location, language, page):
    import re
    _domain_re = re.compile(r"^(?!-)[A-Za-z0-9.-]{1,253}(?<!-)$")
    if site is not None:
        if not isinstance(site, str) or not _domain_re.match(site) or "." not in site:
            raise SecurityError(f"Invalid site filter: {site!r} (must be a domain like 'docs.python.org')")
    if exclude_sites is not None:
        if not isinstance(exclude_sites, list) or len(exclude_sites) > 20:
            raise SecurityError("exclude_sites must be a list of <= 20 domains")
        for d in exclude_sites:
            if not isinstance(d, str) or not _domain_re.match(d) or "." not in d:
                raise SecurityError(f"Invalid exclude_sites entry: {d!r}")
    if location is not None:
        if not isinstance(location, str) or not re.match(r"^[A-Za-z]{2}(-[A-Za-z]{2})?$", location):
            raise SecurityError(f"Invalid location: {location!r} (e.g. 'US' or 'us-en')")
    if language is not None:
        if not isinstance(language, str) or not re.match(r"^[a-z]{2}$", language):
            raise SecurityError(f"Invalid language: {language!r} (2-letter code, e.g. 'en')")
    if page is not None:
        if isinstance(page, bool) or not isinstance(page, int) or page < 0 or page > 10:
            raise SecurityError(f"Invalid page: {page!r} (0-10)")


def _validate_engines(engines):
    if engines is None:
        return None
    if not isinstance(engines, list) or not engines:
        raise SecurityError("engines must be a non-empty list")
    if len(engines) > 6:
        raise SecurityError("engines list too long (max 6)")
    valid = set(DEFAULT_ENGINES) | {"google"}
    for e in engines:
        if not isinstance(e, str) or e.lower() not in valid:
            raise SecurityError(f"Invalid engine: {e!r} (one of {sorted(valid)})")
        # normalize to lowercase
    return [e.lower() for e in engines]


def _validate_freshness(freshness):
    if freshness is None:
        return None
    if freshness not in ("day", "week", "month", "year"):
        raise SecurityError(f"Invalid freshness: {freshness!r} (day|week|month|year)")
    return freshness


# Implemented rerank modes. deep (Phase 3) and find_similar (Phase 4) are added
# in later phases; until then they are rejected here so the schema does not
# advertise a mode that is not wired.
_IMPLEMENTED_MODES = ("auto", "keyword", "neural")


def _validate_mode(mode):
    if mode is None:
        return "auto"
    if not isinstance(mode, str) or mode.lower() not in _IMPLEMENTED_MODES:
        raise SecurityError(f"Invalid mode: {mode!r} (auto|keyword|neural)")
    return mode.lower()


def _rank(query: str, ranked: list[RawResult], mode: str):
    """Apply the chosen rerank. Returns (ranked_list, scores, mode_used, note).

    mode='auto' uses neural if the reranker is available (hound-mcp[all] + model
    downloaded), else keyword BM25. mode='neural' tries neural and falls back to
    keyword with a note if unavailable. mode='keyword' is always BM25.
    """
    note = ""
    if mode in ("neural", "auto"):
        pairs = neural_rerank(query, ranked)
        if pairs is not None:
            return [r for r, _ in pairs], [s for _, s in pairs], "neural", note
        if mode == "neural":
            note = ("neural rerank unavailable - used keyword. " +
                    (unavailable_reason() or "install hound-mcp[all] and retry"))
    scored = bm25_rerank(query, ranked)
    return [r for r, _ in scored], [s for _, s in scored], "keyword", note


def _build_results(query: str, ranked: list[RawResult], scores: Optional[list[float]] = None
                   ) -> list[SearchResult]:
    """Convert RawResults (already ranked) into SearchResults with tiers."""
    total = len(ranked)
    out: list[SearchResult] = []
    for i, r in enumerate(ranked):
        score = scores[i] if scores and i < len(scores) else 0.0
        out.append(SearchResult(
            title=r.title, url=r.url, snippet=r.snippet, source=r.source,
            position=i + 1, relevance_score=round(score, 4),
            fetch_relevance=_tier(score, i + 1, total),
        ))
    return out


# ─── main entry ───────────────────────────────────────────────────────────────

async def smart_search(
    server,
    query: str,
    max_results: int = 10,
    cache_ttl: int = SEARCH_CACHE_TTL,
    mode: str = "auto",
    engines: Optional[list[str]] = None,
    site: Optional[str] = None,
    exclude_sites: Optional[list[str]] = None,
    location: Optional[str] = None,
    language: Optional[str] = None,
    region: Optional[str] = None,
    page: int = 0,
    freshness: Optional[str] = None,
    fetch_content: bool = False,
    fetch_top: int = 3,
    max_content_chars_per: int = 8000,
) -> Union[SearchResponseModel, ResearchResponseModel]:
    """Search the web with hound's local keyless engine layer and return ranked results.

    No API key, no account. Engines (default duckduckgo+bing+wikipedia) are
    scraped in parallel, merged, deduped, and BM25-ranked. Each result carries
    relevance_score + fetch_relevance so the agent smart_fetches the right 1-2.
    Research mode (fetch_content=True) bulk-fetches the reranked top-N.
    """
    t0 = time()

    try:
        query = validate_search_query(query)
        _validate_filters(site, exclude_sites, location, language, page)
        engines = _validate_engines(engines)
        freshness = _validate_freshness(freshness)
        mode = _validate_mode(mode)
    except Exception as e:
        return SearchResponseModel(
            query=query, results=[], total_results=0,
            duration_ms=0, error=str(e),
        )

    max_results = max(1, min(max_results, 50))
    fetch_top = max(1, min(int(fetch_top) if not isinstance(fetch_top, bool) else 3, 5))
    max_content_chars_per = max(1000, min(int(max_content_chars_per), 50000))

    # region derives from location/language if not given (e.g. "US" -> "us-en").
    if region is None:
        loc = (location or "US").lower()
        lang = (language or "en").lower()
        region = f"{loc}-{lang}" if len(loc) == 2 else "us-en"

    # Cache key includes max_results + every filter + engines + freshness.
    cache_type = (f"search:v2:{max_results}:{site or ''}:{','.join(exclude_sites or [])}:"
                  f"{location or ''}:{language or ''}:{page or 0}:{','.join(engines or [])}:{freshness or ''}:{mode}")
    if cache_ttl > 0:
        cached = await get_cached(query, cache_type, None, ttl=cache_ttl)
        if cached and cached.get("content"):
            try:
                data = json.loads(cached["content"][0])
                results_list = [SearchResult(**r) for r in data.get("results", [])]
                if fetch_content:
                    return await _research_fetch(
                        server, query, results_list, fetch_top, max_content_chars_per,
                        cache_ttl, cached=True, t0=t0, error="",
                        engines_used=data.get("engines_used", []),
                        engine_blocked=data.get("engine_blocked", []),
                        rerank_mode=data.get("rerank_mode", "keyword"),
                        fetch_hint=compute_fetch_hint(results_list),
                    )
                return SearchResponseModel(
                    query=query, results=results_list,
                    total_results=len(results_list), cached=True,
                    engines_used=data.get("engines_used", []),
                    engine_blocked=data.get("engine_blocked", []),
                    rerank_mode=data.get("rerank_mode", "keyword"),
                    duration_ms=(time() - t0) * 1000,
                    fetch_hint=compute_fetch_hint(results_list),
                )
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"Corrupt search cache for '{query[:50]}': {e}")

    # Live local search
    error = ""
    ranked: list[RawResult] = []
    reports: list[EngineReport] = []
    try:
        ranked, reports = await multi_search(
            query, max_results, engines=engines, site=site,
            exclude_sites=exclude_sites, region=region,
            freshness=freshness, server=server,
        )
    except Exception as e:
        error = redact_api_key(str(e)[:200])

    engines_used = [r.name for r in reports if r.ok]
    engine_blocked = [r.name for r in reports if r.blocked]
    if not ranked and not error:
        blocked_any = bool(engine_blocked)
        error = (
            "No results from any engine. " +
            ("Engines were rate-limited/CAPTCHA'd; retry in a moment or rephrase. "
             if blocked_any else "Try rephrasing the query.")
        )

    ranked_list, scores, rerank_used, rerank_note = _rank(query, ranked, mode)
    results_list = _build_results(query, ranked_list, scores)
    fetch_hint = compute_fetch_hint(results_list)
    if rerank_note:
        fetch_hint = (fetch_hint + " | " + rerank_note) if fetch_hint else rerank_note

    # Cache successful results (+ engine metadata for cache hits)
    if cache_ttl > 0 and results_list:
        cache_data = json.dumps({
            "results": [r.model_dump() for r in results_list],
            "engines_used": engines_used,
            "engine_blocked": engine_blocked,
            "rerank_mode": rerank_used,
        })
        await set_cached(query, cache_type, [cache_data], 200, None, cache_ttl)

    if fetch_content and results_list:
        return await _research_fetch(
            server, query, results_list, fetch_top, max_content_chars_per,
            cache_ttl, cached=False, t0=t0, error=error,
            engines_used=engines_used, engine_blocked=engine_blocked,
            rerank_mode=rerank_used, fetch_hint=fetch_hint,
        )

    return SearchResponseModel(
        query=query, results=results_list, total_results=len(results_list),
        engines_used=engines_used, engine_blocked=engine_blocked,
        rerank_mode=rerank_used,
        duration_ms=(time() - t0) * 1000, error=error,
        fetch_hint=fetch_hint,
    )


async def _research_fetch(
    server, query: str, results: list[SearchResult],
    fetch_top: int, max_content_chars_per: int, cache_ttl: int,
    cached: bool, t0: float, error: str,
    engines_used: list[str], engine_blocked: list[str],
    rerank_mode: str = "keyword", fetch_hint: str = "",
) -> ResearchResponseModel:
    """Research mode: bulk-fetch the top-N high-relevance results' content."""
    rank = {"high": 3, "med": 2, "low": 1, "": 0}
    ranked = sorted(results, key=lambda r: (rank.get(r.fetch_relevance, 0), -r.relevance_score), reverse=True)
    top = ranked[:fetch_top]

    async def _fetch_one(r: SearchResult) -> ResearchResult:
        try:
            res = await server.smart_fetch(
                url=r.url, cache_ttl=max(cache_ttl, 3600),
                max_content_chars=max_content_chars_per,
            )
            return ResearchResult(
                title=r.title, url=r.url, snippet=r.snippet, source=r.source,
                position=r.position, relevance_score=r.relevance_score,
                fetch_relevance=r.fetch_relevance,
                content=res.content, content_ok=res.content_ok,
                fetched_summary=res.summary, is_truncated=res.is_truncated,
                next_offset=res.next_offset, fetch_error=res.error,
            )
        except Exception as e:
            return ResearchResult(
                title=r.title, url=r.url, snippet=r.snippet, source=r.source,
                position=r.position, relevance_score=r.relevance_score,
                fetch_relevance=r.fetch_relevance,
                content=[f"[Fetch failed: {redact_api_key(str(e)[:160])}]"],
                content_ok=False, fetch_error=redact_api_key(str(e)[:200]),
            )

    fetched = await asyncio.gather(*[_fetch_one(r) for r in top])
    ok = sum(1 for r in fetched if r.content_ok)
    summary = (
        f"searched {query!r} -> {len(results)} results; fetched {len(fetched)} top "
        f"({ok} content_ok). Paginate any is_truncated result via smart_fetch with offset."
    )
    return ResearchResponseModel(
        query=query, results=fetched, total_results=len(results),
        fetched_count=len(fetched), cached=cached,
        engines_used=engines_used, engine_blocked=engine_blocked,
        rerank_mode=rerank_mode,
        duration_ms=(time() - t0) * 1000, error=error,
        fetch_hint=fetch_hint or compute_fetch_hint(results), summary=summary,
    )
