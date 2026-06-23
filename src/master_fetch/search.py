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
from master_fetch.security import validate_search_query, validate_url, redact_api_key, SecurityError
from master_fetch.search_engines import (
    RawResult, multi_search, EngineReport, DEFAULT_ENGINES, bm25_rerank,
    merge_dedupe, fetch_source_for_similar,
)
from master_fetch.reranker import (
    rerank as neural_rerank, deep_rerank, unavailable_reason, get_reranker,
)

logger = logging.getLogger("master-fetch.search")

SEARCH_CACHE_TTL = 300  # 5 minutes


# ─── response models ─────────────────────────────────────────────────────────

class SearchResult(BaseModel):
    title: str = Field(description="Result title")
    url: str = Field(description="Result URL")
    snippet: str = Field(default="", description="Result snippet from the engine")
    source: str = Field(default="", description="Engine that returned this result (duckduckgo/bing/google/wikipedia)")
    position: int = Field(default=0, description="1-indexed rank after merge + rerank")
    relevance_score: float = Field(default=0.0, description="0.0-1.0 relevance to the query (BM25 over title+snippet, or neural cross-encoder score in neural/deep mode). 1.0 = most relevant in this set.")
    fetch_relevance: str = Field(default="", description="high|med|low - fetch 'high' first (1-2), then 'med' if needed, skip 'low'.")
    peek: str = Field(default="", description="Deep mode only: a short extract of the page's real fetched content (top 3 results) so you can judge relevance before smart_fetching. Empty in keyword/neural modes.")


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
_IMPLEMENTED_MODES = ("auto", "keyword", "neural", "deep", "find_similar")


def _validate_mode(mode):
    if mode is None:
        return "auto"
    if not isinstance(mode, str) or mode.lower() not in _IMPLEMENTED_MODES:
        raise SecurityError(f"Invalid mode: {mode!r} (auto|keyword|neural|deep|find_similar)")
    return mode.lower()


def _validate_expand(expand):
    if expand is None:
        return 1
    if isinstance(expand, bool) or not isinstance(expand, int) or expand < 1 or expand > 5:
        raise SecurityError(f"Invalid expand: {expand!r} (1-5; 1 = no query expansion)")
    return expand


def _expand_query(query: str, n: int) -> list[str]:
    """Local autoretrieval (no external LLM): generate up to n sub-query variants
    by appending intent suffixes / prefixes. Boosts recall for niche queries by
    hitting corners a single phrasing misses. n<=1 returns just the original."""
    if n <= 1 or not query:
        return [query]
    q = query.strip()
    variants = [q]
    suffixes = [" explained", " how to", " tutorial", " example", " guide", " in depth"]
    prefixes = ["what is ", "how does ", "why is ", "understanding "]
    pool = [q + s for s in suffixes] + [p + q for p in prefixes]
    for p in pool:
        if len(variants) >= n:
            break
        if p not in variants:
            variants.append(p)
    return variants[:n]


async def _gather(query: str, expand: int, max_results: int, engines, site,
                  exclude_sites, region, freshness, server):
    """Run multi_search, with autoretrieval (expand>1): run sub-query variants in
    parallel across engines, then merge + dedup + filter. Returns (ranked, reports)."""
    if expand <= 1:
        return await multi_search(query, max_results, engines=engines, site=site,
                                  exclude_sites=exclude_sites, region=region,
                                  freshness=freshness, server=server)
    sub_queries = _expand_query(query, expand)
    subs = await asyncio.gather(*[
        multi_search(sq, max_results, engines=engines, site=site,
                     exclude_sites=exclude_sites, region=region,
                     freshness=freshness, server=server)
        for sq in sub_queries
    ], return_exceptions=True)
    all_results: list[RawResult] = []
    all_reports: list[EngineReport] = []
    for sub in subs:
        if isinstance(sub, BaseException):
            all_reports.append(EngineReport("expand", error=str(sub)[:80]))
            continue
        rs, reps = sub
        all_results.extend(rs)
        all_reports.extend(reps)
    merged = merge_dedupe([(all_results, EngineReport("expanded"))], max_results,
                          site=site, exclude_sites=exclude_sites)
    return merged, all_reports


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


def _build_results(query: str, ranked: list[RawResult], scores: Optional[list[float]] = None,
                   peeks: Optional[dict] = None, peek_top: int = 3) -> list[SearchResult]:
    """Convert RawResults (already ranked) into SearchResults with tiers (+ optional deep peeks)."""
    total = len(ranked)
    out: list[SearchResult] = []
    for i, r in enumerate(ranked):
        score = scores[i] if scores and i < len(scores) else 0.0
        peek = ""
        if peeks and i < peek_top:
            p = peeks.get(r.url, "")
            if p:
                peek = " ".join(p[:200].split())
        out.append(SearchResult(
            title=r.title, url=r.url, snippet=r.snippet, source=r.source,
            position=i + 1, relevance_score=round(score, 4),
            fetch_relevance=_tier(score, i + 1, total), peek=peek,
        ))
    return out


# ─── main entry ───────────────────────────────────────────────────────────────

async def smart_search(
    server,
    query: str,
    max_results: int = 10,
    cache_ttl: int = SEARCH_CACHE_TTL,
    mode: str = "auto",
    expand: int = 1,
    engines: Optional[list[str]] = None,
    url: Optional[str] = None,
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
    """Local keyless web search (no API key, no account). Engines (default
    duckduckgo+bing+wikipedia, add 'google') are scraped in parallel, merged,
    deduped, and ranked. Each result has relevance_score + fetch_relevance so the
    agent smart_fetches the right 1-2. Research mode (fetch_content=True)
    bulk-fetches the reranked top-N.

    mode: auto (neural if [all]+model present else keyword), keyword (BM25),
    neural (cross-encoder on snippets), deep (peek real page content, rerank on
    it; research mode auto-uses deep), find_similar (pass url= ; fetches the
    source page, derives a query, and reranks candidates against the source
    content - Exa find-similar, local). expand (1-5): autoretrieval sub-query
    count (1 = off); boosts recall for niche queries. Not used by find_similar.
    """
    t0 = time()

    try:
        query = validate_search_query(query)
        _validate_filters(site, exclude_sites, location, language, page)
        engines = _validate_engines(engines)
        freshness = _validate_freshness(freshness)
        mode = _validate_mode(mode)
        expand = _validate_expand(expand)
    except Exception as e:
        return SearchResponseModel(
            query=query, results=[], total_results=0,
            duration_ms=0, error=str(e),
        )

    max_results = max(1, min(max_results, 50))
    fetch_top = max(1, min(int(fetch_top) if not isinstance(fetch_top, bool) else 3, 5))
    max_content_chars_per = max(1000, min(int(max_content_chars_per), 50000))
    fetch_content = bool(fetch_content)
    # Research mode auto-enables deep rerank (it already pays the full fetches, so
    # the cheap content peek that powers deep mode is effectively free here).
    if fetch_content and mode == "auto":
        mode = "deep"

    # find_similar: the target is a URL, not a query. Derive it early so the cache
    # key is keyed on the source URL. expand is ignored for find_similar.
    find_sim_url = ""
    if mode == "find_similar":
        cand = (url or "").strip() or (query if query.startswith("http") else "")
        try:
            find_sim_url = validate_url(cand) if cand else ""
        except Exception:
            find_sim_url = ""
        if not find_sim_url:
            return SearchResponseModel(
                query=query, results=[], total_results=0,
                duration_ms=(time() - t0) * 1000,
                error="find_similar requires a url (pass url=, or the URL as query).")

    # region derives from location/language if not given (e.g. "US" -> "us-en").
    if region is None:
        loc = (location or "US").lower()
        lang = (language or "en").lower()
        region = f"{loc}-{lang}" if len(loc) == 2 else "us-en"

    cache_query = find_sim_url or query
    cache_type = (f"search:v2:{max_results}:{site or ''}:{','.join(exclude_sites or [])}:"
                  f"{location or ''}:{language or ''}:{page or 0}:{','.join(engines or [])}:"
                  f"{freshness or ''}:{mode}:{expand}:{cache_query}")
    if cache_ttl > 0:
        cached = await get_cached(cache_query, cache_type, None, ttl=cache_ttl)
        if cached and cached.get("content"):
            try:
                data = json.loads(cached["content"][0])
                results_list = [SearchResult(**r) for r in data.get("results", [])]
                if fetch_content:
                    return await _research_fetch(
                        server, cache_query, results_list, fetch_top, max_content_chars_per,
                        cache_ttl, cached=True, t0=t0, error="",
                        engines_used=data.get("engines_used", []),
                        engine_blocked=data.get("engine_blocked", []),
                        rerank_mode=data.get("rerank_mode", "keyword"),
                        fetch_hint=compute_fetch_hint(results_list),
                    )
                return SearchResponseModel(
                    query=cache_query, results=results_list,
                    total_results=len(results_list), cached=True,
                    engines_used=data.get("engines_used", []),
                    engine_blocked=data.get("engine_blocked", []),
                    rerank_mode=data.get("rerank_mode", "keyword"),
                    duration_ms=(time() - t0) * 1000,
                    fetch_hint=compute_fetch_hint(results_list),
                )
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"Corrupt search cache for '{cache_query[:50]}': {e}")

    # Live local search
    error = ""
    ranked: list[RawResult] = []
    reports: list[EngineReport] = []
    rerank_used = "keyword"
    peeks: dict[str, str] = {}
    rerank_note = ""

    if mode == "find_similar":
        src_title, src_text = await fetch_source_for_similar(find_sim_url)
        if not src_text:
            return SearchResponseModel(
                query=find_sim_url, results=[], total_results=0,
                duration_ms=(time() - t0) * 1000,
                error="could not fetch the source URL for find_similar (blocked or offline).")
        derived_query = src_title or " ".join(src_text.split()[:8]) or query
        try:
            ranked, reports = await _gather(
                derived_query, 1, max_results, engines, site, exclude_sites,
                region, freshness, server,
            )
        except Exception as e:
            error = redact_api_key(str(e)[:200])
        # Rerank candidates against the SOURCE page content (Exa find-similar,
        # local: the cross-encoder scores (source_content, candidate)).
        rer = get_reranker()
        if rer is not None and ranked:
            docs = [f"{r.title} {r.snippet}" for r in ranked]
            try:
                scores = rer.score(src_text[:2000], docs)
                pairs = sorted(zip(ranked, scores), key=lambda rs: (-rs[1], rs[0].position))
                ranked_list = [r for r, _ in pairs]
                scores = [s for _, s in pairs]
                rerank_used = "find_similar"
            except Exception:
                ranked_list, scores, _, _ = _rank(derived_query, ranked, "keyword")
                rerank_used = "find_similar"
        else:
            ranked_list, scores, _, _ = _rank(derived_query, ranked, "keyword")
            rerank_used = "find_similar"
            if ranked and get_reranker() is None:
                rerank_note = ("find_similar used keyword BM25 (neural unavailable). " +
                               (unavailable_reason() or "install hound-mcp[all]"))
        results_list = _build_results(cache_query, ranked_list, scores, peeks=peeks)
        sim_note = f"find_similar to {find_sim_url} (searched: {derived_query[:60]!r})"
        fetch_hint = compute_fetch_hint(results_list)
        fetch_hint = (fetch_hint + " | " + sim_note) if fetch_hint else sim_note
        if rerank_note:
            fetch_hint = (fetch_hint + " | " + rerank_note) if fetch_hint else rerank_note
    else:
        try:
            ranked, reports = await _gather(
                query, expand, max_results, engines, site, exclude_sites,
                region, freshness, server,
            )
        except Exception as e:
            error = redact_api_key(str(e)[:200])

        if not ranked and not error:
            blocked_any = bool([r for r in reports if r.blocked])
            error = (
                "No results from any engine. " +
                ("Engines were rate-limited/CAPTCHA'd; retry in a moment or rephrase. "
                 if blocked_any else "Try rephrasing the query.")
            )

        if mode == "deep":
            dr = await deep_rerank(query, ranked)
            if dr is not None:
                pairs, peeks = dr
                ranked_list = [r for r, _ in pairs]
                scores = [s for _, s in pairs]
                rerank_used = "deep"
            else:
                ranked_list, scores, rerank_used, rerank_note = _rank(query, ranked, "neural")
                rerank_note = ("deep rerank unavailable - used " + rerank_used + ". " +
                               (unavailable_reason() or "install hound-mcp[all] and retry"))
        else:
            ranked_list, scores, rerank_used, rerank_note = _rank(query, ranked, mode)
        results_list = _build_results(query, ranked_list, scores, peeks=peeks)
        fetch_hint = compute_fetch_hint(results_list)
        if rerank_note:
            fetch_hint = (fetch_hint + " | " + rerank_note) if fetch_hint else rerank_note

    engines_used = list(dict.fromkeys(r.name for r in reports if r.ok))
    engine_blocked = list(dict.fromkeys(r.name for r in reports if r.blocked))

    # Cache successful results (+ engine metadata for cache hits)
    if cache_ttl > 0 and results_list:
        cache_data = json.dumps({
            "results": [r.model_dump() for r in results_list],
            "engines_used": engines_used,
            "engine_blocked": engine_blocked,
            "rerank_mode": rerank_used,
        })
        await set_cached(cache_query, cache_type, [cache_data], 200, None, cache_ttl)

    if fetch_content and results_list:
        return await _research_fetch(
            server, cache_query, results_list, fetch_top, max_content_chars_per,
            cache_ttl, cached=False, t0=t0, error=error,
            engines_used=engines_used, engine_blocked=engine_blocked,
            rerank_mode=rerank_used, fetch_hint=fetch_hint,
        )

    return SearchResponseModel(
        query=cache_query, results=results_list, total_results=len(results_list),
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
